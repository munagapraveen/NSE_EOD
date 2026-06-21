import asyncio
import sys
import os
from datetime import date, timedelta
from loguru import logger

# Append the project's root directory to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.engine import SessionLocal, engine
from src.services.nse_client import NSEClient
from src.services.index_downloader import IndexDownloader
from src.models import Security, RawPrice, AdjustedPrice, Indicator
from src.services.price_adjuster import adjust_prices_for_security
from src.services.indicators import calculate_indicators_for_security
from config.settings import settings


async def main():
    print("--- Starting Historical Index Backfill (from 2024-01-01) ---")
    
    # Speed up downloads since nsearchives.nseindia.com has no cookie/Akamai protection
    settings.nse_request_delay_seconds = 0.1
    
    # Initialize services
    client = NSEClient()
    session = SessionLocal()
    downloader = IndexDownloader(client)
    
    try:
        start_date = date(2024, 1, 1)
        end_date = date(2026, 6, 15)  # Today
        
        delta = end_date - start_date
        total_days = delta.days + 1
        
        print(f"Total days to process: {total_days}")
        
        # Download index CSVs date-by-date
        for i in range(total_days):
            target_date = start_date + timedelta(days=i)
            # Skip future dates (just in case)
            if target_date > date.today():
                break
                
            # Log progress
            print(f"[{i+1}/{total_days}] Processing index data for {target_date.isoformat()}...", end="", flush=True)
            
            try:
                # download_and_import_date will return count of records imported
                count = await downloader.download_and_import_date(session, target_date)
                if count > 0:
                    print(f" SUCCESS (Imported {count} records)", flush=True)
                else:
                    print(" SKIPPED (No data/Weekend)", flush=True)
            except Exception as e:
                print(f" FAILED: {e}", flush=True)
                # We log warning but continue to other days so we don't abort the entire backfill
                logger.warning(f"Error downloading data for date {target_date.isoformat()}: {e}")
            
            # Periodically commit to avoid holding massive memory transactions
            if i % 10 == 0:
                session.commit()
                
        session.commit()
        print("\n--- Raw Index Price Downloads Completed ---")
        
        # 2. Run post-processing (Price adjustment and SMA calculation) for all Indexes
        print("\nStarting post-processing calculations for all INDEX type securities...")
        indexes = session.query(Security).filter(Security.security_type == "INDEX").all()
        total_indexes = len(indexes)
        print(f"Found {total_indexes} indexes to process.")
        
        for idx, index_sec in enumerate(indexes, 1):
            print(f"[{idx}/{total_indexes}] Processing {index_sec.symbol}...", end="", flush=True)
            try:
                # 1. Price Adjustment (converts raw_prices to adjusted_prices)
                adj_written = await adjust_prices_for_security(session, index_sec.id)
                # 2. SMA Indicator Calculations
                ind_written = await calculate_indicators_for_security(session, index_sec.id)
                print(f" DONE (Adjusted: {adj_written}, Indicators: {ind_written})", flush=True)
            except Exception as e:
                print(f" FAILED: {e}", flush=True)
                logger.error(f"Failed to post-process index {index_sec.symbol}: {e}")
                
            # Commit after each security
            session.commit()
            
        print("\nAll historical index data backfilled and post-processed successfully!")
        
    except Exception as e:
        print(f"\n[FATAL ERROR] Backfill aborted: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()
        await client.close()
        # Dispose engine to close active connections
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
