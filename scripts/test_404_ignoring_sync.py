"""
Test: Calendar date checking and 404-ignoring behavior in SyncManager
"""
import sys
import os
import httpx
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
from datetime import date, timedelta, datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import AsyncMock, MagicMock, patch

from src.models import Base, Security, RawPrice, SyncLog
from src.services.sync_manager import SyncManager

# Setup in-memory DuckDB
engine = create_engine("duckdb:///:memory:", echo=False)
Base.metadata.create_all(bind=engine)
Session = sessionmaker(bind=engine)
session = Session()

# Setup client mock
client_mock = MagicMock()
client_mock.download_bhavcopy_csv = AsyncMock()
client_mock.download_index_csv = AsyncMock()

# Setup SyncManager
sm = SyncManager(client_mock)

async def test_all():
    # Helper to clean and initialize basic securities
    def reset_db_state():
        session.query(RawPrice).delete()
        session.query(Security).delete()
        session.commit()
        
        stock = Security(symbol="RELIANCE", security_type="STOCK", is_active=True)
        index = Security(symbol="NIFTY_50", security_type="INDEX", is_active=True)
        session.add_all([stock, index])
        session.commit()
        return stock, index

    print("=" * 60)
    print("TEST 1: Weekends & Holidays are checked (not skipped)")
    print("=" * 60)
    
    # Reset DB and mocks
    stock, index = reset_db_state()
    client_mock.download_bhavcopy_csv.reset_mock()
    client_mock.download_index_csv.reset_mock()
    
    # Saturday Jan 4, 2025 (Weekend)
    # Since we ignore 404s, mock a 404 response
    res = httpx.Response(status_code=404, request=httpx.Request("GET", "http://test"))
    err = httpx.HTTPStatusError("404 Not Found", request=res.request, response=res)
    client_mock.download_bhavcopy_csv.side_effect = err
    client_mock.download_index_csv.side_effect = err
    
    log = SyncLog(sync_type="FULL_SYNC", sync_date=date(2025, 1, 4), status="STARTED", started_at=datetime.now())
    session.add(log)
    session.commit()
    
    # Run sync for Saturday. It should attempt the download (not skip early)
    # and then succeed because 404 is ignored.
    await sm.run_sync(
        session=session,
        start_date=date(2025, 1, 4),
        end_date=date(2025, 1, 4),
        options={"stocks": True, "indexes": True}
    )
    
    # Assert download was attempted
    client_mock.download_bhavcopy_csv.assert_called_once()
    client_mock.download_index_csv.assert_called_once()
    print("  [PASS] Weekend was checked and download was attempted.")
    print("  [PASS] 404 error was ignored, and sync finished successfully.")

    print()
    print("=" * 60)
    print("TEST 2: Today's missing data (404) does NOT raise error")
    print("=" * 60)
    
    # Reset mocks
    client_mock.download_bhavcopy_csv.reset_mock()
    client_mock.download_index_csv.reset_mock()
    client_mock.download_bhavcopy_csv.side_effect = err
    client_mock.download_index_csv.side_effect = err
    
    today_date = date.today()
    log = SyncLog(sync_type="FULL_SYNC", sync_date=today_date, status="STARTED", started_at=datetime.now())
    session.add(log)
    session.commit()
    
    # Run sync for today's date. Since 404 is ignored, it should not raise ValueError.
    try:
        await sm.run_sync(
            session=session,
            start_date=today_date,
            end_date=today_date,
            options={"stocks": True, "indexes": True}
        )
        print("  [PASS] Sync for today's missing files succeeded without raising ValueError.")
    except ValueError as e:
        assert False, f"Expected 404 to be ignored, but ValueError was raised: {e}"

if __name__ == "__main__":
    asyncio.run(test_all())
    session.close()
    engine.dispose()
    print("\nALL 404-IGNORING TESTS PASSED")
