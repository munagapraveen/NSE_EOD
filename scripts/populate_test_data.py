import asyncio
import os
import sys

# Add root folder to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, update
from src.db.engine import SessionLocal
from src.models import Security
from src.services.price_adjuster import adjust_prices_for_security
from src.services.market_cap import calculate_historical_market_cap
from src.services.indicators import calculate_indicators_for_security


async def main():
    print("=" * 60)
    print("POPULATING DB TEST DATA (100 STOCKS)")
    print("=" * 60)
    
    session = SessionLocal()
    try:
        # Get 150 active stocks
        query = (
            select(Security.id, Security.symbol)
            .where(Security.security_type == "STOCK")
            .where(Security.is_active == True)
            .where(Security.is_delisted == False)
            .limit(150)
        )
        stocks = session.execute(query).all()
        print(f"Selected {len(stocks)} active stocks for test population.")
        
        # Populate mock shares and run calculations
        populated_count = 0
        for idx, stock in enumerate(stocks):
            # Update issued_shares to 100M
            session.execute(
                update(Security)
                .where(Security.id == stock.id)
                .values(issued_shares=100_000_000)
            )
            session.commit()
            
            # Run calculations
            try:
                await adjust_prices_for_security(session, stock.id)
                await calculate_historical_market_cap(session, stock.id, 100_000_000)
                await calculate_indicators_for_security(session, stock.id)
                populated_count += 1
                if populated_count % 10 == 0:
                    print(f"  Processed {populated_count} / {len(stocks)} stocks...")
            except Exception as e:
                print(f"  Failed for {stock.symbol}: {e}")
                session.rollback()
                
        print(f"\nSUCCESS: Successfully populated calculation stages for {populated_count} stocks!")
        
    except Exception as e:
        print(f"\nFAILED: Exception occurred: {e}")
    finally:
        session.close()
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
