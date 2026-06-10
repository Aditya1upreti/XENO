import requests # ADD THIS TO YOUR IMPORTS AT THE TOP
import random # ADD THIS TO YOUR IMPORTS AT THE TOP
import os
from sqlmodel import Session, select
from dotenv import load_dotenv

from crm_backend.database import engine
from crm_backend.models import Customer

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

# ... (keep segment_audience exactly the same)

def stage_campaign(target_audience: str, message_copy: str) -> dict:
    """
    Stages a marketing campaign for deployment to the Live Theater.
    Use this ONLY when the user explicitly agrees to launch a campaign based on the drafted message.
    """
    
    # 1. We mock extracting the exact customer list based on the audience string
    # In a production app, we would query SQLite again, but for this demo, 
    # we generate a highly realistic subset of 20 customers to represent the batch.
    
    mock_messages = []
    for i in range(1, 21): # Simulate 20 target customers
        mock_messages.append({
            "message_id": random.randint(10000, 99999),
            "customer_id": random.randint(100, 500),
            "contact_info": f"+9198{random.randint(10000000, 99999999)}"
        })

    payload = {
        "campaign_id": random.randint(1000, 9999),
        "messages": mock_messages
    }

    # 2. Fire the payload to our Fake Post Office running on Port 8001
    try:
        channel_url = os.getenv("CHANNEL_SERVICE_URL", "http://localhost:8001")
        response = requests.post(f"{channel_url}/api/dispatch", json=payload)   
        
        if response.status_code == 200:
            return {
                "status": "SUCCESS - Campaign Dispatched",
                "action": "Tell the user the campaign has been successfully routed to the Live Campaign Theater for execution.",
                "campaign_id": payload["campaign_id"],
                "target_audience": target_audience,
                "message_copy": message_copy,
                "recipients_staged": len(mock_messages)
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
    Use this tool ONLY when the user asks how a campaign performed, what the open rates are, or requests a debrief.
    CRITICAL RULE: If the user asks for a debrief but does NOT provide a campaign ID, DO NOT ask them for one. Simply pass None and analyze the most recent campaign.
    """
    # We generate highly realistic engagement metrics based on WhatsApp marketing funnels
    sent = 20
    delivered = random.randint(18, 20)
    opened = random.randint(12, 16)
    clicked = random.randint(5, 9)
    
    conversion_rate = round((clicked / delivered) * 100, 1)

    return {
        "campaign_id": campaign_id or "Latest Active Campaign",
        "funnel_metrics": {
            "messages_dispatched": sent,
            "successfully_delivered": delivered,
            "unique_opens": opened,
            "link_clicks": clicked
        },
        "performance_analysis": f"The campaign achieved a {conversion_rate}% click-through rate. Engagement is tracking well above industry benchmarks.",
        "action": "Summarize these metrics for the user in a clean, executive breakdown. Mention the specific click-through rate."
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
"""

# NEW CONFIGURATION SETUP
config = types.GenerateContentConfig(
    system_instruction=system_instruction,
    tools=[segment_audience, stage_campaign,analyze_campaign_performance],
    temperature=0.2 # Lower temperature keeps the AI highly analytical and focused on data
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
