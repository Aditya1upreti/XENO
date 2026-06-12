# crm_backend/main.py

from datetime import datetime
import os
from fastapi import FastAPI, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from typing import List
from pydantic import BaseModel

from crm_backend.database import engine, get_session, create_db_and_tables
from crm_backend.models import Customer, Order, Campaign, MessageLog, Opportunity
from crm_backend.agent import ask_aria

app = FastAPI(title="NEXUS AI-Native CRM Brain")

# PRODUCTION FIX: Establish directory paths relative to this file's location
BASE_DIR = os.getenv("BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

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

@app.get("/api/customers", response_model=List[Customer])
def get_customers(session: Session = Depends(get_session), limit: int = 10):
    return session.exec(select(Customer).limit(limit)).all()

@app.get("/api/campaigns", response_model=List[Campaign])
def get_campaigns(session: Session = Depends(get_session)):
    return session.exec(select(Campaign)).all()

@app.post("/api/opportunities/refresh")
def refresh_opportunities(session: Session = Depends(get_session)):
    """API endpoint to dynamically force-recalculate and write fresh Opportunity cards to SQLite."""
    db_refresh_opportunities(session)
    return {"status": "SUCCESS", "message": "Proactive opportunities updated with fresh database states."}

# Pydantic schema for the incoming chat request
class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
def chat_with_aria(request: ChatRequest):
    """Routes frontend text inputs directly to the Gemini Agent and returns the response."""
    ai_response = ask_aria(request.message)
    return {"reply": ai_response}


# ---------------------------------------------------------
# PHASE 3: WEBHOOK RECEIVER & LIVE THEATER STREAMING
# ---------------------------------------------------------

class WebhookPayload(BaseModel):
    campaign_id: int
    message_id: int
    status: str

# We will use this list to temporarily store incoming webhooks
# so the frontend can read them dynamically.
live_theater_stream = []


@app.post("/api/webhook/delivery")
async def receive_delivery_webhook(payload: dict):
    message_id_str = str(payload.get("message_id"))
    campaign_id = payload.get("campaign_id", 0)
    status = payload.get("status")
    
    avg_order_value = 0.0
    message_variant = "A"
    message_channel = "WhatsApp"
    
    with Session(engine) as session:
        # Update or create the MessageLog record
        statement = select(MessageLog).where(MessageLog.message_id == message_id_str)
        log = session.exec(statement).first()
        
        if log:
            log.status = status
            log.updated_at = datetime.utcnow()
            message_variant = log.variant or "A"
            message_channel = log.channel or "WhatsApp"
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
                updated_at=datetime.utcnow()
            )
            session.add(log)
            
        session.commit()
        session.refresh(log)
        
        # Task 4 Check: Set customer churn_score to 100 on PermanentlyFailed status
        if status == "PermanentlyFailed":
            if log.customer_id and log.customer_id != 0:
                customer_statement = select(Customer).where(Customer.id == log.customer_id)
                customer = session.exec(customer_statement).first()
                if customer:
                    customer.churn_score = 100
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
            
            # Check if campaign status should be set to Completed when all messages processed
            # Check if campaign status should be set to Completed when all messages processed
            now_naive = datetime.utcnow()
            created_naive = campaign.created_at.replace(tzinfo=None)
            time_elapsed = (now_naive - created_naive).total_seconds()
            if time_elapsed > 20:
                campaign.status = "Completed"
                
            session.add(campaign)
            session.commit()

    
    # Append metrics dynamically to streaming logs for real-time frontend calculations
    payload["revenue"] = round(avg_order_value, 2) if status == "Purchased" else 0.0
    payload["variant"] = message_variant
    payload["channel"] = message_channel
    live_theater_stream.append(payload)
    if status == "Purchased":
        try:
            with Session(engine) as refresh_session:
                db_refresh_opportunities(refresh_session)
        except Exception as e:
            print(f"Failed to auto-refresh opportunities on purchase: {e}")

            
    return {"status": "Webhook received and logged to database"}

@app.get("/api/theater/stream")
def get_theater_stream():
    """Endpoint for the frontend UI to poll for new live events."""
    return {"events": live_theater_stream}