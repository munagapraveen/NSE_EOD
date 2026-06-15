import asyncio
import sys
import os
from datetime import timedelta
from loguru import logger
from sqlalchemy import func

# Append the project's root directory to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.engine import SessionLocal, engine
from src.services.nse_client import NSEClient
from src.services.corporate_actions import CorporateActionsService
from src.services.symbol_changes import SymbolChangesService
from src.services.price_adjuster import adjust_all_prices
from src.services.market_cap import calculate_all_historical_market_caps
from src.services.indicators import calculate_all_indicators
from src.models import RawPrice, Security, Base
from src.utils.backup_utils import create_db_backup

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
        print("--- Starting Historical Post-Processing ---")
        
        # 1. Get date range from DB
        min_date = session.query(func.min(RawPrice.trade_date)).scalar()
        max_date = session.query(func.max(RawPrice.trade_date)).scalar()
        
        if not min_date or not max_date:
            print("Error: No raw prices found in the database. Cannot run post-processing.")
            return
            
        print(f"Detected historical date range in DB: {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
        
        # 2. Database Backup
        print("\nCreating a database backup before starting...")
        # Close session/dispose engine first to release locks
        session.close()
        from src.db.engine import engine
        engine.dispose()
        
        backup_path = create_db_backup()
        print(f"Backup created at: {backup_path}")
        
        # Re-open session
        session = SessionLocal()
        
        # 3. Sync Corporate Actions in 90-day chunks
        print("\n--- Phase 1: Syncing Corporate Actions ---")
        ca_service = CorporateActionsService(client)
        curr = min_date
        while curr <= max_date:
            next_date = min(curr + timedelta(days=90), max_date)
            print(f"Fetching corporate actions from {curr.strftime('%Y-%m-%d')} to {next_date.strftime('%Y-%m-%d')}...")
            try:
                await ca_service.sync_corporate_actions(session, curr, next_date)
            except Exception as e:
                print(f"Warning: Failed to fetch corporate actions for range {curr} to {next_date}: {e}")
            curr = next_date + timedelta(days=1)
            await asyncio.sleep(1.0)
            
        # 4. Sync Symbol Changes
        print("\n--- Phase 2: Syncing Ticker Symbol Changes ---")
        sc_service = SymbolChangesService(client)
        try:
            await sc_service.sync_symbol_changes(session)
        except Exception as e:
            print(f"Warning: Failed to sync symbol changes: {e}")
            
        # 5. Fetch Outstanding Shares for active stocks that don't have them using BSEClient in concurrent batches
        print("\n--- Phase 3: Fetching Outstanding Shares from BSE (Concurrent) ---")
        from src.services.bse_client import BSEClient
        bse_client = BSEClient()
        try:
            missing_shares_stocks = session.query(Security).filter(
                Security.security_type == 'STOCK',
                Security.is_active == True,
                Security.is_delisted == False,
                Security.issued_shares == None
            ).all()
            
            print(f"Found {len(missing_shares_stocks)} active stocks with missing issued_shares.")
            if missing_shares_stocks:
                async def fetch_one(stock):
                    if not stock.isin:
                        return stock, None, None, "No ISIN"
                    try:
                        scrip_code = await bse_client.lookup_scripcode_by_isin(stock.isin)
                        if not scrip_code:
                            return stock, None, None, "Could not resolve BSE scripcode"
                        issued, qtr_date = await bse_client.fetch_outstanding_shares(scrip_code)
                        if issued and issued > 0:
                            return stock, issued, qtr_date, None
                        else:
                            return stock, None, None, "No shares parsed"
                    except Exception as err:
                        return stock, None, None, str(err)

                CHUNK_SIZE = 15
                for idx in range(0, len(missing_shares_stocks), CHUNK_SIZE):
                    chunk = missing_shares_stocks[idx:idx+CHUNK_SIZE]
                    print(f"[{idx+1}-{min(idx+CHUNK_SIZE, len(missing_shares_stocks))}/{len(missing_shares_stocks)}] Fetching batch...", end="", flush=True)
                    
                    tasks = [fetch_one(s) for s in chunk]
                    results = await asyncio.gather(*tasks)
                    
                    success_batch_count = 0
                    fail_messages = []
                    for stock, issued, qtr_date, error in results:
                        if issued:
                            stock.issued_shares = issued
                            success_batch_count += 1
                            if qtr_date:
                                from src.models import HistoricalShare
                                from sqlalchemy import select
                                stmt = select(HistoricalShare).where(
                                    HistoricalShare.security_id == stock.id,
                                    HistoricalShare.quarter_date == qtr_date
                                )
                                existing = session.execute(stmt).scalar()
                                if existing:
                                    existing.issued_shares = issued
                                else:
                                    new_share = HistoricalShare(
                                        security_id=stock.id,
                                        quarter_date=qtr_date,
                                        issued_shares=issued,
                                        source="BSE_QUARTERLY_SHP"
                                    )
                                    session.add(new_share)
                        else:
                            fail_messages.append(f"{stock.symbol}: {error}")
                    
                    session.commit()
                    print(f" Success: {success_batch_count}/{len(chunk)}")
                    if fail_messages:
                        print(f"   Failures: {', '.join(fail_messages[:5])}" + (f" and {len(fail_messages)-5} more" if len(fail_messages) > 5 else ""))
                    await asyncio.sleep(0.5)

                # Retry all failed shares once more
                failed_stocks = [s for s in missing_shares_stocks if s.issued_shares is None]
                if failed_stocks:
                    print(f"\n--- Retrying {len(failed_stocks)} failed OS shares downloads once more ---")
                    for idx in range(0, len(failed_stocks), CHUNK_SIZE):
                        chunk = failed_stocks[idx:idx+CHUNK_SIZE]
                        print(f"[RETRY {idx+1}-{min(idx+CHUNK_SIZE, len(failed_stocks))}/{len(failed_stocks)}] Fetching batch...", end="", flush=True)
                        
                        tasks = [fetch_one(s) for s in chunk]
                        results = await asyncio.gather(*tasks)
                        
                        success_batch_count = 0
                        fail_messages = []
                        for stock, issued, qtr_date, error in results:
                            if issued:
                                stock.issued_shares = issued
                                success_batch_count += 1
                                if qtr_date:
                                    from src.models import HistoricalShare
                                    from sqlalchemy import select
                                    stmt = select(HistoricalShare).where(
                                        HistoricalShare.security_id == stock.id,
                                        HistoricalShare.quarter_date == qtr_date
                                    )
                                    existing = session.execute(stmt).scalar()
                                    if existing:
                                        existing.issued_shares = issued
                                    else:
                                        new_share = HistoricalShare(
                                            security_id=stock.id,
                                            quarter_date=qtr_date,
                                            issued_shares=issued,
                                            source="BSE_QUARTERLY_SHP"
                                        )
                                        session.add(new_share)
                            else:
                                fail_messages.append(f"{stock.symbol}: {error}")
                        
                        session.commit()
                        print(f" Success: {success_batch_count}/{len(chunk)}")
                        if fail_messages:
                            print(f"   Failures: {', '.join(fail_messages[:5])}" + (f" and {len(fail_messages)-5} more" if len(fail_messages) > 5 else ""))
                        await asyncio.sleep(0.5)
        finally:
            await bse_client.close()

        # 5.5 Fetch Historical Quarterly Shares for active stocks
        print("\n--- Phase 3.5: Fetching Historical Outstanding Shares from BSE ---")
        from src.services.historical_shares import sync_all_historical_shares
        try:
            print(f"Syncing historical shares starting from: {min_date.strftime('%Y-%m-%d')}...")
            await sync_all_historical_shares(session, min_date)
        except Exception as e:
            print(f"Warning: Failed to sync historical outstanding shares: {e}")
                
        # 6. Run Price Adjustment
        print("\n--- Phase 4: Running Global Price Adjustments (Splits/Bonuses) ---")
        await adjust_all_prices(session)
        print("Price adjustments complete.")
        
        # 7. Run Historical Market Cap
        print("\n--- Phase 5: Calculating Historical Market Caps ---")
        await calculate_all_historical_market_caps(session)
        print("Market cap calculations complete.")
        
        # 8. Run SMA Indicators
        print("\n--- Phase 6: Calculating Historical SMA Indicators ---")
        await calculate_all_indicators(session)
        print("SMA indicator calculations complete.")
        
        print("\n--- Historical Post-Processing Completed Successfully! ---")
        
    except Exception as e:
        print(f"\nError during historical post-processing: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
