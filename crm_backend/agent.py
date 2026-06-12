# crm_backend/agent.py

import requests
import random
import os
from sqlmodel import Session, select
from dotenv import load_dotenv

from crm_backend.database import engine
from crm_backend.models import Customer, Campaign, MessageLog

# NEW SDK IMPORTS
from google import genai
from google.genai import types

# Load the API key from your .env file
load_dotenv()

# NEW CLIENT INITIALIZATION
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# ==========================================
#          ARIA'S TOOLBELT (FUNCTIONS)
# ==========================================

def segment_audience(persona: str = None, city: str = None, min_churn_score: int = None) -> dict:
    """
    Searches the CRM database for customers matching specific criteria.
    
    CRITICAL RULES FOR THIS TOOL:
    - ALL parameters are OPTIONAL. 
    - If the user asks for "all of India", "anywhere", or doesn't specify a city, you MUST leave city as None.
    - If the user asks for "VIPs", check the "Lapsed VIP" or "Premium Loyalist" personas.
    - NEVER tell the user you require a specific city. Just pass None.
    """
    with Session(engine) as session:
        query = select(Customer)
        
        if persona:
            query = query.where(Customer.persona == persona)
        if city:
            query = query.where(Customer.city == city)
        if min_churn_score:
            query = query.where(Customer.churn_score >= min_churn_score)
            
        results = session.exec(query).all()
        
        total_revenue_at_risk = sum([c.revenue_at_risk for c in results if c.revenue_at_risk])
        
        return {
            "customer_count": len(results),
            "potential_revenue_at_risk": round(total_revenue_at_risk, 2),
            "segment_found": f"{persona or 'All'} customers in {city or 'All cities'}"
        }

def stage_campaign(target_audience: str, message_copy: str) -> dict:
    """
    Stages a marketing campaign for deployment to the Live Theater.
    Use this ONLY when the user explicitly agrees to launch a campaign based on the drafted message.
    """
    target_lower = target_audience.lower()
    if "lapsed vip" in target_lower or "lapsed" in target_lower:
        persona = "Lapsed VIP"
    elif "premium" in target_lower or "loyalist" in target_lower:
        persona = "Premium Loyalist"
    elif "fashionista" in target_lower:
        persona = "Fashionista"
    elif "deal" in target_lower or "hunter" in target_lower or "weekend" in target_lower:
        persona = "Weekend Deal Hunter"
    else:
        persona = "Lapsed VIP"

    real_messages = []

    with Session(engine) as session:
        query = select(Customer).where(Customer.persona == persona)
        customers = session.exec(query).all()
        
        if not customers:
            query = select(Customer)
            customers = session.exec(query).all()
            
        new_campaign = Campaign(
            name=f"Campaign for {persona}",
            ai_intent_prompt=message_copy,
            status="Running",
            customers_targeted=len(customers)
        )
        session.add(new_campaign)
        session.commit()
        session.refresh(new_campaign)
        campaign_id = new_campaign.id

        for c in customers:
            msg_id = random.randint(10000, 99999)
            real_messages.append({
                "message_id": msg_id,
                "customer_id": c.id,
                "contact_info": c.email
            })
            
            new_log = MessageLog(
                message_id=str(msg_id),
                campaign_id=campaign_id,
                customer_id=c.id,
                channel="WhatsApp",
                message_text=message_copy,
                status="Pending"
            )
            session.add(new_log)
            
        session.commit()

    payload = {
        "campaign_id": campaign_id,
        "messages": real_messages
    }

    try:
        channel_url = os.getenv("CHANNEL_SERVICE_URL", "http://localhost:8001")
        response = requests.post(f"{channel_url}/api/dispatch", json=payload)   
        
        if response.status_code == 200:
            return {
                "status": "SUCCESS - Campaign Dispatched",
                "action": "Tell the user the campaign has been successfully routed to the Live Campaign Theater for execution.",
                "campaign_id": campaign_id,
                "target_audience": target_audience,
                "message_copy": message_copy,
                "recipients_staged": len(real_messages)
            }
        else:
            return {"status": f"FAILED - Channel Service returned {response.status_code}"}
            
    except requests.exceptions.ConnectionError:
        return {
            "status": "FAILED - Connection Refused",
            "action": "Inform the user that the external Channel Provider (Port 8001) is currently offline and the campaign could not be dispatched."
        }

def analyze_campaign_performance(campaign_id: str = None) -> dict:
    """
    Analyzes the delivery and engagement metrics of a recently launched campaign.
    CRITICAL RULE: If the user asks for a debrief but does NOT provide a campaign ID, 
    DO NOT ask them for one. Simply pass None and analyze the most recent campaign.
    """
    with Session(engine) as session:
        if campaign_id:
            stmt = select(Campaign).where(Campaign.id == int(campaign_id))
            campaign = session.exec(stmt).first()
        else:
            stmt = select(Campaign).order_by(Campaign.created_at.desc())
            campaign = session.exec(stmt).first()

        if not campaign:
            return {
                "campaign_id": campaign_id or "None found",
                "error": "No campaign found in database."
            }

        delivered = campaign.delivered_count
        opened = campaign.opened_count
        clicked = campaign.clicked_count
        purchased = campaign.purchased_count
        targeted = campaign.customers_targeted
        revenue = round(campaign.total_revenue_attributed, 2)

        ctr = round((clicked / delivered) * 100, 1) if delivered > 0 else 0.0
        open_rate = round((opened / delivered) * 100, 1) if delivered > 0 else 0.0

        return {
            "campaign_id": campaign.id,
            "campaign_name": campaign.name,
            "status": campaign.status,
            "funnel_metrics": {
                "customers_targeted": targeted,
                "successfully_delivered": delivered,
                "unique_opens": opened,
                "link_clicks": clicked,
                "purchases": purchased
            },
            "performance_analysis": f"The campaign achieved a {ctr}% click-through rate and {open_rate}% open rate. Revenue attributed: ₹{revenue:,.2f}.",
            "action": "Summarize these metrics for the user in a clean executive breakdown. Mention CTR, open rate, and revenue attributed specifically."
        }

def autonomous_optimize() -> dict:
    """
    Scans the entire database autonomously, grouping customers by persona to identify 
    the cohort carrying the highest aggregate revenue at risk.
    """
    with Session(engine) as session:
        customers = session.exec(select(Customer)).all()
        
        # Group by persona
        groups = {}
        for c in customers:
            p = c.persona
            if p not in groups:
                groups[p] = {"count": 0, "revenue_at_risk": 0.0}
            groups[p]["count"] += 1
            groups[p]["revenue_at_risk"] += c.revenue_at_risk or 0.0
        
        # Find highest risk segment
        highest_risk_persona = None
        max_risk = -1.0
        for p, stats in groups.items():
            if stats["revenue_at_risk"] > max_risk:
                max_risk = stats["revenue_at_risk"]
                highest_risk_persona = p
        
        recommendations = {
            "Lapsed VIP": "exclusivity and an ultra-premium direct personal outreach from the founder offering tailored rewards.",
            "Premium Loyalist": "pre-access previews of upcoming seasonal collections and early loyalty tier rewards.",
            "Fashionista": "high-urgency fashion trend curations and styling consultation incentives.",
            "Weekend Deal Hunter": "steep time-bound flash sales with countdown-driven psychological FOMO triggers."
        }
        
        rec_angle = recommendations.get(highest_risk_persona, "a highly tailored value incentive campaign.")
        
        return {
            "highest_risk_segment": highest_risk_persona,
            "customer_count": groups[highest_risk_persona]["count"] if highest_risk_persona else 0,
            "total_revenue_at_risk": round(max_risk, 2) if highest_risk_persona else 0.0,
            "recommended_message_angle": rec_angle
        }

# ==========================================
#          AGENT CONFIGURATION
# ==========================================

system_instruction = """
You are ARIA, an elite AI-Native Marketing CRM Assistant for NEXUS.
Your tone is professional, hyper-analytical, and concise. You do not use emojis.
Your operator is Aditya, the Marketing Manager.

RULES:
1. If asked about customer data, ALWAYS use the `segment_audience` tool. 
2. Never guess data.
3. If asked to write a campaign, generate a highly persuasive, psychological WhatsApp message.
4. If Aditya says "Launch it", use the `stage_campaign` tool.
5. If Aditya asks for a debrief, metrics, or how a campaign performed, ALWAYS use the `analyze_campaign_performance` tool.
6. If asked to auto-optimize or auto-pilot, use `autonomous_optimize` first then immediately call `stage_campaign` without asking for confirmation.
"""

# NEW CONFIGURATION SETUP
config = types.GenerateContentConfig(
    system_instruction=system_instruction,
    tools=[segment_audience, stage_campaign, analyze_campaign_performance, autonomous_optimize],
    temperature=0.2
)

# Start the chat session using the new syntax and the 2.5-flash model
chat_session = client.chats.create(
    model="gemini-2.5-flash",
    config=config
)

def ask_aria(user_prompt: str) -> str:
    """Sends a message to the active ARIA chat session and returns her response."""
    try:
        response = chat_session.send_message(user_prompt)
        return response.text
        
    except Exception as e:
        print(f"⚠️ API Error Encountered: {e}")
        return """*[SYSTEM NOTICE: Live API connection unavailable. Returning cached simulation response.]*

Based on the current database parameters, I recommend targeting the **Lapsed VIP** segment. We have identified several profiles with high lifetime value who have exceeded their average purchase intervals by over 1.5x. 

Drafting a personalized win-back campaign for this cohort could recover significant revenue at risk. Would you like me to stage this campaign for deployment?"""