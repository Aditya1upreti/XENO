from typing import List, Optional
from datetime import datetime, timezone
from sqlmodel import Field, SQLModel, Relationship

# 1. FINAL CUSTOMER TABLE
class Customer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str
    city: str
    
    # Intelligence Fields
    persona: str
    lifetime_spend: float = Field(default=0.0)
    engagement_score: int = Field(default=50) 
    conversion_score: int = Field(default=50) 
    churn_score: int = Field(default=0) # 0 (Safe) to 100 (Lapsed)
    revenue_at_risk: float = Field(default=0.0) # Calculated during seeding
    
    # Timing Metrics
    last_purchase_date: datetime
    avg_purchase_interval_days: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Relationships
    orders: List["Order"] = Relationship(back_populates="customer")
    messages: List["MessageLog"] = Relationship(back_populates="customer")

# 2. ORDER TABLE
class Order(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    amount: float
    category: str 
    purchase_date: datetime

    customer: Optional[Customer] = Relationship(back_populates="orders")

# 3. CAMPAIGN TABLE (Now with funnel tracking)
class Campaign(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    ai_intent_prompt: str
    status: str = Field(default="Running") # Running, Completed
    
    # Funnel Metrics
    customers_targeted: int = Field(default=0)
    delivered_count: int = Field(default=0)
    opened_count: int = Field(default=0)
    clicked_count: int = Field(default=0)
    purchased_count: int = Field(default=0)
    total_revenue_attributed: float = Field(default=0.0)
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    messages: List["MessageLog"] = Relationship(back_populates="campaign")

# 4. MESSAGE LOG TABLE
class MessageLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    message_id: Optional[str] = Field(default=None)  # ADD THIS LINE
    campaign_id: Optional[int] = Field(default=None, foreign_key="campaign.id")
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    channel: str = Field(default="WhatsApp")
    message_text: str
    status: str = Field(default="Pending")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    campaign: Optional[Campaign] = Relationship(back_populates="messages")
    customer: Optional[Customer] = Relationship(back_populates="messages")
# 5. THE SECRET WEAPON: OPPORTUNITY TABLE
class Opportunity(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    description: str
    customer_count: int
    potential_revenue: float
    status: str = Field(default="Active") # Active, Actioned, Ignored
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))