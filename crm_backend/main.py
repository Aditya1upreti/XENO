import os
from fastapi import FastAPI, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from typing import List

from crm_backend.database import engine, get_session, create_db_and_tables
from crm_backend.models import Customer, Order, Campaign, MessageLog, Opportunity

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