# crm_backend/main.py

# FIX #4: added timezone to imports
from datetime import datetime, timezone
import os
from fastapi import FastAPI, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, func
from typing import List
from pydantic import BaseModel
from fastapi import FastAPI, Request
from fastapi.responses import Response

from crm_backend.database import engine, get_session, create_db_and_tables
from crm_backend.models import Customer, Order, Campaign, MessageLog, Opportunity, CampaignMemory
from crm_backend.agent import ask_aria
from crm_backend.seed import seed_database

# PRODUCTION FIX: Establish directory paths relative to this file's location
BASE_DIR = os.getenv("BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# FIX #6: startup lifespan now calls seed_database() so Render cold starts get a populated DB
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    seed_database()
    yield

# Consolidated FastAPI application instantiation with standard single lifespan initialization
app = FastAPI(title="NEXUS AI-Native CRM Brain", lifespan=lifespan)


# Add this right below it:
@app.middleware("http")
async def handle_head_requests(request: Request, call_next):
    if request.method == "HEAD":
        return Response(status_code=200)
    return await call_next(request)

# ==========================================
#      OPPORTUNITY REFRESH HELPER (DRY)
# ==========================================
def db_refresh_opportunities(session: Session):
    """Core logic to recalculate and sync proactive Opportunity cards inside SQLite."""
    # 1. Churn Risk segment (churn_score > 80)
    risk_stmt = select(Customer).where(Customer.churn_score > 80)
    risk_customers = session.exec(risk_stmt).all()
    risk_count = len(risk_customers)
    risk_revenue = sum(c.revenue_at_risk for c in risk_customers if c.revenue_at_risk)
    
    # 2. Weekend Deal Hunter segment count and valuation
    deal_stmt = select(Customer).where(Customer.persona == "Weekend Deal Hunter")
    deal_customers = session.exec(deal_stmt).all()
    deal_count = len(deal_customers)
    deal_revenue = sum(c.lifetime_spend for c in deal_customers if c.lifetime_spend) * 0.25
    if deal_revenue == 0:
        deal_revenue = deal_count * 1200.0
    
    # Sync SQLite rows
    opportunities = session.exec(select(Opportunity)).all()
    for opp in opportunities:
        title_lower = opp.title.lower()
        if "vip" in title_lower or "lapsed" in title_lower or "churn" in title_lower:
            opp.customer_count = risk_count
            opp.potential_revenue = round(risk_revenue, 2)
            session.add(opp)
        elif "hunter" in title_lower or "deal" in title_lower or "discount" in title_lower:
            opp.customer_count = deal_count
            opp.potential_revenue = round(deal_revenue, 2)
            session.add(opp)
    session.commit()

# ==========================================
#          VIEW ROUTER (FRONTEND ENTRY)
# ==========================================
@app.get("/", response_class=HTMLResponse)
def read_root(request: Request, session: Session = Depends(get_session)):
    try:
        db_refresh_opportunities(session)
    except Exception as e:
        print(f"Failed to auto-refresh proactive opportunities on load: {e}")

    opportunities = session.exec(select(Opportunity)).all()
    return templates.TemplateResponse(
        request=request,
        name="index.html", 
        context={"opportunities": opportunities}
    )

# ==========================================
#          CORE API DATA ENDPOINTS
# ==========================================
@app.get("/api/opportunities", response_model=List[Opportunity])
def get_opportunities(session: Session = Depends(get_session)):
    return session.exec(select(Opportunity)).all()

# FIX #18: added sort by churn_score descending so high-risk customers appear first
@app.get("/api/customers", response_model=List[Customer])
def get_customers(session: Session = Depends(get_session), limit: int = 10):
    return session.exec(select(Customer).order_by(Customer.churn_score.desc()).limit(limit)).all()

@app.get("/api/campaigns", response_model=List[Campaign])
def get_campaigns(session: Session = Depends(get_session)):
    return session.exec(select(Campaign)).all()

@app.post("/api/opportunities/refresh")
def refresh_opportunities(session: Session = Depends(get_session)):
    """API endpoint to dynamically force-recalculate and write fresh Opportunity cards to SQLite."""
    db_refresh_opportunities(session)
    return {"status": "SUCCESS", "message": "Proactive opportunities updated with fresh database states."}

@app.get("/api/analysis/why-not")
def get_why_not_analysis(session: Session = Depends(get_session)):
    """Exposes a comparative marketing run across all four shopper personas in SQLite."""
    customers = session.exec(select(Customer)).all()
    all_personas = ["Lapsed VIP", "Weekend Deal Hunter", "Premium Loyalist", "Fashionista"]
    groups = {p: [] for p in all_personas}
    for c in customers:
        if c.persona in groups:
            groups[c.persona].append(c)
            
    results = []
    for p in all_personas:
        cohort = groups[p]
        count = len(cohort)
        risk = sum(c.revenue_at_risk for c in cohort if c.revenue_at_risk)
        
        # Expected ROI calculation logic mirroring crm_backend/agent.py
        avg_conversion = sum(c.conversion_score for c in cohort) / max(1, count)
        avg_engagement = sum(c.engagement_score for c in cohort) / max(1, count)
        roi = round((avg_conversion * 0.12) + (avg_engagement * 0.04), 1)
        
        results.append({
            "persona": p,
            "count": count,
            "revenue_at_risk": round(risk, 2),
            "expected_roi": roi
        })
        
    # Sort descending by Expected ROI %
    results.sort(key=lambda x: x["expected_roi"], reverse=True)
    return {"segments": results}

# FIX: health check endpoint — ping this before demo to confirm DB is seeded and service is warm
@app.get("/api/health")
def health_check(session: Session = Depends(get_session)):
    customer_count = session.exec(select(func.count(Customer.id))).one()
    campaign_count = session.exec(select(func.count(Campaign.id))).one()
    return {
        "status": "healthy",
        "customers": customer_count,
        "campaigns": campaign_count,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

# Pydantic schema for the incoming chat request
class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
def chat_with_aria(request: ChatRequest, session: Session = Depends(get_session)):
    """Routes frontend text inputs directly to the Gemini Agent and returns the response."""
    ai_response = ask_aria(request.message)
    # Return the latest campaign_id so frontend can filter theater events correctly
    latest_campaign = session.exec(select(Campaign).order_by(Campaign.created_at.desc())).first()
    campaign_id = latest_campaign.id if latest_campaign else None
    return {"reply": ai_response, "campaign_id": campaign_id}


# ---------------------------------------------------------
# PHASE 3: WEBHOOK RECEIVER & LIVE THEATER STREAMING
# ---------------------------------------------------------


# FIX #10: changed from async def to def — FastAPI runs sync endpoints in thread pool,
# which is correct for blocking SQLite operations. async def was blocking the event loop.
@app.post("/api/webhook/delivery")
def receive_delivery_webhook(payload: dict):
    status = payload.get("status")
    if not status:
        return {"status": "ignored - no status provided"}
    
    message_id_str = str(payload.get("message_id"))
    campaign_id = payload.get("campaign_id", 0)
    avg_order_value = 0.0
    
    with Session(engine) as session:
        # Update or create the MessageLog record
        statement = select(MessageLog).where(MessageLog.message_id == message_id_str)
        log = session.exec(statement).first()
        
        if log:
            if log.status == status:
                return {"status": "ignored - duplicate webhook"}
            log.status = status
            # FIX #3: use timezone-aware datetime
            log.updated_at = datetime.now(timezone.utc)
            session.add(log)
        else:
            log = MessageLog(
                message_id=message_id_str,
                campaign_id=campaign_id,
                customer_id=0,
                channel="WhatsApp",
                message_text="",
                status=status,
                variant="A",
                # FIX #3: use timezone-aware datetime
                updated_at=datetime.now(timezone.utc)
            )
            session.add(log)
            
        session.commit()
        session.refresh(log)
        
        # Set customer churn_score to 100 on PermanentlyFailed status
        if status == "PermanentlyFailed":
            if log.customer_id and log.customer_id != 0:
                customer_statement = select(Customer).where(Customer.id == log.customer_id)
                customer = session.exec(customer_statement).first()
                if customer:
                    customer.churn_score = 100
                    # FIX #15: also recalculate revenue_at_risk when churn hits 100
                    order_stmt_risk = select(Order).where(Order.customer_id == customer.id)
                    orders_risk = session.exec(order_stmt_risk).all()
                    if orders_risk:
                        avg_order_risk = sum(o.amount for o in orders_risk) / len(orders_risk)
                        customer.revenue_at_risk = round(avg_order_risk * 1.0, 2)
                    session.add(customer)
                    session.commit()
        
        # Update Campaign metrics
        campaign_statement = select(Campaign).where(Campaign.id == campaign_id)
        campaign = session.exec(campaign_statement).first()
        
        if campaign:
            if status == "Delivered":
                campaign.delivered_count += 1
            elif status == "Opened":
                campaign.opened_count += 1
            elif status == "Clicked":
                campaign.clicked_count += 1
            elif status == "Purchased":
                campaign.purchased_count += 1
                
                # Adds customer average order value to total_revenue_attributed
                if log.customer_id and log.customer_id != 0:
                    order_stmt = select(Order).where(Order.customer_id == log.customer_id)
                    orders = session.exec(order_stmt).all()
                    if orders:
                        avg_order_value = sum(o.amount for o in orders) / len(orders)
                    else:
                        customer_stmt = select(Customer).where(Customer.id == log.customer_id)
                        customer = session.exec(customer_stmt).first()
                        if customer and customer.lifetime_spend > 0:
                            avg_order_value = customer.lifetime_spend / 5.0
                        else:
                            avg_order_value = 1500.0
                else:
                    avg_order_value = 1500.0
                
                campaign.total_revenue_attributed += round(avg_order_value, 2)
            
            # CRITICAL LIFE-CYCLE FIX: Count all terminal actions (Delivered + PermanentlyFailed) 
            # by executing a precise state check on MessageLog to prevent infinite loop stalls.
            total_processed_stmt = select(func.count(MessageLog.id)).where(
                MessageLog.campaign_id == campaign.id,
                MessageLog.status != "Pending",
                MessageLog.status != "Sent"
            )
            total_processed = session.exec(total_processed_stmt).one()

            if (total_processed >= campaign.customers_targeted 
                    and campaign.customers_targeted > 0 
                    and campaign.status == "Running"):
                campaign.status = "Completed"
                session.add(campaign)
                session.commit()
                session.refresh(campaign)
                
                # Write cognitive performance to CampaignMemory on completion
                logs_list = session.exec(select(MessageLog).where(MessageLog.campaign_id == campaign.id)).all()
                if logs_list:
                    split_metrics = {
                        "A": {"delivered": 0, "clicked": 0, "opened": 0},
                        "B": {"delivered": 0, "clicked": 0, "opened": 0}
                    }
                    for message in logs_list:
                        v = message.variant or "A"
                        if message.status in ["Delivered", "Opened", "Clicked", "Purchased"]:
                            split_metrics[v]["delivered"] += 1
                        if message.status in ["Opened", "Clicked", "Purchased"]:
                            split_metrics[v]["opened"] += 1
                        if message.status in ["Clicked", "Purchased"]:
                            split_metrics[v]["clicked"] += 1
                            
                    ctr_a = (split_metrics["A"]["clicked"] / max(1, split_metrics["A"]["delivered"])) * 100
                    ctr_b = (split_metrics["B"]["clicked"] / max(1, split_metrics["B"]["delivered"])) * 100
                    
                    winner_variant = "A" if ctr_a > ctr_b else "B" if ctr_b > ctr_a else "A"
                    winner_ctr = max(ctr_a, ctr_b)
                    
                    total_delivered = sum(m["delivered"] for m in split_metrics.values())
                    total_opened = sum(m["opened"] for m in split_metrics.values())
                    overall_open_rate = round((total_opened / max(1, total_delivered)) * 100, 1)
                    
                    known_personas = ["Lapsed VIP", "Premium Loyalist", "Fashionista", "Weekend Deal Hunter"]
                    persona_val = campaign.name.replace("Campaign for ", "")
                    if persona_val not in known_personas:
                        persona_val = "Unknown"
                    if winner_variant == "A":
                        lesson = "urgency and limited-time warnings performed best at capturing click interest."
                    else:
                        lesson = "exclusivity benefits and direct premium rewards statements drove higher conversion scores."
                        
                    mem = CampaignMemory(
                        persona=persona_val,
                        winner_variant=winner_variant,
                        ctr=round(winner_ctr, 1),
                        open_rate=overall_open_rate,
                        lesson_learned=lesson
                    )
                    session.add(mem)
                    session.commit()
            else:
                session.add(campaign)
                session.commit()
        purchased_count_snapshot = campaign.purchased_count if campaign else 0

    # FIX #12: db_refresh_opportunities now uses a FRESH session, not the already-committed one
    if status == "Purchased" and purchased_count_snapshot % 5 == 0:
        try:
            with Session(engine) as fresh_session:
                db_refresh_opportunities(fresh_session)
        except Exception as e:
            print(f"Failed to auto-refresh opportunities on purchase: {e}")

    return {"status": "Webhook received and logged to database"}

@app.get("/api/theater/stream")
def get_theater_stream(session: Session = Depends(get_session)):
    """Endpoint for the frontend UI to poll for live events, backed by SQLite as single source of truth."""
    # Query the latest active campaign
    latest_campaign_stmt = select(Campaign).order_by(Campaign.created_at.desc())
    latest_campaign = session.exec(latest_campaign_stmt).first()
    
    if not latest_campaign:
        return {"events": []}
    
    # Query all non-Pending message log transitions for the latest campaign
    logs_stmt = select(MessageLog).where(
        MessageLog.campaign_id == latest_campaign.id,
        MessageLog.status != "Pending"
    ).order_by(MessageLog.updated_at.asc())
    
    logs = session.exec(logs_stmt).all()
    
    events = []
    for log in logs:
        revenue = 0.0
        if log.status == "Purchased" and log.customer_id:
            order_stmt = select(Order).where(Order.customer_id == log.customer_id)
            orders = session.exec(order_stmt).all()
            if orders:
                revenue = round(sum(o.amount for o in orders) / len(orders), 2)
            else:
                customer_stmt = select(Customer).where(Customer.id == log.customer_id)
                customer = session.exec(customer_stmt).first()
                if customer and customer.lifetime_spend > 0:
                    revenue = round(customer.lifetime_spend / 5.0, 2)
                else:
                    revenue = 1500.0

        events.append({
            "campaign_id": log.campaign_id,
            # FIX #19: return message_id as plain string — no int cast that returns 0 for non-numeric IDs
            "message_id": log.message_id or "unknown",
            "status": log.status,
            "revenue": revenue,
            "variant": log.variant or "A",
            "channel": log.channel or "WhatsApp"
        })
        
    return {"events": events}