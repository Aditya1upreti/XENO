from sqlmodel import Session, select
from database import engine
from models import Customer, Order, Opportunity

def inspect_data():
    print("\n" + "="*60)
    print("                NEXUS DATABASE SNAPSHOT INSPECTION            ")
    print("="*60)
    
    with Session(engine) as session:
        # 1. Inspect Opportunity Cards
        print("\n💡 [TABLE: OPPORTUNITY]")
        opportunities = session.exec(select(Opportunity)).all()
        for opp in opportunities:
            print(f" ➔ Title:   {opp.title}")
            print(f"   Desc:    {opp.description}")
            print(f"   Metrics: {opp.customer_count} customers | Potential Revenue: ₹{opp.potential_revenue:,.2f}")
            print("-" * 50)
            
        # 2. Inspect a few sample customers and verify matching logic
        print("\n👥 [TABLE: CUSTOMER & ORDERS SNAPSHOT (First 10 Profiles)]")
        customers = session.exec(select(Customer).limit(10)).all()
        
        for c in customers:
            print(f"\n CUSTOMER #{c.id}: {c.name}")
            print(f" ➔ Email:         {c.email}")
            print(f" ➔ City:          {c.city}")
            print(f" ➔ Persona:       {c.persona}")
            print(f" ➔ Timing Matrix: Last purchased {c.last_purchase_date.strftime('%Y-%m-%d')} | Avg Interval: {c.avg_purchase_interval_days} days")
            print(f" ➔ Risk Matrix:   Churn Score: {c.churn_score}/100 | Revenue At Risk: ₹{c.revenue_at_risk:,.2f}")
            print(f" ➔ Total Value:   Lifetime Spend calculated: ₹{c.lifetime_spend:,.2f}")
            
            # Fetch corresponding timeline orders to check structural consistency
            orders = session.exec(select(Order).where(Order.customer_id == c.id)).all()
            print(f" ➔ Historical Purchase Timeline ({len(orders)} orders):")
            for o in orders:
                print(f"    • [{o.purchase_date.strftime('%Y-%m-%d')}] Paid ₹{o.amount:,.2f} for category: {o.category}")
            print("-" * 60)

if __name__ == "__main__":
    inspect_data()