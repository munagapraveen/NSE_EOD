import asyncio
import sys
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Append the project's root directory to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.base import Base
from src.models import Security
from src.services.bse_client import BSEClient
from src.services.sync_manager import SyncManager
from src.services.nse_client import NSEClient

async def run_bse_integration_tests():
    print("=== BSE Ingestion Integration Tests ===")
    
    # 1. Test BSEClient Lookup and Fetch
    print("\n--- Test 1: BSEClient Direct Fetch ---")
    bse_client = BSEClient()
    
    # Sample ISINs:
    # Reliance: INE002A01018
    # TCS: INE467B01029
    # HDFC Bank: INE040A01034
    test_stocks = [
        {"symbol": "RELIANCE", "isin": "INE002A01018"},
        {"symbol": "TCS", "isin": "INE467B01029"},
        {"symbol": "HDFCBANK", "isin": "INE040A01034"}
    ]
    
    scrip_codes = {}
    for stock in test_stocks:
        symbol = stock["symbol"]
        isin = stock["isin"]
        print(f"Looking up scripcode for {symbol} (ISIN: {isin})...")
        scrip = await bse_client.lookup_scripcode_by_isin(isin)
        if scrip:
            print(f"  [OK] Resolved {symbol} to BSE scripcode: {scrip}")
            scrip_codes[symbol] = scrip
        else:
            print(f"  [FAIL] Could not resolve scripcode for {symbol}")
            
    for symbol, scrip in scrip_codes.items():
        print(f"Fetching outstanding shares for {symbol} (BSE scrip: {scrip})...")
        shares, qtr_date = await bse_client.fetch_outstanding_shares(scrip)
        if shares and shares > 0:
            print(f"  [OK] Outstanding Shares for {symbol}: {shares:,} (quarter: {qtr_date})")
        else:
            print(f"  [FAIL] Could not fetch outstanding shares for {symbol}")

    await bse_client.close()

    # 2. Test Integration in SyncManager
    print("\n--- Test 2: SyncManager Integration (In-Memory DB) ---")
    # Setup clean in-memory engine and session
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        # Create test Security records
        securities = []
        for stock in test_stocks:
            sec = Security(
                symbol=stock["symbol"],
                isin=stock["isin"],
                security_type="STOCK",
                is_active=True,
                is_delisted=False,
                issued_shares=None
            )
            session.add(sec)
            securities.append(sec)
        session.commit()
        
        # Instantiate SyncManager (using empty/mock NSEClient since we won't call it)
        nse_client = NSEClient()
        sync_manager = SyncManager(nse_client)
        
        # Execute stage 6 fetching
        print("Running SyncManager._fetch_shares_for_all_stocks...")
        await sync_manager._fetch_shares_for_all_stocks(session, progress_callback=lambda stage, pct, msg: print(f"  [{stage} {pct:.1f}%] {msg}"))
        
        # Verify db updates
        print("\nVerifying database state:")
        session.expire_all()
        for sec in session.query(Security).all():
            if sec.issued_shares:
                print(f"  [PASS] {sec.symbol}: {sec.issued_shares:,} shares populated in DB.")
            else:
                print(f"  [FAIL] {sec.symbol}: issued_shares is still NULL in DB.")
                
        # Clean up nse client
        await nse_client.close()
        
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(run_bse_integration_tests())
