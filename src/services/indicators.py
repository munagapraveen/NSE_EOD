import pandas as pd
import pandas_ta_classic as ta
from datetime import date
import asyncio
from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from loguru import logger

from src.models import Security, AdjustedPrice, Indicator
from src.utils.math_utils import truncate_decimal


async def calculate_indicators_for_security(session: Session, security_id: int) -> int:
    """
    Calculate simple moving averages (SMA 5, 10, 20, 50, 200) for a security
    using its adjusted close price history.
    
    Returns:
        Number of indicator records written.
    """
    # 1. Fetch adjusted close prices sorted by trade_date
    query = (
        select(AdjustedPrice.trade_date, AdjustedPrice.adj_close)
        .where(AdjustedPrice.security_id == security_id)
        .order_by(AdjustedPrice.trade_date.asc())
    )
    
    rows = session.execute(query).all()
    if not rows:
        return 0

    df = pd.DataFrame([{
        "trade_date": r.trade_date,
        "close": float(r.adj_close)
    } for r in rows])

    if df.empty:
        return 0

    # 2. Calculate Simple Moving Averages using pandas-ta-classic
    df["sma_5"] = ta.sma(df["close"], length=5)
    df["sma_10"] = ta.sma(df["close"], length=10)
    df["sma_20"] = ta.sma(df["close"], length=20)
    df["sma_50"] = ta.sma(df["close"], length=50)
    df["sma_200"] = ta.sma(df["close"], length=200)

    # 3. Prepare records for insertion
    records = []
    for _, row in df.iterrows():
        record = {
            "security_id": security_id,
            "trade_date": row["trade_date"]
        }
        
        # Round and map values
        for col in ["sma_5", "sma_10", "sma_20", "sma_50", "sma_200"]:
            val = row[col]
            record[col] = truncate_decimal(val, 2) if pd.notna(val) else None
            
        records.append(record)

    # 4. Delete existing indicators for this security
    session.execute(
        delete(Indicator).where(Indicator.security_id == security_id)
    )

    if records:
        session.bulk_insert_mappings(Indicator, records)

    logger.debug(
        f"Calculated SMAs for security ID {security_id}: "
        f"{len(records)} records saved (SMA 5, 10, 20, 50, 200)."
    )
    return len(records)


async def calculate_all_indicators(session: Session, progress_callback=None) -> int:
    """
    Calculate moving averages globally for all active securities (Stocks, ETFs, and Indexes).
    
    Returns:
        Total number of indicator records written.
    """
    logger.info("Starting global SMA indicator calculation...")
    
    # Fetch all active securities
    securities = session.execute(
        select(Security.id, Security.symbol)
        .where(Security.is_active == True)
        .where(Security.is_delisted == False)
    ).all()

    total_written = 0
    total = len(securities)
    for idx, sec in enumerate(securities):
        if progress_callback:
            pct = 90.0 + (idx / total) * 9.0
            progress_callback("INDICATORS", pct, f"Calculating SMA for {sec.symbol} ({idx+1}/{total})...")
        
        written = await calculate_indicators_for_security(session, sec.id)
        total_written += written
        
        if (idx + 1) % 10 == 0 or (idx + 1) == total:
            logger.info(f"[{idx+1}/{total}] Calculated SMAs for security {sec.symbol}: {written} records saved")
        await asyncio.sleep(0.01)
        
    session.commit()

    logger.info(f"Global SMA indicator calculation completed. Total records written: {total_written}")
    return total_written


async def calculate_incremental_indicators_for_range(session: Session, start_date: date, end_date: date, progress_callback=None) -> int:
    """
    Calculate SMAs incrementally for a short date range.
    Recalculates full history for securities that had splits/bonuses ex-dating in this range.
    For all other securities, loads the last 250 days prior to end_date, computes SMAs,
    and inserts/updates the new dates' records.
    """
    from datetime import timedelta
    from src.models import CorporateAction

    # Load active security symbols to map security_id to symbol for logging/progress purposes
    sec_symbols = {s.id: s.symbol for s in session.execute(
        select(Security.id, Security.symbol)
        .where(Security.is_active == True)
    ).all()}

    # 1. Find securities with corporate actions ex-dating in this range
    actions_query = (
        select(CorporateAction.security_id)
        .where(CorporateAction.ex_date >= start_date)
        .where(CorporateAction.ex_date <= end_date)
        .where(CorporateAction.action_type.in_(["SPLIT", "BONUS"]))
    )
    affected_sec_ids = set(session.execute(actions_query).scalars().all())

    # 2. Recalculate full history for affected securities
    total_written = 0
    for idx, sec_id in enumerate(affected_sec_ids):
        symbol = sec_symbols.get(sec_id, f"ID {sec_id}")
        if progress_callback:
            progress_callback("INDICATORS", 90.0, f"Recalculating affected security {symbol}...")
        written = await calculate_indicators_for_security(session, sec_id)
        total_written += written
        logger.info(f"Recalculated full historical SMAs for affected security {symbol}: {written} records saved")
        await asyncio.sleep(0.01)

    # 3. For all other active securities, load adjusted prices from start_date - 365 days to end_date
    securities = session.execute(
        select(Security.id, Security.symbol)
        .where(Security.is_active == True)
        .where(Security.is_delisted == False)
    ).all()

    other_sec_ids = [s.id for s in securities if s.id not in affected_sec_ids]
    if not other_sec_ids:
        return total_written

    logger.info(f"Loading adjusted prices for {len(other_sec_ids)} securities to calculate SMAs...")

    # Load all adjusted prices in the range in a single query, excluding affected securities
    query = (
        select(AdjustedPrice.security_id, AdjustedPrice.trade_date, AdjustedPrice.adj_close)
        .where(AdjustedPrice.trade_date >= start_date - timedelta(days=365))
        .where(AdjustedPrice.trade_date <= end_date)
    )
    if affected_sec_ids:
        query = query.where(AdjustedPrice.security_id.not_in(affected_sec_ids))
    query = query.order_by(AdjustedPrice.security_id, AdjustedPrice.trade_date.asc())

    rows = session.execute(query).all()
    if not rows:
        return total_written

    df = pd.DataFrame([{
        "security_id": r.security_id,
        "trade_date": r.trade_date,
        "close": float(r.adj_close)
    } for r in rows])

    logger.info(f"Loaded {len(df)} price rows. Calculating SMAs...")

    # Group and calculate SMAs per security
    calculated_dfs = []
    grouped_list = list(df.groupby("security_id"))
    total_groups = len(grouped_list)
    processed_count = 0
    
    for sec_id, group in grouped_list:
        symbol = sec_symbols.get(sec_id, f"ID {sec_id}")
        group = group.sort_values("trade_date").copy()
        group["sma_5"] = ta.sma(group["close"], length=5)
        group["sma_10"] = ta.sma(group["close"], length=10)
        group["sma_20"] = ta.sma(group["close"], length=20)
        group["sma_50"] = ta.sma(group["close"], length=50)
        group["sma_200"] = ta.sma(group["close"], length=200)
        calculated_dfs.append(group)
        processed_count += 1
        
        if progress_callback and processed_count % 50 == 0:
            pct = 90.0 + (processed_count / total_groups) * 9.0
            progress_callback("INDICATORS", pct, f"Calculating SMA for {symbol} ({processed_count}/{total_groups})...")
            
        if processed_count % 50 == 0 or processed_count == total_groups:
            logger.info(f"Calculating SMAs: {processed_count}/{total_groups} securities processed (current: {symbol})...")
            
        await asyncio.sleep(0)
    df_calc = pd.concat(calculated_dfs, ignore_index=True)

    # Filter for new dates only
    df_new = df_calc[(df_calc["trade_date"] >= start_date) & (df_calc["trade_date"] <= end_date)]

    records_to_insert = []
    for _, row in df_new.iterrows():
        record = {
            "security_id": int(row["security_id"]),
            "trade_date": row["trade_date"]
        }
        for col in ["sma_5", "sma_10", "sma_20", "sma_50", "sma_200"]:
            val = row[col]
            record[col] = truncate_decimal(val, 2) if pd.notna(val) else None
        records_to_insert.append(record)

    if records_to_insert:
        # Delete existing indicators in the date range for non-affected securities
        delete_query = (
            delete(Indicator)
            .where(Indicator.trade_date >= start_date)
            .where(Indicator.trade_date <= end_date)
        )
        if affected_sec_ids:
            delete_query = delete_query.where(Indicator.security_id.not_in(affected_sec_ids))
        session.execute(delete_query)
        
        session.bulk_insert_mappings(Indicator, records_to_insert)
        session.commit()
        total_written += len(records_to_insert)

    logger.info(f"Incremental SMA indicator calculation completed. Total records written/updated: {total_written}")
    return total_written

