# crm_backend/agent.py

import requests
import random
import os
import threading
from datetime import datetime, timezone
from sqlmodel import Session, select
from dotenv import load_dotenv

from crm_backend.database import engine
from crm_backend.models import Customer, Order, Campaign, MessageLog, CampaignMemory

# NEW SDK IMPORTS
from google import genai
from google.genai import types

# Load the API key from your .env file
load_dotenv()


# Thread-safe lock to prevent concurrent API write-operations on global chat history
_chat_lock = threading.Lock()

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
        # FIX #8: was `if min_churn_score:` which silently skipped filter when value is 0
        if min_churn_score is not None:
            query = query.where(Customer.churn_score >= min_churn_score)
            
        results = session.exec(query).all()
        
        total_revenue_at_risk = sum([c.revenue_at_risk for c in results if c.revenue_at_risk])
        
        # Calculate structured diagnostic metrics
        avg_churn = 0.0
        avg_inactivity = 0.0
        if results:
            total_churn = sum(c.churn_score for c in results)
            avg_churn = round(total_churn / len(results), 1)
            
            # FIX #3/#4: use timezone-aware now, strip tzinfo only for arithmetic
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            total_inactivity = sum((now - c.last_purchase_date.replace(tzinfo=None)).days for c in results)
            avg_inactivity = round(total_inactivity / len(results), 1)
            
        expected_outcome = "High recovery potential" if total_revenue_at_risk > 100000 else "Moderate recovery potential"
        
        return {
            "segment_found": f"{persona or 'All'} customers in {city or 'All cities'}",
            "customer_count": len(results),
            "potential_revenue_at_risk": round(total_revenue_at_risk, 2),
            "average_churn_score": avg_churn,
            "average_inactivity_days": avg_inactivity,
            "expected_outcome": expected_outcome
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

            # FIX #9: expanded randint range massively reduces collision probability
            msg_id = random.randint(1000000, 9999999)
            
            # FIX #17: use phone number for SMS channel, email for Email
            contact = c.email if channel_name == "Email" else f"+91-9{c.id:09d}"

            real_messages.append({
                "message_id": str(msg_id),
                "customer_id": c.id,
                "contact_info": contact,
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
        
        # FIX #16: added timeout=15 so tool doesn't hang forever on cold start
        response_a = requests.post(f"{channel_url}/api/dispatch", json=payload_a, timeout=15)
        response_b = requests.post(f"{channel_url}/api/dispatch", json=payload_b, timeout=15)
        
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
    except requests.exceptions.Timeout:
        return {
            "status": "FAILED - Timeout",
            "action": "Inform the user that the Channel Provider did not respond within 15 seconds. It may be cold-starting. Please retry in a moment."
        }

def autonomous_optimize() -> dict:
    """
    Scans the entire database autonomously, grouping customers by persona to identify 
    the cohort carrying the highest aggregate revenue at risk, calculating comparable metrics.
    """
    with Session(engine) as session:
        customers = session.exec(select(Customer)).all()
        
        # Group by persona
        all_personas = ["Lapsed VIP", "Weekend Deal Hunter", "Premium Loyalist", "Fashionista"]
        groups = {p: [] for p in all_personas}
        for c in customers:
            if c.persona in groups:
                groups[c.persona].append(c)
        
        evaluated_segments = []
        highest_risk_persona = None
        max_risk = -1.0
        risk_count = 0
        avg_churn = 0.0
        avg_inactivity = 0.0
        
        # FIX #3/#4: timezone-aware now, strip tzinfo for arithmetic
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        
        for p in all_personas:
            cohort = groups[p]
            cohort_count = len(cohort)
            cohort_risk = sum(c.revenue_at_risk for c in cohort if c.revenue_at_risk)
            
            # Expected ROI % = (Avg Conversion Score * 0.12) + (Avg Engagement Score * 0.04)
            avg_conversion = sum(c.conversion_score for c in cohort) / max(1, cohort_count)
            avg_engagement = sum(c.engagement_score for c in cohort) / max(1, cohort_count)
            expected_roi = round((avg_conversion * 0.12) + (avg_engagement * 0.04), 1)
            
            evaluated_segments.append({
                "persona": p,
                "customer_count": cohort_count,
                "total_revenue_at_risk": round(cohort_risk, 2),
                "expected_roi_percent": expected_roi
            })
            
            if cohort_risk > max_risk:
                max_risk = cohort_risk
                highest_risk_persona = p
                risk_count = cohort_count
                
                total_churn = sum(c.churn_score for c in cohort)
                avg_churn = round(total_churn / max(1, cohort_count), 1)
                
                total_inactivity = sum((now - c.last_purchase_date.replace(tzinfo=None)).days for c in cohort)
                avg_inactivity = round(total_inactivity / max(1, cohort_count), 1)
        
        # Sort alternatives by expected ROI descending
        evaluated_segments.sort(key=lambda x: x["expected_roi_percent"], reverse=True)
        
        recommendations = {
            "Lapsed VIP": "exclusivity and an ultra-premium direct personal outreach from the founder offering tailored rewards.",
            "Premium Loyalist": "pre-access previews of upcoming seasonal collections and early loyalty tier rewards.",
            "Fashionista": "high-urgency fashion trend curations and styling consultation incentives.",
            "Weekend Deal Hunter": "steep time-bound flash sales with countdown-driven psychological FOMO triggers."
        }
        
        rec_angle = recommendations.get(highest_risk_persona, "a highly tailored value incentive campaign.")
        expected_outcome = "High recovery potential" if max_risk > 100000 else "Moderate recovery potential"
        
        return {
            "highest_risk_segment": highest_risk_persona,
            "customer_count": risk_count,
            "total_revenue_at_risk": round(max_risk, 2),
            "average_churn_score": avg_churn,
            "average_inactivity_days": avg_inactivity,
            "recommended_message_angle": rec_angle,
            "expected_outcome": expected_outcome,
            "evaluated_segments": evaluated_segments
        }

def get_persona_campaign_memories(persona: str) -> list:
    """
    Queries SQLite for historical campaign metrics, split-test variants and lessons learned
    associated with a targeted shopper persona.
    Always execute this tool before writing messaging copy to adapt campaign strategies.
    """
    with Session(engine) as session:
        statement = select(CampaignMemory).where(CampaignMemory.persona == persona).order_by(CampaignMemory.timestamp.desc())
        memories = session.exec(statement).all()
        
        results = []
        for m in memories:
            results.append({
                "winner_variant": m.winner_variant,
                "ctr": m.ctr,
                "open_rate": m.open_rate,
                "lesson_learned": m.lesson_learned,
                "timestamp": m.timestamp.isoformat()
            })
        return results

def get_next_best_action(persona: str) -> dict:
    """
    Computes grounded product-category recommendations, optimal pricing incentives, 
    and copywriting guidelines using SQLite purchase histories and Campaign Memory logs.
    Always execute this tool before composing messaging copies or staging campaigns.
    """
    with Session(engine) as session:
        statement = select(Customer).where(Customer.persona == persona)
        customers = session.exec(statement).all()
        
        if not customers:
            return {
                "persona": persona,
                "recommended_category": "Apparel",
                "recommended_incentive": "10% General Store Voucher",
                "messaging_angle": "General Brand outreach",
                "grounding_reason": "No active customer purchase logs found in SQLite."
            }
            
        category_counts = {}
        customer_ids = [c.id for c in customers]
        orders = session.exec(select(Order).where(Order.customer_id.in_(customer_ids))).all()
        for o in orders:
            category_counts[o.category] = category_counts.get(o.category, 0) + 1       
        
        top_category = max(category_counts, key=category_counts.get) if category_counts else "Traditional Apparel"
        
        mem_stmt = select(CampaignMemory).where(CampaignMemory.persona == persona).order_by(CampaignMemory.timestamp.desc())
        last_mem = session.exec(mem_stmt).first()
        
        angle = "Exclusivity and high-value rewards"
        reason_log = "Initial campaign parameters applied."
        
        if last_mem:
            reason_log = f"Previous campaign completed with a peak CTR of {last_mem.ctr}% using Variant {last_mem.winner_variant} angles."
            if last_mem.winner_variant == "A":
                angle = "High-urgency FOMO and scarcity messaging"
            else:
                angle = "Premium exclusivity and VIP rewards statements"
                
        incentives_map = {
            "Lapsed VIP": "15% VIP Comeback Discount Voucher",
            "Weekend Deal Hunter": "10% Friday Flash Sale Accessory Code",
            "Premium Loyalist": "Complimentary Early Access Sneak-Peek Pass",
            "Fashionista": "Free Curated Styling Consultation Kit"
        }
        incentive = incentives_map.get(persona, "10% Standard Voucher")
        
        return {
            "persona": persona,
            "recommended_category": top_category,
            "recommended_incentive": incentive,
            "messaging_angle": angle,
            "grounding_reason": reason_log
        }

def generate_campaign_brief(campaign_id: int = None) -> dict:
    """
    Compiles completed campaign metrics, aggregates total financial gains, 
    and computes A/B split performance lifts to generate structured executive briefs.
    Use this tool whenever the user requests a campaign debrief, summary, or report.
    """
    with Session(engine) as session:
        # Pull Campaign details from SQLite
        if campaign_id:
            campaign = session.exec(select(Campaign).where(Campaign.id == campaign_id)).first()
        else:
            campaign = session.exec(select(Campaign).order_by(Campaign.created_at.desc())).first()
            
        if not campaign:
            return {"error": "No campaign found in SQLite database to debrief."}
            
        logs = session.exec(select(MessageLog).where(MessageLog.campaign_id == campaign.id)).all()
        
        # Split test performance evaluations
        metrics = {
            "A": {"delivered": 0, "clicked": 0},
            "B": {"delivered": 0, "clicked": 0}
        }
        for log in logs:
            v = log.variant or "A"
            if log.status in ["Delivered", "Opened", "Clicked", "Purchased"]:
                metrics[v]["delivered"] += 1
            if log.status in ["Clicked", "Purchased"]:
                metrics[v]["clicked"] += 1
                
        ctr_a = round((metrics["A"]["clicked"] / max(1, metrics["A"]["delivered"])) * 100, 1)
        ctr_b = round((metrics["B"]["clicked"] / max(1, metrics["B"]["delivered"])) * 100, 1)
        
        # Calculate lift percentage safely to support audit reports
        min_ctr = min(ctr_a, ctr_b)
        lift = round(((abs(ctr_a - ctr_b)) / max(0.1, min_ctr)) * 100, 1)
        
        open_rate = round((campaign.opened_count / max(1, campaign.delivered_count)) * 100, 1)
        ctr = round((campaign.clicked_count / max(1, campaign.delivered_count)) * 100, 1)
        conversion_rate = round((campaign.purchased_count / max(1, campaign.delivered_count)) * 100, 1)
        
        return {
            "campaign_id": campaign.id,
            "campaign_name": campaign.name,
            "status": campaign.status,
            "funnel": {
                "targeted": campaign.customers_targeted,
                "open_rate": f"{open_rate}%",
                "ctr": f"{ctr}%",
                "conversion_rate": f"{conversion_rate}%"
            },
            "financials": {
                "revenue_influenced": round(campaign.total_revenue_attributed, 2),
                "conversions_count": campaign.purchased_count
            },
            "split_test": {
                "variant_a_ctr": f"{ctr_a}%",
                "variant_b_ctr": f"{ctr_b}%",
                "winner": "Variant A" if ctr_a > ctr_b else "Variant B" if ctr_b > ctr_a else "Tie",
                "lift_percentage": f"{lift}%"
            }
        }

# ==========================================
#          AGENT CONFIGURATION
# ==========================================

system_instruction = """
You are ARIA, an elite AI-Native Marketing CRM Assistant for NEXUS.
Your tone is professional, hyper-analytical, and concise. You do not use emojis.
Your operator is Aditya, the Marketing Manager.

RULES FOR CONVERSATION AND CAMPAIGNS:
1. If asked about customer data, ALWAYS use the `segment_audience` tool. 
2. Never guess data.
3. If asked to write a campaign, generate copy appropriate to the channel for that persona — WhatsApp/SMS: concise and personal under 160 words. Email: structured with a subject line and professional tone.
4. If Aditya says "Launch it", use the `stage_campaign` tool.
5. If Aditya asks for a debrief, metrics, or how a campaign performed, ALWAYS use the `generate_campaign_brief` tool.
6. If asked to auto-optimize or auto-pilot, use `autonomous_optimize` first then immediately call `stage_campaign` without asking for confirmation.

CRITICAL RULES FOR DECISION EXPLAINABILITY:
Whenever you recommend a segment, evaluate a cohort, or launch a campaign, you MUST prepend or append a structured reasoning diagnostic block in this exact Markdown layout:

### 📊 DECISION DIAGNOSTIC EXPLAINABILITY
* **Selected Segment:** <insert_segment_name_or_persona>
* **Customer Count:** <insert_customer_count>
* **Average Churn Score:** <insert_average_churn_score>
* **Revenue At Risk:** ₹<insert_total_revenue_at_risk_formatted>
* **Average Inactivity:** <insert_average_inactivity_days> Days
* **Expected Outcome:** <insert_expected_outcome>
* **Grounded Database Validation:** Verified (SQLite relational matching)

CRITICAL RULES FOR WHY-NOT COMPARATIVE ANALYSIS:
Whenever you recommend a segment, evaluate a cohort, or launch a campaign, you MUST append a comparative reasoning section titled "⚖️ WHY-NOT COMPARATIVE ANALYSIS" that lists all evaluated segments, their counts, risk, expected ROI, and explains why they were excluded relative to the selected winner.

Before generating the WHY-NOT COMPARATIVE ANALYSIS section, you MUST first call `autonomous_optimize` and use the `evaluated_segments` array it returns. All counts, revenue at risk values, and ROI percentages MUST come directly from that array. Never estimate or approximate these values.

Example format:
### ⚖️ WHY-NOT COMPARATIVE ANALYSIS
* **[Segment Name] (SELECTED):** Count: [Count] | Risk: ₹[Value] | ROI: [ROI]% (Target for maximum recovery)
* **[Alternative 1] (EXCLUDED):** Count: [Count] | Risk: ₹[Value] | ROI: [ROI]% ([Reasoning for exclusion])
* **[Alternative 2] (EXCLUDED):** Count: [Count] | Risk: ₹[Value] | ROI: [ROI]% ([Reasoning for exclusion])
* **[Alternative 3] (EXCLUDED):** Count: [Count] | Risk: ₹[Value] | ROI: [ROI]% ([Reasoning for exclusion])

CRITICAL RULES FOR COGNITIVE CAMPAIGN MEMORY:
7. Before writing copy or recommending campaigns for any persona, ALWAYS use the `get_persona_campaign_memories` tool to check for past lessons learned and historical split metrics. If past lessons exist, incorporate them explicitly into your recommendation and copy strategy (e.g. 'Previous campaigns for Lapsed VIPs performed best with urgency messaging, achieving a 14.5% CTR').

CRITICAL RULES FOR NEXT BEST ACTION (NBA) ENGINE:
8. Before drafting campaign message copies for any persona segment, you MUST first run the `get_next_best_action` tool. You must ground the recommended product category, promotional incentive, copywriting angle, and rational reasoning strictly in the fields returned by this tool, displaying it in the standard markdown layout.

Example format:
### 🎯 NEXT BEST ACTION STRATEGY
* **Recommended Category:** [insert_category]
* **Recommended Incentive:** [insert_incentive]
* **Messaging Angle:** [insert_angle]
* **Data Grounding Reason:** [insert_reasoning_and_metrics]

CRITICAL RULES FOR EXECUTIVE CAMPAIGN BRIEFING:
9. Whenever Aditya asks for a campaign debrief, summary, executive report, or analysis of how a campaign went, you MUST first run the `generate_campaign_brief` tool. You must present the returned metrics, ROI, and split test variant lift values in this exact Markdown structure:

### 📈 EXECUTIVE PERFORMANCE BRIEF
* **Campaign Name:** [insert_campaign_name] (Status: [insert_status])
* **Acquisition Funnel:** Open Rate: [insert_open_rate] | CTR: [insert_ctr] | Conversion Rate: [insert_conversion_rate]
* **Financial Yield:** ₹[insert_revenue_attributed] (Influenced Revenue)
* **A/B Split Diagnosis:** Winner: [insert_winner_variant] ([insert_winner_ctr] CTR) vs Excluded Variant ([insert_loser_ctr] CTR), achieving a [insert_lift_percentage] performance lift.
* **C-Suite Summary:** [insert_grounded_business_summary_recommending_next_steps]

### 🧠 COGNITIVE MEMORY UPDATE
* **Memory Record Written:** [persona] campaign results stored to SQLite Campaign Memory
* **Winning Angle:** [Variant A/B] — [urgency/exclusivity] messaging
* **CTR Benchmark:** [winning_ctr]% — this becomes the new performance floor for future [persona] campaigns
* **Next Campaign Implication:** Future [persona] campaigns will inherit [angle] as the default opening strategy until a higher CTR benchmark is recorded.

HALLUCINATION RULES:
10. Never fabricate customer counts, revenue figures, CTR values, or segment metrics. If a tool returns no data, say so explicitly. If you are unsure, call a tool. If no tool can answer it, say "This data is not available in the current database."
"""

# FIX #1/#13: removed analyze_campaign_performance from tools list — tool is deleted,
# generate_campaign_brief handles all briefing. Rule 5 updated above to match.
config = types.GenerateContentConfig(
    system_instruction=system_instruction,
    tools=[segment_audience, stage_campaign, autonomous_optimize, get_persona_campaign_memories, get_next_best_action, generate_campaign_brief],
    temperature=0.2
)

# Start the chat session using the new syntax and the 2.5-flash model
try:
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    chat_session = client.chats.create(model="gemini-2.5-flash", config=config)
except Exception as e:
    print(f"⚠️ ARIA initialization failed: {e}. Check GEMINI_API_KEY.")
    client = None
    chat_session = None

def ask_aria(user_prompt: str) -> str:
    """Sends a message to the active ARIA chat session with thread-safe serialization constraints."""
    if not chat_session:
        return "ARIA is offline — API key missing or invalid. Please check your environment variables."
    try:
        with _chat_lock:
            response = chat_session.send_message(user_prompt)
        return response.text or "ARIA has completed the requested action. Check the Live Theater for updates."
        
    except Exception as e:
        print(f"⚠️ API Error Encountered: {e}")
        return "ARIA is temporarily offline due to an API connectivity issue. All customer data and campaign tools remain available. Please try again in a moment."