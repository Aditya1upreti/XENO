# channel_service/main.py

import os
import asyncio
import random
import httpx
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from typing import List

app = FastAPI(title="NEXUS External Channel Provider")

@app.get("/")
def read_root():
    return {"status": "NEXUS Channel Service Provider is Online and listening for dispatches."}

# This is where we will send updates back to your main CRM
CRM_WEBHOOK_URL = os.getenv("CRM_WEBHOOK_URL", "http://localhost:8000/api/webhook/delivery")

class MessagePayload(BaseModel):
    message_id: int
    customer_id: int
    contact_info: str
    variant: str = "A" # Added variant field for A/B testing dispatches

class CampaignPayload(BaseModel):
    campaign_id: int
    messages: List[MessagePayload]

async def process_message_lifecycle(campaign_id: int, message: MessagePayload):
    """Simulates realistic network delays and user interactions with retry logic."""
    async with httpx.AsyncClient() as client:
        
        async def push_status(status: str):
            """Helper function to fire the webhook back to the CRM."""
            try:
                await client.post(CRM_WEBHOOK_URL, json={
                    "campaign_id": campaign_id,
                    "message_id": message.message_id,
                    "status": status,
                    "variant": message.variant
                })
                print(f"[{status}] Webhook fired for Msg {message.message_id}")
            except Exception as e:
                print(f"Webhook failed: Is the CRM running on port 8000? Error: {e}")

        # 1. Sent to Carrier (Immediate delay)
        await asyncio.sleep(random.uniform(0.5, 1.5))
        await push_status("Sent")

        # 2. Delivered to Device (with simulated 20% failure probability)
        success = random.random() >= 0.20
        if not success:
            print(f"Msg {message.message_id} failed delivery. Queueing for retry...")
            # Wait 10 seconds before retrying once
            await asyncio.sleep(10.0)
            
            # Retry attempt with a fresh success check
            retry_success = random.random() >= 0.20
            if not retry_success:
                print(f"Msg {message.message_id} failed retry permanently.")
                await push_status("PermanentlyFailed")
                return
            else:
                print(f"Msg {message.message_id} retry succeeded.")

        # Proceed with normal delivery lifecycle upon success
        await asyncio.sleep(random.uniform(1.0, 3.0))
        await push_status("Delivered")

        # 3. Opened by User (75% probability)
        if random.random() < 0.75:
            await asyncio.sleep(random.uniform(2.0, 5.0))
            await push_status("Opened")

            # 4. Clicked Link (40% probability if opened)
            if random.random() < 0.40:
                await asyncio.sleep(random.uniform(1.0, 4.0))
                await push_status("Clicked")
                
                # 5. Purchased! (25% probability if clicked)
                if random.random() < 0.25:
                    await asyncio.sleep(random.uniform(3.0, 6.0))
                    await push_status("Purchased")

@app.post("/api/dispatch")
async def dispatch_campaign(payload: CampaignPayload, background_tasks: BackgroundTasks):
    """Receives bulk messages from the CRM and processes them in the background."""
    print(f"\n📡 RECEIVED BATCH: Campaign {payload.campaign_id} | {len(payload.messages)} Messages.")
    
    # Send all messages into the background processor so the API responds instantly
    for msg in payload.messages:
        background_tasks.add_task(process_message_lifecycle, payload.campaign_id, msg)
        
    return {"status": "accepted", "message": "Campaign staging in progress."}