import asyncio
import sys
import os
from datetime import date, timedelta
from sqlalchemy import func

# Append the project's root directory to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.engine import SessionLocal, engine
from src.services.nse_client import NSEClient
from src.services.sync_manager import SyncManager
from src.models import RawPrice, Base
from loguru import logger

async def main():
    # Initialize/update database tables to ensure all new schemas are created
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as db_init_err:
        logger.warning(f"Database schema initialization warning: {db_init_err}")

    # Align database auto-increment sequences with max IDs
    from src.utils.db_utils import align_database_sequences
    try:
        align_database_sequences(engine)
    except Exception as db_align_err:
        logger.warning(f"Database sequence alignment warning: {db_align_err}")

    client = NSEClient()
    session = SessionLocal()
    
    try:
        # Get last updated date for stocks/ETFs and indexes (self-healing)
        from src.models import Security
        
        # Find dates with Stocks/ETFs but no Indexes
        gap_stock = session.query(RawPrice.trade_date).distinct()\
            .join(Security, Security.id == RawPrice.security_id)\
            .filter(Security.security_type.in_(["STOCK", "ETF"]))\
            .filter(Security.is_active == True)\
            .filter(~Security.symbol.like("TEST%"))\
            .filter(~Security.symbol.like("MOCK%"))\
            .filter(~RawPrice.trade_date.in_(
                session.query(RawPrice.trade_date)
                .join(Security, Security.id == RawPrice.security_id)
                .filter(Security.security_type == "INDEX")
            ))\
            .order_by(RawPrice.trade_date)\
            .first()
            
        # Find dates with Indexes but no Stocks/ETFs
        gap_index = session.query(RawPrice.trade_date).distinct()\
            .join(Security, Security.id == RawPrice.security_id)\
            .filter(Security.security_type == "INDEX")\
            .filter(Security.is_active == True)\
            .filter(~Security.symbol.like("TEST%"))\
            .filter(~Security.symbol.like("MOCK%"))\
            .filter(~RawPrice.trade_date.in_(
                session.query(RawPrice.trade_date)
                .join(Security, Security.id == RawPrice.security_id)
                .filter(Security.security_type.in_(["STOCK", "ETF"]))
            ))\
            .order_by(RawPrice.trade_date)\
            .first()
            
        first_gap = None
        if gap_stock and gap_index:
            first_gap = min(gap_stock[0], gap_index[0])
        elif gap_stock:
            first_gap = gap_stock[0]
        elif gap_index:
            first_gap = gap_index[0]
            
        if first_gap:
            max_date = first_gap - timedelta(days=1)
            print(f"Detected incomplete daily sync (gap date: {first_gap}). Resuming sync from gap date to backfill...")
        else:
            # 2. If no gaps, return the max date
            max_stock = session.query(func.max(RawPrice.trade_date))\
                .join(Security, Security.id == RawPrice.security_id)\
                .filter(Security.security_type.in_(["STOCK", "ETF"]))\
                .scalar()
            max_index = session.query(func.max(RawPrice.trade_date))\
                .join(Security, Security.id == RawPrice.security_id)\
                .filter(Security.security_type == "INDEX")\
                .scalar()
            if max_stock and max_index:
                max_date = min(max_stock, max_index)
            else:
                max_date = max_stock or max_index

        if max_date:
            start_date = max_date + timedelta(days=1)
            print(f"Database has historical data up to {max_date}. Resuming sync from {start_date}...")
        else:
            start_date = date(2024, 1, 1)
            print(f"Database is empty. Starting fresh sync from {start_date}...")
            
        end_date = date.today()
        if start_date > end_date:
            print(f"Database is already up to date ({max_date}). No sync needed.")
            return

        options = {
            "stocks": True,
            "etfs": True,
            "indexes": True,
            "corporate_actions": True,
            "market_cap": True,
            "indicators": True
        }
        
        def progress_callback(stage, percentage, msg):
            print(f"[{percentage:5.1f}%] [{stage}] {msg}", flush=True)

        manager = SyncManager(client)
        print(f"Starting sync from {start_date} to {end_date}...")
        summary = await manager.run_sync(
            session=session,
            start_date=start_date,
            end_date=end_date,
            options=options,
            progress_callback=progress_callback
        )
        print("\n--- Sync Summary ---")
        print(f"Status: {summary.get('status')}")
        print(f"Message: {summary.get('message')}")
        print(f"Records Processed: {summary.get('records_processed')}")
        
    except Exception as e:
        print(f"Error executing sync: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if session.is_active:
            session.close()
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
