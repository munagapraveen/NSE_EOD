import asyncio
from datetime import date
from sqlalchemy import select
from sqlalchemy.orm import Session
from loguru import logger

from src.models import Security, HistoricalShare
from src.services.bse_client import BSEClient
from config.constants import BSE_ANCHOR_DATE, BSE_ANCHOR_QTRID

def get_qtrids_for_range(start_date: date, end_date: date) -> list[int]:
    """
    Returns the list of BSE qtrids covering the date range.
    Anchor: March 31, 2026 -> qtrid 129
    """
    # Calculate starting qtrid
    start_months = (start_date.year - BSE_ANCHOR_DATE.year) * 12 + (start_date.month - BSE_ANCHOR_DATE.month)
    start_qtrid = BSE_ANCHOR_QTRID + (start_months // 3)
    
    # Calculate ending qtrid
    end_months = (end_date.year - BSE_ANCHOR_DATE.year) * 12 + (end_date.month - BSE_ANCHOR_DATE.month)
    end_qtrid = BSE_ANCHOR_QTRID + (end_months // 3)
    
    # Ensure they are in logical range and bounds
    return list(range(start_qtrid, end_qtrid + 1))

async def sync_historical_shares_for_security(
    session: Session, security_id: int, scrip_code: str, start_date: date, bse_client: BSEClient
) -> int:
    """
    Fetch and sync historical quarterly shares for a specific stock starting from start_date.
    Returns:
        Number of historical share records saved.
    """
    qtrids = get_qtrids_for_range(start_date, date.today())
    if not qtrids:
        return 0
        
    logger.debug(f"Syncing historical shares for security ID {security_id} (Scrip: {scrip_code}) across qtrids {qtrids}...")
    
    async def fetch_one(qid):
        try:
            shares, qtr_date = await bse_client.fetch_outstanding_shares(scrip_code, str(qid))
            if shares and qtr_date:
                return qid, shares, qtr_date, None
            return qid, None, None, "No shares parsed"
        except Exception as e:
            return qid, None, None, str(e)
            
    tasks = [fetch_one(qid) for qid in qtrids]
    results = await asyncio.gather(*tasks)
    
    records_added = 0
    for qid, shares, qtr_date, err in results:
        if shares and qtr_date:
            # Check if record already exists for this security and quarter
            stmt = select(HistoricalShare).where(
                HistoricalShare.security_id == security_id,
                HistoricalShare.quarter_date == qtr_date
            )
            existing = session.execute(stmt).scalar()
            if existing:
                existing.issued_shares = shares
            else:
                new_share = HistoricalShare(
                    security_id=security_id,
                    quarter_date=qtr_date,
                    issued_shares=shares,
                    source="BSE_QUARTERLY_SHP"
                )
                session.add(new_share)
            records_added += 1
        else:
            logger.debug(f"Skipped qtrid {qid} for scrip {scrip_code}: {err}")
            
    if records_added > 0:
        session.commit()
        logger.debug(f"Saved {records_added} quarterly share records for security ID {security_id}.")
        
    return records_added

async def sync_all_historical_shares(session: Session, start_date: date, progress_callback=None) -> int:
    """
    Fetch and populate historical shares for all active stocks in the database.
    """
    logger.info(f"Starting global historical outstanding shares sync from {start_date}...")
    
    stocks = session.execute(
        select(Security.id, Security.symbol, Security.isin)
        .where(Security.security_type == "STOCK")
        .where(Security.is_active == True)
        .where(Security.is_delisted == False)
    ).all()
    
    if not stocks:
        return 0
        
    bse_client = BSEClient()
    total_records = 0
    try:
        total = len(stocks)
        CHUNK_SIZE = 15
        
        for idx in range(0, total, CHUNK_SIZE):
            chunk = stocks[idx:idx+CHUNK_SIZE]
            
            async def process_stock(stock):
                if not stock.isin:
                    return 0
                try:
                    scrip_code = await bse_client.lookup_scripcode_by_isin(stock.isin)
                    if not scrip_code:
                        return 0
                    return await sync_historical_shares_for_security(session, stock.id, scrip_code, start_date, bse_client)
                except Exception as err:
                    logger.warning(f"Error processing historical shares for {stock.symbol}: {err}")
                    return 0
                    
            tasks = [process_stock(s) for s in chunk]
            results = await asyncio.gather(*tasks)
            total_records += sum(results)
            
            if progress_callback:
                pct = (idx + len(chunk)) / total * 100.0
                progress_callback(pct)
                
            await asyncio.sleep(0.5)
            
    finally:
        await bse_client.close()
        
    logger.info(f"Global historical shares sync completed. Total quarterly records written: {total_records}")
    return total_records
