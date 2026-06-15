from datetime import date, datetime
import asyncio
from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from loguru import logger

from src.models import Security, RawPrice, AdjustedPrice, CorporateAction
from src.utils.math_utils import truncate_decimal


async def adjust_prices_for_security(session: Session, security_id: int) -> int:
    """
    Calculate and populate adjusted prices for a specific security.
    Applies compounding split/bonus factors to historical data prior to ex-dates.
    
    Returns:
        Number of adjusted price records written.
    """
    # 1. Fetch all corporate actions for this security (ordered by ex_date ASC)
    actions = session.execute(
        select(CorporateAction)
        .where(CorporateAction.security_id == security_id)
        .where(CorporateAction.action_type.in_(["SPLIT", "BONUS"]))
        .order_by(CorporateAction.ex_date.asc())
    ).scalars().all()

    # 2. Fetch all raw prices for this security (ordered by trade_date ASC)
    raw_prices = session.execute(
        select(RawPrice)
        .where(RawPrice.security_id == security_id)
        .order_by(RawPrice.trade_date.asc())
    ).scalars().all()

    if not raw_prices:
        return 0

    # 3. Create a list of ex_date and adjustment factor tuples (guarding against invalid/non-positive factors)
    action_factors = []
    for a in actions:
        try:
            factor = float(a.adjustment_factor) if a.adjustment_factor else 1.0
            if factor <= 0:
                logger.warning(f"Invalid adjustment factor {factor} for corporate action ID {a.id}. Defaulting to 1.0.")
                factor = 1.0
        except Exception as e:
            logger.warning(f"Failed to parse adjustment factor for corporate action ID {a.id}: {e}. Defaulting to 1.0.")
            factor = 1.0
        action_factors.append((a.ex_date, factor))

    # 4. Generate adjusted prices
    adjusted_records = []
    now = datetime.now()

    for price in raw_prices:
        trade_date = price.trade_date
        
        # Cumulative adjustment factor is the product of all action factors that occur AFTER this trade date
        cumulative_factor = 1.0
        for ex_date, factor in action_factors:
            if trade_date < ex_date:
                cumulative_factor *= factor
                
        if cumulative_factor <= 0:
            cumulative_factor = 1.0

        adjusted_records.append({
            "security_id": security_id,
            "trade_date": trade_date,
            "adj_open": truncate_decimal(float(price.open) / cumulative_factor, 2),
            "adj_high": truncate_decimal(float(price.high) / cumulative_factor, 2),
            "adj_low": truncate_decimal(float(price.low) / cumulative_factor, 2),
            "adj_close": truncate_decimal(float(price.close) / cumulative_factor, 2),
            "adj_volume": int(round(float(price.volume) * cumulative_factor)),
            "adjustment_factor": round(cumulative_factor, 6)
        })

    # 5. Delete existing adjusted prices for this security and bulk insert
    session.execute(
        delete(AdjustedPrice).where(AdjustedPrice.security_id == security_id)
    )
    
    if adjusted_records:
        session.bulk_insert_mappings(AdjustedPrice, adjusted_records)
        
    # 6. Mark corporate actions as processed
    for action in actions:
        action.is_processed = True
        action.processed_at = now

    session.commit()
    
    logger.debug(
        f"Adjusted prices updated for security ID {security_id}: "
        f"{len(adjusted_records)} records processed (compounded factors: {len(actions)} actions)."
    )
    
    return len(adjusted_records)


async def adjust_all_prices(session: Session) -> int:
    """
    Run price adjustment calculation for all securities.
    
    Returns:
        Total number of adjusted price records written.
    """
    logger.info("Starting global price adjustment calculation...")
    
    securities = session.execute(
        select(Security.id)
    ).scalars().all()
    
    total_written = 0
    for sec_id in securities:
        try:
            written = await adjust_prices_for_security(session, sec_id)
            total_written += written
        except Exception as e:
            logger.error(f"Failed to adjust prices for security ID {sec_id}: {e}")
            session.rollback()
        await asyncio.sleep(0.01)
            
    logger.info(f"Global price adjustment completed. Total records adjusted: {total_written}")
    return total_written


async def adjust_incremental_prices(session: Session, start_date: date, end_date: date) -> int:
    """
    Fast incremental price adjustments for a short date range.
    Only recalculates full history for securities that had splits/bonuses ex-dating in this range.
    For all other securities, it simply copies the new raw prices to adjusted prices
    using their existing cumulative adjustment factors.
    """
    from sqlalchemy import func
    
    # 1. Find securities with corporate actions ex-dating in this range
    actions_query = (
        select(CorporateAction.security_id)
        .where(CorporateAction.ex_date >= start_date)
        .where(CorporateAction.ex_date <= end_date)
        .where(CorporateAction.action_type.in_(["SPLIT", "BONUS"]))
    )
    affected_sec_ids = set(session.execute(actions_query).scalars().all())
    
    # 2. Recalculate full history for affected securities
    for sec_id in affected_sec_ids:
        await adjust_prices_for_security(session, sec_id)
        await asyncio.sleep(0.01)
        
    # 3. For all other securities, copy today's raw prices using their latest known factor
    # Subquery to get the latest trade date in adjusted_prices prior to start_date
    subq = (
        select(
            AdjustedPrice.security_id,
            func.max(AdjustedPrice.trade_date).label("max_date")
        )
        .where(AdjustedPrice.trade_date < start_date)
        .group_by(AdjustedPrice.security_id)
        .subquery()
    )
    
    latest_factors = session.execute(
        select(AdjustedPrice.security_id, AdjustedPrice.adjustment_factor)
        .join(subq, (AdjustedPrice.security_id == subq.c.security_id) & (AdjustedPrice.trade_date == subq.c.max_date))
    ).all()
    
    factor_map = {f.security_id: float(f.adjustment_factor) for f in latest_factors}
    
    # Query raw prices in range
    raw_prices_query = (
        select(RawPrice)
        .where(RawPrice.trade_date >= start_date)
        .where(RawPrice.trade_date <= end_date)
    )
    if affected_sec_ids:
        raw_prices_query = raw_prices_query.where(RawPrice.security_id.not_in(affected_sec_ids))
        
    raw_prices = session.execute(raw_prices_query).scalars().all()
    
    adjusted_records = []
    for rp in raw_prices:
        factor = factor_map.get(rp.security_id, 1.0)
        if factor <= 0:
            factor = 1.0
        adjusted_records.append({
            "security_id": rp.security_id,
            "trade_date": rp.trade_date,
            "adj_open": truncate_decimal(float(rp.open) / factor, 2),
            "adj_high": truncate_decimal(float(rp.high) / factor, 2),
            "adj_low": truncate_decimal(float(rp.low) / factor, 2),
            "adj_close": truncate_decimal(float(rp.close) / factor, 2),
            "adj_volume": int(round(float(rp.volume) * factor)),
            "adjustment_factor": round(factor, 6)
        })
        
    if adjusted_records:
        delete_query = delete(AdjustedPrice).where(AdjustedPrice.trade_date >= start_date).where(AdjustedPrice.trade_date <= end_date)
        if affected_sec_ids:
            delete_query = delete_query.where(AdjustedPrice.security_id.not_in(affected_sec_ids))
        session.execute(delete_query)
        
        session.bulk_insert_mappings(AdjustedPrice, adjusted_records)
        session.commit()
        
    logger.info(f"Incremental price adjustment completed. Total records adjusted: {len(adjusted_records)}")
    return len(adjusted_records)

