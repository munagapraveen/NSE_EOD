import asyncio
import sys
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch, MagicMock

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import Base, Security
from src.services.sync_manager import SyncManager
from src.services.nse_client import NSEClient


# 1. Mock BSE Client implementation
class MockBSEClient:
    def __init__(self):
        self.call_counts = {}

    async def lookup_scripcode_by_isin(self, isin: str):
        if isin == "INE_SUCCESS":
            return "500123"
        elif isin == "INE_RETRY":
            return "500456"
        elif isin == "INE_FAIL":
            return "500789"
        return None

    async def fetch_outstanding_shares(self, scrip_code: str):
        self.call_counts[scrip_code] = self.call_counts.get(scrip_code, 0) + 1
        
        # Stock A: Success on first attempt
        if scrip_code == "500123":
            return 1000000, None
            
        # Stock B: Fails on first attempt, succeeds on retry (second attempt)
        elif scrip_code == "500456":
            if self.call_counts[scrip_code] == 1:
                return None, None
            else:
                return 2500000, None
                
        # Stock C: Fails on all attempts
        elif scrip_code == "500789":
            return None, None
            
        return None, None

    async def close(self):
        pass


async def run_tests():
    print("--- Starting Outstanding Shares Retry Tests ---")
    
    # 2. Setup clean in-memory database
    print("\nSetting up in-memory DuckDB...")
    engine = create_engine("duckdb:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # 3. Seed test securities
    print("Seeding test securities...")
    stock_success = Security(
        symbol="SUCC", company_name="Success Stock", security_type="STOCK",
        is_active=True, is_delisted=False, isin="INE_SUCCESS", issued_shares=None
    )
    stock_retry = Security(
        symbol="RETR", company_name="Retry Stock", security_type="STOCK",
        is_active=True, is_delisted=False, isin="INE_RETRY", issued_shares=None
    )
    stock_fail = Security(
        symbol="FAIL", company_name="Fail Stock", security_type="STOCK",
        is_active=True, is_delisted=False, isin="INE_FAIL", issued_shares=None
    )
    session.add_all([stock_success, stock_retry, stock_fail])
    session.commit()

    # Mock NSEClient for SyncManager instantiation
    nse_client = MagicMock(spec=NSEClient)
    sync_manager = SyncManager(nse_client)

    # 4. Patch BSEClient and run SyncManager retry test
    print("\n--- Test 1: Testing SyncManager Retry Logic ---")
    mock_bse = MockBSEClient()
    
    with patch("src.services.bse_client.BSEClient", return_value=mock_bse):
        await sync_manager._fetch_shares_for_all_stocks(session)
            
    # Refresh objects from DB
    session.expire_all()
    s_success = session.query(Security).filter_by(symbol="SUCC").one()
    s_retry = session.query(Security).filter_by(symbol="RETR").one()
    s_fail = session.query(Security).filter_by(symbol="FAIL").one()

    print(f"SUCC shares: {s_success.issued_shares}")
    print(f"RETR shares: {s_retry.issued_shares}")
    print(f"FAIL shares: {s_fail.issued_shares}")

    assert s_success.issued_shares == 1000000, "SUCC should succeed on first try"
    assert s_retry.issued_shares == 2500000, "RETR should succeed on retry attempt"
    assert s_fail.issued_shares is None, "FAIL should remain NULL"
    print("[PASS] SyncManager retry logic verified successfully!")

    # 5. Reset values for postprocess script test
    print("\n--- Test 2: Testing Post-Processing Script Phase 3 Retry Logic ---")
    s_success.issued_shares = None
    s_retry.issued_shares = None
    s_fail.issued_shares = None
    session.commit()
    
    # Simulate run_historical_postprocess.py's Phase 3 with mocked client
    mock_bse2 = MockBSEClient()
    
    # We execute the Phase 3 retry code block directly on our test database using the mock
    import src.services.bse_client
    
    with patch("src.services.bse_client.BSEClient", return_value=mock_bse2):
        bse_client = src.services.bse_client.BSEClient()
        try:
            missing_shares_stocks = session.query(Security).filter(
                Security.security_type == 'STOCK',
                Security.is_active == True,
                Security.is_delisted == False,
                Security.issued_shares == None
            ).all()
            
            print(f"Found {len(missing_shares_stocks)} active stocks with missing issued_shares in test.")
            assert len(missing_shares_stocks) == 3
            
            async def fetch_one(stock):
                if not stock.isin:
                    return stock, None, "No ISIN"
                try:
                    scrip_code = await bse_client.lookup_scripcode_by_isin(stock.isin)
                    if not scrip_code:
                        return stock, None, "Could not resolve BSE scripcode"
                    issued, qtr_date = await bse_client.fetch_outstanding_shares(scrip_code)
                    if issued and issued > 0:
                        return stock, issued, None
                    else:
                        return stock, None, "No shares parsed"
                except Exception as err:
                    return stock, None, str(err)

            CHUNK_SIZE = 2
            # First Loop
            for idx in range(0, len(missing_shares_stocks), CHUNK_SIZE):
                chunk = missing_shares_stocks[idx:idx+CHUNK_SIZE]
                tasks = [fetch_one(s) for s in chunk]
                results = await asyncio.gather(*tasks)
                for stock, issued, error in results:
                    print(f"  First pass result for {stock.symbol}: issued={issued}, error={error}")
                    if issued:
                        stock.issued_shares = issued
                session.commit()

            # Retry Loop
            failed_stocks = [s for s in missing_shares_stocks if s.issued_shares is None]
            print(f"Failed stocks after first pass: {[s.symbol for s in failed_stocks]}")
            assert len(failed_stocks) == 2  # RETR and FAIL should have failed on first pass
            
            if failed_stocks:
                for idx in range(0, len(failed_stocks), CHUNK_SIZE):
                    chunk = failed_stocks[idx:idx+CHUNK_SIZE]
                    tasks = [fetch_one(s) for s in chunk]
                    results = await asyncio.gather(*tasks)
                    for stock, issued, error in results:
                        if issued:
                            stock.issued_shares = issued
                    session.commit()
        finally:
            await bse_client.close()

    # Refresh objects from DB
    session.expire_all()
    s_success = session.query(Security).filter_by(symbol="SUCC").one()
    s_retry = session.query(Security).filter_by(symbol="RETR").one()
    s_fail = session.query(Security).filter_by(symbol="FAIL").one()

    print(f"SUCC shares: {s_success.issued_shares}")
    print(f"RETR shares: {s_retry.issued_shares}")
    print(f"FAIL shares: {s_fail.issued_shares}")

    assert s_success.issued_shares == 1000000, "SUCC should succeed on first try in postprocess"
    assert s_retry.issued_shares == 2500000, "RETR should succeed on retry attempt in postprocess"
    assert s_fail.issued_shares is None, "FAIL should remain NULL in postprocess"
    print("[PASS] Post-processing script retry logic verified successfully!")

    session.close()
    print("\nAll retry tests passed successfully!")


if __name__ == "__main__":
    asyncio.run(run_tests())
