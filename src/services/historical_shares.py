"""
NSE XBRL-based outstanding shares fetcher.

Replaces the previous BSE shareholding pattern HTML scraping approach.

Data flow:
  1. Call NSE /api/corporate-share-holdings-master (with or without symbol filter)
     → returns list of XBRL filing URLs per company per quarter
  2. Download each XBRL file (NSE archive, no cookie needed) and parse XML
     → extract NumberOfFullyPaidUpEquityShares from the "total" context row
  3. Upsert into historical_shares table (source = "NSE_XBRL_SHP")
"""

import asyncio
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import HistoricalShare, Security
from src.services.nse_client import NSEClient

# NSE shareholding pattern master endpoint
NSE_SHAREHOLDING_MASTER_URL = (
    "https://www.nseindia.com/api/corporate-share-holdings-master"
)

# XBRL context IDs that represent the "Grand Total" row across filing vintages
_TOTAL_CONTEXTS = {"ShareholdingPattern_ContextI", "ShareholdingPatternI"}

# Max concurrent XBRL downloads to avoid overwhelming NSE archive servers
_XBRL_CONCURRENCY = 5


# ──────────────────────────────────────────────────────────────────────────────
#  XBRL Parsing helpers
# ──────────────────────────────────────────────────────────────────────────────

def _local_name(tag: str) -> str:
    """Strip XML namespace prefix from a tag, e.g. '{http://...}NumberOfShares' → 'NumberOfShares'."""
    return tag.split("}", 1)[-1]


def _parse_int_text(value: Optional[str]) -> Optional[int]:
    """Safely parse a share count string (may contain commas) to int."""
    if value is None:
        return None
    value = value.strip().replace(",", "")
    if not value or value == "-":
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def parse_xbrl_shares(xml_bytes: bytes) -> tuple[Optional[int], Optional[date]]:
    """
    Parse NSE shareholding pattern XBRL XML and extract total shares + report date.

    Returns:
        (shares, quarter_date) — either may be None if not found / parse error.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.debug(f"XBRL XML parse error: {exc}")
        return None, None

    reported_date: Optional[date] = None
    shares_fully_paid: Optional[int] = None
    shares_total: Optional[int] = None

    for element in root.iter():
        tag = _local_name(element.tag)

        if tag == "DateOfReport" and reported_date is None:
            try:
                reported_date = date.fromisoformat(element.text.strip())
            except (ValueError, AttributeError):
                pass
            continue

        context = element.attrib.get("contextRef", "")
        if context not in _TOTAL_CONTEXTS:
            continue

        if tag == "NumberOfFullyPaidUpEquityShares" and shares_fully_paid is None:
            shares_fully_paid = _parse_int_text(element.text)
        elif tag == "NumberOfShares" and shares_total is None:
            shares_total = _parse_int_text(element.text)

    # Prefer fully-paid count; fall back to total shares
    shares = shares_fully_paid or shares_total
    return shares, reported_date


# ──────────────────────────────────────────────────────────────────────────────
#  NSE API helpers
# ──────────────────────────────────────────────────────────────────────────────

async def fetch_shareholding_filings(
    nse_client: NSEClient,
    symbol: Optional[str],
    from_date: date,
    to_date: date,
) -> list[dict]:
    """
    Fetch NSE shareholding pattern filing list from the master endpoint.

    Args:
        nse_client: Authenticated NSEClient instance.
        symbol:     NSE symbol to filter (e.g. "RELIANCE"). Pass None to fetch
                    filings for ALL listed equities in the date range (bulk fetch).
        from_date:  Start of filing report date range.
        to_date:    End of filing report date range.

    Returns:
        List of filing dicts with keys: symbol, xbrl_url, report_date.
    """
    params: dict = {
        "index": "equities",
        "from_date": from_date.strftime("%d-%m-%Y"),
        "to_date": to_date.strftime("%d-%m-%Y"),
    }
    if symbol:
        params["symbol"] = symbol.upper()

    try:
        response = await nse_client._get(
            NSE_SHAREHOLDING_MASTER_URL,
            params=params,
            requires_cookies=True,
            accept_json=True,
            headers={"Referer": "https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern"},
        )
        rows = response.json()
    except Exception as exc:
        target = f"symbol={symbol}" if symbol else "all symbols"
        logger.error(f"Failed to fetch NSE shareholding master [{target}]: {exc}")
        return []

    filings: list[dict] = []
    for row in rows:
        xbrl_url = row.get("xbrl")
        sym = (row.get("symbol") or symbol or "").upper().strip()
        report_date_str = (row.get("date") or "").strip()

        if not xbrl_url or not sym:
            continue

        # Parse report date (format: "31-Mar-2025")
        report_date: Optional[date] = None
        try:
            report_date = datetime.strptime(report_date_str, "%d-%b-%Y").date()
        except (ValueError, AttributeError):
            continue  # Skip filings with unparseable dates

        filings.append({
            "symbol": sym,
            "xbrl_url": xbrl_url,
            "report_date": report_date,
            "record_id": str(row.get("recordId") or ""),
        })

    return filings


async def _download_xbrl(xbrl_url: str, nse_client: NSEClient) -> bytes:
    """
    Download a single XBRL XML file from NSE archives.
    Archive URLs do not require session cookies.
    """
    response = await nse_client._get(
        xbrl_url,
        requires_cookies=False,
        headers={"Accept": "application/xml,text/xml,*/*"},
    )
    return response.content


async def _fetch_and_parse_xbrl(
    filing: dict,
    nse_client: NSEClient,
    semaphore: asyncio.Semaphore,
) -> tuple[str, Optional[int], Optional[date]]:
    """
    Download and parse one XBRL filing.

    Returns:
        (symbol, shares, quarter_date) — shares/quarter_date may be None on failure.
    """
    async with semaphore:
        try:
            xml_bytes = await _download_xbrl(filing["xbrl_url"], nse_client)
            shares, quarter_date = parse_xbrl_shares(xml_bytes)
            return filing["symbol"], shares, quarter_date
        except Exception as exc:
            logger.debug(
                f"XBRL fetch/parse failed for {filing['symbol']} "
                f"(record {filing['record_id']}): {exc}"
            )
            return filing["symbol"], None, None


# ──────────────────────────────────────────────────────────────────────────────
#  Public sync functions
# ──────────────────────────────────────────────────────────────────────────────

async def sync_historical_shares_for_security(
    session: Session,
    security_id: int,
    symbol: str,
    start_date: date,
    nse_client: NSEClient,
) -> int:
    """
    Fetch and sync historical quarterly shares for a specific stock via NSE XBRL.

    Args:
        session:     SQLAlchemy DB session.
        security_id: Primary key of the Security record.
        symbol:      NSE stock symbol (e.g. "RELIANCE").
        start_date:  Earliest quarter date to fetch (inclusive).
        nse_client:  Authenticated NSEClient instance.

    Returns:
        Number of HistoricalShare records upserted.
    """
    filings = await fetch_shareholding_filings(
        nse_client, symbol, start_date, date.today()
    )
    if not filings:
        logger.debug(f"No NSE shareholding filings found for {symbol} from {start_date}")
        return 0

    logger.debug(
        f"Parsing {len(filings)} NSE XBRL filings for {symbol} "
        f"(security ID {security_id})..."
    )

    semaphore = asyncio.Semaphore(_XBRL_CONCURRENCY)
    tasks = [_fetch_and_parse_xbrl(f, nse_client, semaphore) for f in filings]
    results = await asyncio.gather(*tasks)

    seen_dates: set[date] = set()
    records_saved = 0

    for _sym, shares, quarter_date in results:
        if not shares or not quarter_date:
            continue
        if quarter_date in seen_dates:
            continue
        seen_dates.add(quarter_date)

        existing = session.execute(
            select(HistoricalShare).where(
                HistoricalShare.security_id == security_id,
                HistoricalShare.quarter_date == quarter_date,
            )
        ).scalar_one_or_none()

        if existing:
            existing.issued_shares = shares
            existing.source = "NSE_XBRL_SHP"
        else:
            session.add(
                HistoricalShare(
                    security_id=security_id,
                    quarter_date=quarter_date,
                    issued_shares=shares,
                    source="NSE_XBRL_SHP",
                )
            )
        records_saved += 1

    if records_saved > 0:
        session.commit()
        logger.debug(
            f"Saved {records_saved} quarterly share records for {symbol} "
            f"(security ID {security_id}, source: NSE_XBRL_SHP)."
        )

    return records_saved


async def sync_all_historical_shares(
    session: Session,
    start_date: date,
    nse_client: NSEClient,
    progress_callback=None,
) -> int:
    """
    Bulk fetch all NSE shareholding filings for all listed equities in one API call,
    parse each XBRL file, and upsert into the historical_shares table.

    Args:
        session:           SQLAlchemy DB session.
        start_date:        Earliest quarter date to fetch (inclusive).
        nse_client:        Authenticated NSEClient instance.
        progress_callback: Optional callable(pct: float) for progress updates.

    Returns:
        Total number of HistoricalShare records upserted.
    """
    today = date.today()
    logger.info(
        f"Fetching all NSE shareholding filings from {start_date} to {today} "
        f"(single bulk API call)..."
    )

    # Step 1: One API call for ALL listed equities in the date range
    all_filings = await fetch_shareholding_filings(
        nse_client, None, start_date, today
    )
    if not all_filings:
        logger.warning("NSE shareholding master returned no filings.")
        return 0

    logger.info(
        f"Got {len(all_filings)} NSE shareholding filings. "
        f"Fetching and parsing XBRL files..."
    )

    # Step 2: Build symbol → security_id map for tracked stocks
    tracked = session.execute(
        select(Security.id, Security.symbol)
        .where(Security.security_type == "STOCK")
        .where(Security.is_active == True)
        .where(Security.is_delisted == False)
    ).all()
    symbol_to_id: dict[str, int] = {row.symbol: row.id for row in tracked}

    # Step 3: Filter filings to only tracked symbols
    relevant_filings = [
        f for f in all_filings if f["symbol"] in symbol_to_id
    ]
    logger.info(
        f"Matched {len(relevant_filings)} filings across "
        f"{len({f['symbol'] for f in relevant_filings})} tracked symbols."
    )

    if not relevant_filings:
        return 0

    # Step 4: Download + parse all XBRL files concurrently (bounded semaphore)
    semaphore = asyncio.Semaphore(_XBRL_CONCURRENCY)
    tasks = [_fetch_and_parse_xbrl(f, nse_client, semaphore) for f in relevant_filings]
    results = await asyncio.gather(*tasks)

    # Step 5: Group results by symbol and upsert into historical_shares
    # Track best (latest) share count per (security_id, quarter_date)
    records_map: dict[tuple[int, date], int] = {}

    for (sym, shares, quarter_date), filing in zip(results, relevant_filings):
        if not shares or not quarter_date:
            continue
        sec_id = symbol_to_id.get(sym)
        if not sec_id:
            continue
        key = (sec_id, quarter_date)
        # Last writer wins (filings are sorted oldest→newest by the API)
        records_map[key] = shares

    if not records_map:
        logger.warning("No valid share counts extracted from XBRL filings.")
        return 0

    # Step 6: Bulk upsert into historical_shares
    total_records = 0
    COMMIT_BATCH = 500

    pending = list(records_map.items())
    for batch_start in range(0, len(pending), COMMIT_BATCH):
        batch = pending[batch_start: batch_start + COMMIT_BATCH]

        for (sec_id, quarter_date), shares in batch:
            existing = session.execute(
                select(HistoricalShare).where(
                    HistoricalShare.security_id == sec_id,
                    HistoricalShare.quarter_date == quarter_date,
                )
            ).scalar_one_or_none()

            if existing:
                existing.issued_shares = shares
                existing.source = "NSE_XBRL_SHP"
            else:
                session.add(
                    HistoricalShare(
                        security_id=sec_id,
                        quarter_date=quarter_date,
                        issued_shares=shares,
                        source="NSE_XBRL_SHP",
                    )
                )
            total_records += 1

        session.commit()

        if progress_callback:
            pct = min((batch_start + COMMIT_BATCH) / len(pending) * 100.0, 100.0)
            progress_callback(pct)

    logger.info(
        f"NSE XBRL historical shares sync completed. "
        f"Total records upserted: {total_records} (source: NSE_XBRL_SHP)."
    )
    return total_records
