import os
from fastapi import FastAPI, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from typing import List

from crm_backend.database import engine, get_session, create_db_and_tables
from crm_backend.models import Customer, Order, Campaign, MessageLog, Opportunity
from pydantic import BaseModel
from crm_backend.agent import ask_aria
app = FastAPI(title="NEXUS AI-Native CRM Brain")

# PRODUCTION FIX: Establish directory paths relative to this file's location
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

# ==========================================
#          VIEW ROUTER (FRONTEND ENTRY)
# ==========================================
@app.get("/", response_class=HTMLResponse)
def read_root(request: Request, session: Session = Depends(get_session)):
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
    # PRODUCTION FIX: Corrected clean SQLModel statement execution syntax
    return session.exec(select(Campaign)).all()
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
async def receive_delivery_webhook(payload: WebhookPayload):
    """
    Receives real-time delivery updates (Sent, Delivered, Opened, Clicked) 
    from the external Channel Provider (Port 8001).
    """
    update = {
        "message_id": payload.message_id,
        "status": payload.status,
        "timestamp": "Just now" # In production, use real datetime
    }
    
    # Add to our live stream list (keeping only the last 50 events to prevent memory bloat)
    live_theater_stream.append(update)
    if len(live_theater_stream) > 50:
        live_theater_stream.pop(0)
        
    print(f"✅ Webhook Received: Msg {payload.message_id} -> {payload.status}")
    
    # In a full production app, we would save this to the `MessageLog` SQLite table here.
    return {"status": "received"}

@app.get("/api/theater/stream")
def get_theater_stream():
    """Endpoint for the frontend UI to poll for new live events."""
    return {"events": live_theater_stream}

