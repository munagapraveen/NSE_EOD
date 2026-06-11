"""
Test: Incomplete sync detection and exception propagation in SyncManager
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
from src.ui.pages.download import get_last_updated_date

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
        session.query(SyncLog).delete()
        session.commit()
        
        stock = Security(symbol="RELIANCE", security_type="STOCK", is_active=True)
        index = Security(symbol="NIFTY_50", security_type="INDEX", is_active=True)
        session.add_all([stock, index])
        session.commit()
        return stock, index

    # 1. TEST CASE A: Index data gap
    print("=" * 60)
    print("TEST 1A: Index gap (Stock exists but Index is missing for a day)")
    print("=" * 60)
    
    stock, index = reset_db_state()
    
    # Stock has prices on Jun 8, Jun 9, Jun 10
    # Index only has prices on Jun 8
    # Gap date should be Jun 9, returning Jun 8
    session.add_all([
        RawPrice(security_id=stock.id, trade_date=date(2026, 6, 8), open=100, high=110, low=95, close=105, volume=1000),
        RawPrice(security_id=stock.id, trade_date=date(2026, 6, 9), open=105, high=115, low=100, close=110, volume=1000),
        RawPrice(security_id=stock.id, trade_date=date(2026, 6, 10), open=110, high=120, low=105, close=115, volume=1000),
        RawPrice(security_id=index.id, trade_date=date(2026, 6, 8), open=10000, high=10100, low=9900, close=10050, volume=1000),
    ])
    session.commit()
    
    with patch("src.ui.pages.download.SessionLocal", return_value=session):
        dt = get_last_updated_date()
        print(f"  Result date: {dt}")
        assert dt == date(2026, 6, 8), f"Expected 2026-06-08, got {dt}"
        print("  [PASS] Index gap correctly resolved to day before first gap.")

    # 2. TEST CASE B: Stock data gap
    print()
    print("=" * 60)
    print("TEST 1B: Stock gap (Index exists but Stock is missing for a day)")
    print("=" * 60)
    
    stock, index = reset_db_state()
    
    # Index has prices on Jun 8, Jun 9, Jun 10
    # Stock only has prices on Jun 8
    # Gap date should be Jun 9, returning Jun 8
    session.add_all([
        RawPrice(security_id=stock.id, trade_date=date(2026, 6, 8), open=100, high=110, low=95, close=105, volume=1000),
        RawPrice(security_id=index.id, trade_date=date(2026, 6, 8), open=10000, high=10100, low=9900, close=10050, volume=1000),
        RawPrice(security_id=index.id, trade_date=date(2026, 6, 9), open=10000, high=10100, low=9900, close=10050, volume=1000),
        RawPrice(security_id=index.id, trade_date=date(2026, 6, 10), open=10000, high=10100, low=9900, close=10050, volume=1000),
    ])
    session.commit()
    
    with patch("src.ui.pages.download.SessionLocal", return_value=session):
        dt = get_last_updated_date()
        print(f"  Result date: {dt}")
        assert dt == date(2026, 6, 8), f"Expected 2026-06-08, got {dt}"
        print("  [PASS] Stock gap correctly resolved to day before first gap.")

    # 3. TEST CASE C: No gaps
    print()
    print("=" * 60)
    print("TEST 1C: No gaps (both Stock and Index updated up to same date)")
    print("=" * 60)
    
    stock, index = reset_db_state()
    
    # Both updated to Jun 10
    session.add_all([
        RawPrice(security_id=stock.id, trade_date=date(2026, 6, 8), open=100, high=110, low=95, close=105, volume=1000),
        RawPrice(security_id=stock.id, trade_date=date(2026, 6, 9), open=105, high=115, low=100, close=110, volume=1000),
        RawPrice(security_id=stock.id, trade_date=date(2026, 6, 10), open=110, high=120, low=105, close=115, volume=1000),
        RawPrice(security_id=index.id, trade_date=date(2026, 6, 8), open=10000, high=10100, low=9900, close=10050, volume=1000),
        RawPrice(security_id=index.id, trade_date=date(2026, 6, 9), open=10000, high=10100, low=9900, close=10050, volume=1000),
        RawPrice(security_id=index.id, trade_date=date(2026, 6, 10), open=10000, high=10100, low=9900, close=10050, volume=1000),
    ])
    session.commit()
    
    with patch("src.ui.pages.download.SessionLocal", return_value=session):
        dt = get_last_updated_date()
        print(f"  Result date: {dt}")
        assert dt == date(2026, 6, 10), f"Expected 2026-06-10, got {dt}"
        print("  [PASS] No gaps resolved to max trade date correctly.")
        
    print()
    print("=" * 60)
    print("TEST 2: Today's index download 404 is ignored (does not fail sync)")
    print("=" * 60)
    
    # Reset DB and mocks
    stock, index = reset_db_state()
    client_mock.download_bhavcopy_csv.reset_mock()
    client_mock.download_index_csv.reset_mock()
    
    # Mock bhavcopy returning successfully (so stock/ETF sync succeeds)
    import pandas as pd
    dummy_bhav = pd.DataFrame({
        "TradDt": ["2026-06-10"],
        "TckrSymb": ["RELIANCE"],
        "ISIN": ["INE002A01018"],
        "SctySrs": ["EQ"],
        "OpnPric": [100.0],
        "HghPric": [110.0],
        "LwPric": [95.0],
        "ClsPric": [105.0],
        "TtlTradgVol": [1000]
    })
    client_mock.download_bhavcopy_csv.return_value = dummy_bhav
    
    # Mock index download returning 404 for today
    res = httpx.Response(status_code=404, request=httpx.Request("GET", "http://test"))
    err = httpx.HTTPStatusError("404 Not Found", request=res.request, response=res)
    client_mock.download_index_csv.side_effect = err
    
    # Mock today's date as 2026-06-10 for test stability
    with patch("src.services.sync_manager.date") as mock_date, \
         patch("src.services.index_downloader.date") as mock_idx_date:
        mock_date.today.return_value = date(2026, 6, 10)
        mock_date.fromordinal = date.fromordinal
        mock_idx_date.today.return_value = date(2026, 6, 10)
        mock_idx_date.fromordinal = date.fromordinal
        
        log = SyncLog(sync_type="FULL_SYNC", sync_date=date(2026, 6, 10), status="STARTED", started_at=datetime.now())
        session.add(log)
        session.commit()
        
        # This should succeed under the new behavior
        await sm.run_sync(
            session=session,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            options={"stocks": True, "etfs": True, "indexes": True}
        )
        print("  [PASS] Today's index 404 was ignored, and sync completed successfully.")

    print()
    print("=" * 60)
    print("TEST 3: Other HTTP errors (e.g. 500) propagate and fail sync")
    print("=" * 60)
    
    # Reset DB and mocks
    stock, index = reset_db_state()
    client_mock.download_bhavcopy_csv.reset_mock()
    client_mock.download_index_csv.reset_mock()
    client_mock.download_bhavcopy_csv.return_value = dummy_bhav
    
    # Mock index download returning 500 (Internal Server Error)
    res_500 = httpx.Response(status_code=500, request=httpx.Request("GET", "http://test"))
    err_500 = httpx.HTTPStatusError("500 Internal Server Error", request=res_500.request, response=res_500)
    client_mock.download_index_csv.side_effect = err_500
    
    with patch("src.services.sync_manager.date") as mock_date, \
         patch("src.services.index_downloader.date") as mock_idx_date:
        mock_date.today.return_value = date(2026, 6, 10)
        mock_date.fromordinal = date.fromordinal
        mock_idx_date.today.return_value = date(2026, 6, 10)
        mock_idx_date.fromordinal = date.fromordinal
        
        log = SyncLog(sync_type="FULL_SYNC", sync_date=date(2026, 6, 10), status="STARTED", started_at=datetime.now())
        session.add(log)
        session.commit()
        
        try:
            await sm.run_sync(
                session=session,
                start_date=date(2026, 6, 10),
                end_date=date(2026, 6, 10),
                options={"stocks": True, "etfs": True, "indexes": True}
            )
            assert False, "Expected HTTPStatusError to be raised!"
        except httpx.HTTPStatusError as e:
            print(f"  Caught expected error: {e}")
            assert e.response.status_code == 500
            print("  [PASS] Correctly failed sync on 500 Internal Server Error.")

if __name__ == "__main__":
    asyncio.run(test_all())
    session.close()
    engine.dispose()
    print("\nALL INCOMPLETE SYNC TESTS PASSED")
