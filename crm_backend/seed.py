import random
from datetime import datetime, timedelta, timezone
from sqlmodel import Session, select
from crm_backend.models import Customer, Order, Opportunity
from crm_backend.database import engine, create_db_and_tables
FIRST_NAMES = ["Aarav", "Vihaan", "Aditya", "Diya", "Ananya", "Sneha", "Karan", "Priya", "Rahul", "Neha", "Rohan", "Shruti", "Kabir", "Meera", "Arjun", "Zara"]
LAST_NAMES = ["Sharma", "Singh", "Patel", "Gupta", "Verma", "Reddy", "Kumar", "Deshmukh", "Joshi", "Kapoor", "Iyer", "Chopra"]
CITIES = ["Delhi", "Mumbai", "Bangalore", "Hyderabad", "Chennai", "Pune", "Jaipur", "Ahmedabad", "Kolkata"]

PERSONAS = {
    "Premium Loyalist": {"interval": (20, 35), "amount": (5000, 12000), "categories": ["Saree", "Kurta"], "engage": (80, 100), "conv": (70, 95)},
    "Weekend Deal Hunter": {"interval": (45, 90), "amount": (1000, 3500), "categories": ["Accessories", "Western Wear"], "engage": (40, 70), "conv": (20, 45)},
    "Fashionista": {"interval": (15, 40), "amount": (3000, 8000), "categories": ["Western Wear", "Accessories"], "engage": (70, 95), "conv": (60, 85)},
    "Lapsed VIP": {"interval": (25, 35), "amount": (6000, 15000), "categories": ["Saree", "Kurta"], "engage": (10, 30), "conv": (10, 20)}
}

def seed_database():
    print("Initializing Database tables...")
    create_db_and_tables()
    
    with Session(engine) as session:
        if session.exec(select(Customer)).first():
            print("Database is already seeded! Delete nexus_crm.db to re-seed.")
            return

        print("Generating 500 hyper-realistic shoppers...")
        current_time = datetime.now(timezone.utc)
        lapsed_vips_count = 0
        lapsed_vips_revenue = 0.0
        
        for _ in range(500):
            persona_name = random.choices(list(PERSONAS.keys()), weights=[0.3, 0.3, 0.25, 0.15], k=1)[0]
            rules = PERSONAS[persona_name]
            avg_interval = random.randint(*rules["interval"])
            
            if persona_name == "Lapsed VIP":
                days_since_last = random.randint(90, 150)
                churn_score = random.randint(85, 100)
            else:
                days_since_last = random.randint(2, avg_interval + 20)
                ratio = days_since_last / avg_interval
                if ratio > 1.5: churn_score = random.randint(75, 90)
                elif ratio > 1.0: churn_score = random.randint(40, 74)
                else: churn_score = random.randint(0, 39)
                    
            last_purchase = current_time - timedelta(days=days_since_last)
            
            f_name = random.choice(FIRST_NAMES)
            l_name = random.choice(LAST_NAMES)
            
            # Create base customer
            customer = Customer(
                name=f"{f_name} {l_name}",
                email=f"{f_name.lower()}.{l_name.lower()}{random.randint(100, 999)}@example.com",
                city=random.choice(CITIES),
                persona=persona_name,
                engagement_score=random.randint(*rules["engage"]),
                conversion_score=random.randint(*rules["conv"]),
                churn_score=churn_score,
                last_purchase_date=last_purchase,
                avg_purchase_interval_days=avg_interval,
                revenue_at_risk=0.0, # Will update after calculating total spend
                lifetime_spend=0.0
            )
            session.add(customer)
            session.commit()
            session.refresh(customer)

            # Generate Orders & Calculate Dynamic Math
            timeline_date = last_purchase
            total_spend = 0.0
            order_count = random.randint(2, 5) if persona_name == "Weekend Deal Hunter" else random.randint(4, 8)
            
            for _ in range(order_count):
                amount = round(random.uniform(*rules["amount"]), 2)
                total_spend += amount
                order = Order(
                    customer_id=customer.id, amount=amount,
                    category=random.choice(rules["categories"]), purchase_date=timeline_date
                )
                session.add(order)
                timeline_date = timeline_date - timedelta(days=random.randint(avg_interval - 5, avg_interval + 5))
                
            # Dynamic Revenue At Risk & Lifetime Spend!
            customer.lifetime_spend = round(total_spend, 2)
            avg_order = total_spend / order_count
            customer.revenue_at_risk = round(avg_order * (churn_score / 100), 2) if churn_score > 60 else 0.0
            session.add(customer)

            if persona_name == "Lapsed VIP" or churn_score > 85:
                lapsed_vips_count += 1
                lapsed_vips_revenue += customer.revenue_at_risk

        # ==========================================
        # DEMO OUTLIERS: THE WHALE & SUPER CHURNER
        # ==========================================
        whale = Customer(
            name="Vikram The Whale", email="vikram.boss999@example.com", city="Mumbai", persona="Premium Loyalist",
            engagement_score=98, conversion_score=99, churn_score=85, 
            last_purchase_date=current_time - timedelta(days=35), avg_purchase_interval_days=18,
            lifetime_spend=285000.00, revenue_at_risk=13500.00
        )
        session.add(whale)
        
        super_churner = Customer(
            name="Neha Super Churned", email="neha.lost123@example.com", city="Delhi", persona="Lapsed VIP",
            engagement_score=5, conversion_score=5, churn_score=100, 
            last_purchase_date=current_time - timedelta(days=145), avg_purchase_interval_days=25,
            lifetime_spend=142000.00, revenue_at_risk=11200.00
        )
        session.add(super_churner)
        session.commit()

        # Generate Opportunity Cards
        print("Analyzing data to generate Proactive Opportunities...")
        session.add(Opportunity(
            title=f"⚠️ {lapsed_vips_count + 2} VIP Customers are likely to lapse",
            description="These high-value customers have exceeded their average purchase interval by over 1.5x.",
            customer_count=lapsed_vips_count + 2,
            potential_revenue=round(lapsed_vips_revenue + 13500 + 11200, 2)
        ))
        session.add(Opportunity(
            title="🎯 Weekend Deal Hunters Ready",
            description="Historical data shows deal hunters engage highest on Friday evenings. Suggesting a 15% accessory flash sale.",
            customer_count=142, potential_revenue=284000.00
        ))
        session.add(Opportunity(
            title="🛒 43 Abandoned Carts Detected",
            description="Customers clicked WhatsApp campaigns but never completed checkout.",
            customer_count=43, potential_revenue=126000.00
        ))
        session.commit()
        print("✅ Successfully seeded intelligent profiles, Outliers, and Opportunity Cards!")

if __name__ == "__main__":
    seed_database()