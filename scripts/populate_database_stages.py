import asyncio
import os
import sys

# Add root folder to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import update, func
from src.db.engine import SessionLocal
from src.models import Security, MarketCap, AdjustedPrice, Indicator
from src.services.price_adjuster import adjust_all_prices
from src.services.market_cap import calculate_all_historical_market_caps
from src.services.indicators import calculate_all_indicators


async def main():
    print("=" * 60)
    print("POPULATING DATABASE CALCULATION STAGES")
    print("=" * 60)
    
    session = SessionLocal()
    try:
        # 1. Populate mock issued shares for all active stocks where it is NULL
        print("Populating mock issued shares (100M) for stocks...")
        stmt = (
            update(Security)
            .where(Security.security_type == "STOCK")
            .where(Security.issued_shares == None)
            .values(issued_shares=100_000_000) # 100 Million shares
        )
        res = session.execute(stmt)
        session.commit()
        print(f"Mock shares updated: {res.rowcount} stocks.")
        
        # 2. Run global price adjustment
        print("\nRunning global price adjustment calculation...")
        total_adj = await adjust_all_prices(session)
        print(f"Adjusted price records written: {total_adj}")
        
        # 3. Run global market cap calculation
        print("\nRunning global historical market cap calculation...")
        total_mcap = await calculate_all_historical_market_caps(session)
        print(f"Market cap records written: {total_mcap}")
        
        # 4. Run global indicators calculation
        print("\nRunning global SMA indicators calculation...")
        total_ind = await calculate_all_indicators(session)
        print(f"Indicator records written: {total_ind}")
        
        print("\nSUCCESS: All database stages have been populated!")
        
    except Exception as e:
        print(f"\nFAILED: Exception occurred: {e}")
        import traceback
        traceback.print_exc()
        session.rollback()
    finally:
        session.close()
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
