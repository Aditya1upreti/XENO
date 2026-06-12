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

def route_channel(persona: str) -> str:
    """
    Determines the optimal communication channel based on customer persona demographics.
    
    Premium Loyalists get Email.
    Weekend Deal Hunters get SMS.
    Lapsed VIPs get WhatsApp.
    Fashionistas get WhatsApp.
    """
    if persona == "Premium Loyalist":
        return "Email"
    elif persona == "Weekend Deal Hunter":
        return "SMS"
    elif persona == "Lapsed VIP":
        return "WhatsApp"
    elif persona == "Fashionista":
        return "WhatsApp"
    return "WhatsApp"

def stage_campaign(target_audience: str, message_copy: str) -> dict:
    """
    Stages a marketing campaign for deployment to the Live Theater using A/B testing and Omni-channel routing.
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
    channel_name = route_channel(persona)

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

        # Generate message variants automatically
        variant_a_copy = f"[⏳ URGENT: Limited Time Offer!] {message_copy}"
        variant_b_copy = f"[👑 VIP Access Benefit] {message_copy}"

        # Split 50/50
        half = len(customers) // 2
        for idx, c in enumerate(customers):
            variant_tag = "A" if idx < half else "B"
            chosen_text = variant_a_copy if variant_tag == "A" else variant_b_copy
            msg_id = random.randint(10000, 99999)
            
            real_messages.append({
                "message_id": msg_id,
                "customer_id": c.id,
                "contact_info": c.email,
                "variant": variant_tag,
                "channel": channel_name
            })
            
            new_log = MessageLog(
                message_id=str(msg_id),
                campaign_id=campaign_id,
                customer_id=c.id,
                channel=channel_name,
                message_text=chosen_text,
                status="Pending",
                variant=variant_tag
            )
            session.add(new_log)
            
        session.commit()

    # Split lists into separate payloads for individual batch dispatches
    real_messages_a = [m for m in real_messages if m["variant"] == "A"]
    real_messages_b = [m for m in real_messages if m["variant"] == "B"]

    payload_a = {
        "campaign_id": campaign_id,
        "messages": real_messages_a
    }
    
    payload_b = {
        "campaign_id": campaign_id,
        "messages": real_messages_b
    }

    try:
        channel_url = os.getenv("CHANNEL_SERVICE_URL", "http://localhost:8001")
        
        # Dispatch Variant A and Variant B payloads separately
        response_a = requests.post(f"{channel_url}/api/dispatch", json=payload_a)   
        response_b = requests.post(f"{channel_url}/api/dispatch", json=payload_b)   
        
        if response_a.status_code == 200 and response_b.status_code == 200:
            return {
                "status": "SUCCESS - Both Batches Dispatched",
                "action": f"Tell the user that both A/B test variations (FOMO vs Exclusivity) have been successfully routed to the Live Campaign Theater via {channel_name}.",
                "campaign_id": campaign_id,
                "target_audience": target_audience,
                "message_copy": message_copy,
                "recipients_staged": len(real_messages)
            }
        else:
            return {"status": "FAILED - One or both batches returned a non-200 response from the Channel Provider."}
            
    except requests.exceptions.ConnectionError:
        return {
            "status": "FAILED - Connection Refused",
            "action": "Inform the user that the external Channel Provider (Port 8001) is currently offline and the campaign could not be dispatched."
        }

def analyze_campaign_performance(campaign_id: str = None) -> dict:
    """
    Analyzes the delivery and engagement metrics of recently launched campaigns grouped by A/B test variant.
    """
    with Session(engine) as session:
        # Resolve campaign reference dynamically
        if campaign_id:
            campaign_statement = select(Campaign).where(Campaign.id == int(campaign_id))
        else:
            campaign_statement = select(Campaign).order_by(Campaign.created_at.desc())
        
        campaign = session.exec(campaign_statement).first()
        
        if campaign:
            logs_statement = select(MessageLog).where(MessageLog.campaign_id == campaign.id)
            logs = session.exec(logs_statement).all()
            
            metrics = {
                "A": {"delivered": 0, "clicked": 0},
                "B": {"delivered": 0, "clicked": 0}
            }
            
            for log in logs:
                v = log.variant or "A"
                if v not in metrics:
                    metrics[v] = {"delivered": 0, "clicked": 0}
                if log.status in ["Delivered", "Opened", "Clicked", "Purchased"]:
                    metrics[v]["delivered"] += 1
                if log.status in ["Clicked", "Purchased"]:
                    metrics[v]["clicked"] += 1
                    
            ctr_a = round((metrics["A"]["clicked"] / max(1, metrics["A"]["delivered"])) * 100, 1)
            ctr_b = round((metrics["B"]["clicked"] / max(1, metrics["B"]["delivered"])) * 100, 1)
            
            winner = "Variant A" if ctr_a > ctr_b else "Variant B" if ctr_b > ctr_a else "Tie"
            reasoning = f"Variant A (FOMO) achieved {ctr_a}% CTR, while Variant B (Exclusivity) achieved {ctr_b}% CTR."
            
            if winner == "Variant A":
                reasoning += " FOMO and immediate urgency proved significantly more effective at driving engagement."
            elif winner == "Variant B":
                reasoning += " Exclusivity and early collection pre-access aligned more effectively with high-value consumer mindsets."
            else:
                reasoning += " Both variations achieved parity across all targeted subscriber sets."

            return {
                "campaign_id": campaign.id,
                "campaign_name": campaign.name,
                "winner": winner,
                "funnel_metrics": {
                    "variant_a": {"delivered": metrics["A"]["delivered"], "clicks": metrics["A"]["clicked"], "ctr": f"{ctr_a}%"},
                    "variant_b": {"delivered": metrics["B"]["delivered"], "clicks": metrics["B"]["clicked"], "ctr": f"{ctr_b}%"}
                },
                "performance_analysis": reasoning,
                "action": "Present this structural split test CTR analysis clearly in a clean winner format."
            }
            
        else:
            return {
                "campaign_id": "None Available",
                "performance_analysis": "No campaigns are currently logged in the local SQLite database to analyze."
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
    tools=[segment_audience, stage_campaign, analyze_campaign_performance, autonomous_optimize, route_channel],
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