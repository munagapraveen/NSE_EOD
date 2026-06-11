import asyncio
import sys
import os
from datetime import date, timedelta
from loguru import logger
from sqlalchemy import func

# Append the project's root directory to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.engine import SessionLocal
from src.services.nse_client import NSEClient
from src.services.corporate_actions import CorporateActionsService
from src.services.symbol_changes import SymbolChangesService
from src.services.price_adjuster import adjust_all_prices
from src.services.market_cap import calculate_all_historical_market_caps
from src.services.indicators import calculate_all_indicators
from src.models import RawPrice, Security
from src.utils.backup_utils import create_db_backup

async def main():
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
            
        # 5. Fetch Outstanding Shares for active stocks that don't have them
        print("\n--- Phase 3: Fetching Outstanding Shares ---")
        missing_shares_stocks = session.query(Security).filter(
            Security.security_type == 'STOCK',
            Security.is_active == True,
            Security.is_delisted == False,
            Security.issued_shares == None
        ).all()
        
        print(f"Found {len(missing_shares_stocks)} active stocks with missing issued_shares.")
        if missing_shares_stocks:
            consecutive_failures = 0
            for idx, stock in enumerate(missing_shares_stocks):
                print(f"[{idx+1}/{len(missing_shares_stocks)}] Fetching shares for {stock.symbol}...", end="", flush=True)
                try:
                    quote = await client.fetch_stock_quote(stock.symbol, retries=0)
                    trade_info = quote.get("marketDeptOrderBook", {}).get("tradeInfo", {})
                    issued_cap = trade_info.get("issuedSize") or quote.get("securityInfo", {}).get("issuedSize")
                    
                    if issued_cap:
                        stock.issued_shares = int(float(issued_cap))
                        session.commit()
                        print(f" Success ({stock.issued_shares:,} shares)")
                        consecutive_failures = 0
                    else:
                        print(" No issuedSize in quote")
                        consecutive_failures = 0
                except Exception as e:
                    print(f" Failed ({e})")
                    consecutive_failures += 1
                    if consecutive_failures >= 5:
                        print("\nAborting outstanding shares fetch due to 5 consecutive failures (likely blocked by NSE).")
                        break
                await asyncio.sleep(1.0)
                
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
