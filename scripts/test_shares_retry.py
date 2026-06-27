"""
test_shares_retry.py — Updated for NSE XBRL-based outstanding shares.

Tests that:
1. sync_historical_shares_for_security correctly upserts XBRL data and updates Security.issued_shares
2. _fetch_shares_for_all_stocks correctly bulk-fetches and updates all stocks
3. Stocks with no XBRL filings remain NULL (graceful failure)
"""

import asyncio
import sys
import os
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Security, HistoricalShare
from src.services.sync_manager import SyncManager
from src.services.nse_client import NSEClient

pytestmark = pytest.mark.asyncio


# ──────────────────────────────────────────────────────────────────────────────
#  Minimal stub XBRL XML builder
# ──────────────────────────────────────────────────────────────────────────────

def _make_xbrl(shares: int, quarter_date: str, ctx: str = "ShareholdingPattern_ContextI") -> bytes:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance">
  <DateOfReport>{quarter_date}</DateOfReport>
  <NumberOfFullyPaidUpEquityShares contextRef="{ctx}">{shares}</NumberOfFullyPaidUpEquityShares>
</xbrl>'''.encode()


# ──────────────────────────────────────────────────────────────────────────────
#  Test setup helpers
# ──────────────────────────────────────────────────────────────────────────────

def _setup_db():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _make_sync_manager():
    nse_client = MagicMock(spec=NSEClient)
    return SyncManager(nse_client), nse_client


# ──────────────────────────────────────────────────────────────────────────────
#  Test 1: sync_historical_shares_for_security — happy path
# ──────────────────────────────────────────────────────────────────────────────

async def test_sync_one_stock_success():
    print("\n--- Test 1: sync_historical_shares_for_security (success) ---")
    from src.services.historical_shares import sync_historical_shares_for_security

    session = _setup_db()
    sec = Security(
        symbol="RELIANCE", company_name="Reliance", security_type="STOCK",
        is_active=True, is_delisted=False, issued_shares=None
    )
    session.add(sec)
    session.commit()

    xbrl_bytes = _make_xbrl(1_500_000_000, "2025-03-31")

    # Mock: fetch_shareholding_filings returns 1 filing; _download_xbrl returns our stub
    mock_filings = [{
        "symbol": "RELIANCE",
        "xbrl_url": "https://nsearchives.nseindia.com/fake/reliance.xml",
        "report_date": date(2025, 3, 31),
        "record_id": "1234",
    }]

    with patch("src.services.historical_shares.fetch_shareholding_filings", return_value=mock_filings), \
         patch("src.services.historical_shares._download_xbrl", return_value=xbrl_bytes):
        nse_client = MagicMock(spec=NSEClient)
        saved = await sync_historical_shares_for_security(
            session, sec.id, "RELIANCE", date(2024, 1, 1), nse_client
        )

    assert saved == 1, f"Expected 1 record saved, got {saved}"

    session.expire_all()
    record = session.query(HistoricalShare).filter_by(security_id=sec.id).first()
    assert record is not None, "HistoricalShare record not created"
    assert record.issued_shares == 1_500_000_000
    assert record.quarter_date == date(2025, 3, 31)
    assert record.source == "NSE_XBRL_SHP"

    print(f"  [PASS] Saved 1 XBRL record: {record.issued_shares:,} shares @ {record.quarter_date}")
    session.close()


# ──────────────────────────────────────────────────────────────────────────────
#  Test 2: sync_historical_shares_for_security — no filings (graceful null)
# ──────────────────────────────────────────────────────────────────────────────

async def test_sync_one_stock_no_filings():
    print("\n--- Test 2: sync_historical_shares_for_security (no filings -> NULL stays) ---")
    from src.services.historical_shares import sync_historical_shares_for_security

    session = _setup_db()
    sec = Security(
        symbol="NEWSTOCK", company_name="New Stock", security_type="STOCK",
        is_active=True, is_delisted=False, issued_shares=None
    )
    session.add(sec)
    session.commit()

    with patch("src.services.historical_shares.fetch_shareholding_filings", return_value=[]):
        nse_client = MagicMock(spec=NSEClient)
        saved = await sync_historical_shares_for_security(
            session, sec.id, "NEWSTOCK", date(2024, 1, 1), nse_client
        )

    assert saved == 0, f"Expected 0 records saved, got {saved}"
    session.expire_all()
    assert sec.issued_shares is None, "issued_shares should remain NULL"
    print("  [PASS] No filings -> issued_shares stays NULL (graceful)")
    session.close()


# ──────────────────────────────────────────────────────────────────────────────
#  Test 3: sync_all_historical_shares — bulk fetch, multiple stocks
# ──────────────────────────────────────────────────────────────────────────────

async def test_bulk_fetch_all_stocks():
    print("\n--- Test 3: sync_all_historical_shares (bulk fetch, 3 stocks) ---")
    from src.services.historical_shares import sync_all_historical_shares

    session = _setup_db()
    stocks = []
    for sym, shares in [("INFY", 420_000_000), ("TCS", 370_000_000), ("WIPRO", 530_000_000)]:
        sec = Security(
            symbol=sym, company_name=sym, security_type="STOCK",
            is_active=True, is_delisted=False, issued_shares=None
        )
        session.add(sec)
        stocks.append((sym, shares))
    session.commit()

    mock_filings = [
        {"symbol": sym, "xbrl_url": f"https://fake/{sym}.xml", "report_date": date(2025, 3, 31), "record_id": str(i)}
        for i, (sym, _) in enumerate(stocks)
    ]

    def _fake_xbrl(sym):
        sh = dict(stocks)[sym]
        return _make_xbrl(sh, "2025-03-31")

    async def _mock_download(url, nse_client):
        sym = url.split("/")[-1].replace(".xml", "")
        return _fake_xbrl(sym)

    with patch("src.services.historical_shares.fetch_shareholding_filings", return_value=mock_filings), \
         patch("src.services.historical_shares._download_xbrl", side_effect=_mock_download):
        nse_client = MagicMock(spec=NSEClient)
        total_saved = await sync_all_historical_shares(
            session, date(2024, 1, 1), nse_client
        )

    assert total_saved == 3, f"Expected 3 records, got {total_saved}"

    for sym, expected_shares in stocks:
        rec = session.query(HistoricalShare).join(Security).filter(Security.symbol == sym).first()
        assert rec is not None, f"No HistoricalShare for {sym}"
        assert rec.issued_shares == expected_shares, f"{sym}: expected {expected_shares}, got {rec.issued_shares}"
        assert rec.source == "NSE_XBRL_SHP"
        print(f"  [PASS] {sym}: {rec.issued_shares:,} shares @ {rec.quarter_date}")

    session.close()


# ──────────────────────────────────────────────────────────────────────────────
#  Test 4: _fetch_shares_for_all_stocks via SyncManager
# ──────────────────────────────────────────────────────────────────────────────

async def test_sync_manager_fetch_all_stocks():
    print("\n--- Test 4: SyncManager._fetch_shares_for_all_stocks (NSE XBRL bulk) ---")

    session = _setup_db()
    for sym, shares in [("HDFC", 760_000_000), ("AXIS", 310_000_000)]:
        sec = Security(
            symbol=sym, company_name=sym, security_type="STOCK",
            is_active=True, is_delisted=False, issued_shares=None
        )
        session.add(sec)
    session.commit()

    mock_filings = [
        {"symbol": "HDFC", "xbrl_url": "https://fake/HDFC.xml", "report_date": date(2025, 3, 31), "record_id": "1"},
        {"symbol": "AXIS", "xbrl_url": "https://fake/AXIS.xml", "report_date": date(2025, 3, 31), "record_id": "2"},
    ]
    xbrl_map = {"HDFC": _make_xbrl(760_000_000, "2025-03-31"), "AXIS": _make_xbrl(310_000_000, "2025-03-31")}

    async def _mock_download(url, nse_client):
        sym = url.split("/")[-1].replace(".xml", "")
        return xbrl_map[sym]

    sync_manager, _ = _make_sync_manager()
    with patch("src.services.historical_shares.fetch_shareholding_filings", return_value=mock_filings), \
         patch("src.services.historical_shares._download_xbrl", side_effect=_mock_download):
        await sync_manager._fetch_shares_for_all_stocks(session, force_refresh=True)

    session.expire_all()
    for sym, expected_shares in [("HDFC", 760_000_000), ("AXIS", 310_000_000)]:
        sec = session.query(Security).filter_by(symbol=sym).one()
        assert sec.issued_shares == expected_shares, f"{sym}: expected {expected_shares}, got {sec.issued_shares}"
        print(f"  [PASS] {sym}.issued_shares = {sec.issued_shares:,}")

    session.close()


# ──────────────────────────────────────────────────────────────────────────────
#  Runner
# ──────────────────────────────────────────────────────────────────────────────

async def run_tests():
    print("=== NSE XBRL Outstanding Shares Tests ===")
    await test_sync_one_stock_success()
    await test_sync_one_stock_no_filings()
    await test_bulk_fetch_all_stocks()
    await test_sync_manager_fetch_all_stocks()
    print("\n=== All tests passed! ===")


if __name__ == "__main__":
    asyncio.run(run_tests())
