from datetime import date
import asyncio
from sqlalchemy import select, delete, func
from sqlalchemy.orm import Session
from loguru import logger

from src.models import Security, RawPrice, AdjustedPrice, MarketCap, HistoricalShare
from src.utils.math_utils import truncate_decimal
from config.constants import CRORE


async def calculate_historical_market_cap(session: Session, security_id: int, current_issued_shares: int) -> int:
    """
    Calculate historical market cap for a specific stock by retrieving direct historical
    quarterly outstanding shares from the database, falling back to reverse-engineering.
    
    Returns:
        Number of market cap records written.
    """
    # 1. Fetch raw close prices and their corresponding adjustment factors by joining raw_prices and adjusted_prices
    query = (
        select(RawPrice.trade_date, RawPrice.close, AdjustedPrice.adjustment_factor)
        .join(
            AdjustedPrice,
            (RawPrice.security_id == AdjustedPrice.security_id) & (RawPrice.trade_date == AdjustedPrice.trade_date)
        )
        .where(RawPrice.security_id == security_id)
        .order_by(RawPrice.trade_date.asc())
    )
    
    results = session.execute(query).all()
    if not results:
        return 0

    # 2. Fetch all historical shares for this security
    hist_shares_query = (
        select(HistoricalShare.quarter_date, HistoricalShare.issued_shares)
        .where(HistoricalShare.security_id == security_id)
        .order_by(HistoricalShare.quarter_date.asc())
    )
    hist_shares_rows = session.execute(hist_shares_query).all()
    hist_shares = [(r.quarter_date, r.issued_shares) for r in hist_shares_rows]
    
    # Fetch all adjusted price factors in memory for this security once to avoid N+1 query loop
    adj_rows = session.execute(
        select(AdjustedPrice.trade_date, AdjustedPrice.adjustment_factor)
        .where(AdjustedPrice.security_id == security_id)
        .order_by(AdjustedPrice.trade_date.asc())
    ).all()

    # Resolve factors locally in Python
    quarter_dates = [q_date for q_date, _ in hist_shares]
    factor_map = {}
    for q_date in quarter_dates:
        # Find closest preceding factor
        preceding = [r for r in adj_rows if r.trade_date <= q_date]
        if preceding:
            f = preceding[-1].adjustment_factor
        else:
            # Fallback to closest succeeding factor
            succeeding = [r for r in adj_rows if r.trade_date >= q_date]
            f = succeeding[0].adjustment_factor if succeeding else None
            
        factor_map[q_date] = float(f) if f is not None else 1.0

    records = []
    for row in results:
        trade_date = row.trade_date
        raw_close = float(row.close)
        factor = float(row.adjustment_factor) if row.adjustment_factor else 1.0
        if factor <= 0:
            factor = 1.0

        # Try to resolve shares outstanding from the closest preceding quarter
        resolved_shares = None
        for q_date, shares in reversed(hist_shares):
            if q_date <= trade_date:
                q_factor = factor_map.get(q_date, 1.0)
                if q_factor <= 0:
                    q_factor = 1.0
                # Adjust for any splits/bonuses between the quarter and trade_date
                resolved_shares = int(round(shares * (q_factor / factor)))
                break
                
        # Fallback 1: If trade_date is earlier than the first quarter record, use the first quarter count
        if resolved_shares is None and hist_shares:
            q_date, shares = hist_shares[0]
            q_factor = factor_map.get(q_date, 1.0)
            if q_factor <= 0:
                q_factor = 1.0
            resolved_shares = int(round(shares * (q_factor / factor)))
            
        # Fallback 2: If no quarterly records exist at all, reverse-engineer using current shares
        if resolved_shares is None:
            if not current_issued_shares or current_issued_shares <= 0:
                continue
            resolved_shares = int(round(current_issued_shares / factor))

        mcap_value = truncate_decimal((resolved_shares * raw_close) / CRORE, 2)
        close_price_truncated = truncate_decimal(raw_close, 2)

        records.append({
            "security_id": security_id,
            "trade_date": trade_date,
            "close_price": close_price_truncated,
            "issued_shares": resolved_shares,
            "market_cap": mcap_value,
            "shares_source": "NSE_XBRL_SHP" if hist_shares else "REVERSE_ENGINEERED"
        })

    # Delete existing records for this security
    session.execute(
        delete(MarketCap).where(MarketCap.security_id == security_id)
    )

    if records:
        session.bulk_insert_mappings(MarketCap, records)

    logger.debug(
        f"Calculated historical market cap for security ID {security_id}: "
        f"{len(records)} records saved (source: {'NSE_XBRL_SHP' if hist_shares else 'REVERSE_ENGINEERED'})."
    )
    return len(records)


async def calculate_all_historical_market_caps(session: Session) -> int:
    """
    Calculate historical market cap for all active stocks in the database.
    Only calculated for security_type == 'STOCK'.
    
    Returns:
        Total number of market cap records written.
    """
    logger.info("Starting global historical market cap calculation...")
    
    # Fetch all stocks that have issued shares populated
    stocks = session.execute(
        select(Security.id, Security.symbol, Security.issued_shares)
        .where(Security.security_type == "STOCK")
        .where(Security.is_active == True)
        .where(Security.is_delisted == False)
    ).all()

    total_written = 0
    for stock in stocks:
        if stock.issued_shares:
            written = await calculate_historical_market_cap(
                session, stock.id, stock.issued_shares
            )
            total_written += written
        else:
            logger.warning(f"Stock {stock.symbol} (ID: {stock.id}) has NULL issued_shares. Skipping.")
        await asyncio.sleep(0.01)
        
    session.commit()

    logger.info(f"Global historical market cap calculation completed. Total records written: {total_written}")
    return total_written


async def calculate_incremental_market_caps_for_range(session: Session, start_date: date, end_date: date) -> int:
    """
    Calculate market caps incrementally for a date range.
    Recalculates full history for stocks that had splits/bonuses ex-dating in this range.
    For all other stocks, calculates market cap only for the new dates.
    """
    from src.models import CorporateAction

    # 1. Find stocks with corporate actions ex-dating in this range
    actions_query = (
        select(CorporateAction.security_id)
        .where(CorporateAction.ex_date >= start_date)
        .where(CorporateAction.ex_date <= end_date)
        .where(CorporateAction.action_type.in_(["SPLIT", "BONUS"]))
    )
    affected_sec_ids = set(session.execute(actions_query).scalars().all())

    # 2. Recalculate full history for affected stocks
    total_written = 0
    stocks_query = (
        select(Security.id, Security.symbol, Security.issued_shares)
        .where(Security.security_type == "STOCK")
        .where(Security.is_active == True)
        .where(Security.is_delisted == False)
    )
    stocks = session.execute(stocks_query).all()
    stock_shares_map = {s.id: s.issued_shares for s in stocks if s.issued_shares}

    for stock_id in affected_sec_ids:
        shares = stock_shares_map.get(stock_id)
        if shares:
            written = await calculate_historical_market_cap(session, stock_id, shares)
            total_written += written
        await asyncio.sleep(0.01)

    # 3. Fetch all historical shares for active stocks
    hist_shares_query = (
        select(HistoricalShare.security_id, HistoricalShare.quarter_date, HistoricalShare.issued_shares)
        .order_by(HistoricalShare.security_id, HistoricalShare.quarter_date.asc())
    )
    hist_shares_rows = session.execute(hist_shares_query).all()
    
    from collections import defaultdict
    shares_by_sec = defaultdict(list)
    for r in hist_shares_rows:
        shares_by_sec[r.security_id].append((r.quarter_date, r.issued_shares))

    # Fetch adjustment factors for the unique quarter dates of all securities
    unique_quarter_dates = {r.quarter_date for r in hist_shares_rows}
    factor_map = {}
    for q_date in unique_quarter_dates:
        # Load the exact factors for this date for all securities
        exact_rows = session.execute(
            select(AdjustedPrice.security_id, AdjustedPrice.adjustment_factor)
            .where(AdjustedPrice.trade_date == q_date)
        ).all()
        for r in exact_rows:
            factor_map[(r.security_id, q_date)] = float(r.adjustment_factor)
            
        # For any security missing on this exact date (e.g. weekend/holiday), find closest preceding/succeeding factors in bulk
        missing_sec_ids = [s.id for s in stocks if (s.id, q_date) not in factor_map]
        if missing_sec_ids:
            # 1. Fetch closest preceding factors in bulk
            subq_prec = (
                select(
                    AdjustedPrice.security_id,
                    func.max(AdjustedPrice.trade_date).label("max_date")
                )
                .where(AdjustedPrice.security_id.in_(missing_sec_ids))
                .where(AdjustedPrice.trade_date <= q_date)
                .group_by(AdjustedPrice.security_id)
                .subquery()
            )
            prec_factors = session.execute(
                select(AdjustedPrice.security_id, AdjustedPrice.adjustment_factor)
                .join(subq_prec, (AdjustedPrice.security_id == subq_prec.c.security_id) & (AdjustedPrice.trade_date == subq_prec.c.max_date))
            ).all()
            
            resolved_sec_ids = set()
            for r in prec_factors:
                factor_map[(r.security_id, q_date)] = float(r.adjustment_factor)
                resolved_sec_ids.add(r.security_id)
                
            # 2. For any still missing, fallback to closest succeeding factors in bulk
            still_missing = [sid for sid in missing_sec_ids if sid not in resolved_sec_ids]
            if still_missing:
                subq_succ = (
                    select(
                        AdjustedPrice.security_id,
                        func.min(AdjustedPrice.trade_date).label("min_date")
                    )
                    .where(AdjustedPrice.security_id.in_(still_missing))
                    .where(AdjustedPrice.trade_date >= q_date)
                    .group_by(AdjustedPrice.security_id)
                    .subquery()
                )
                succ_factors = session.execute(
                    select(AdjustedPrice.security_id, AdjustedPrice.adjustment_factor)
                    .join(subq_succ, (AdjustedPrice.security_id == subq_succ.c.security_id) & (AdjustedPrice.trade_date == subq_succ.c.min_date))
                ).all()
                
                resolved_succ = set()
                for r in succ_factors:
                    factor_map[(r.security_id, q_date)] = float(r.adjustment_factor)
                    resolved_succ.add(r.security_id)
                    
                # 3. Ultimate fallback: default to 1.0 if absolutely no prices exist yet
                for sid in still_missing:
                    if sid not in resolved_succ:
                        factor_map[(sid, q_date)] = 1.0
            
            # Ultimate fallback for any that missed preceding search
            for sid in missing_sec_ids:
                if (sid, q_date) not in factor_map:
                    factor_map[(sid, q_date)] = 1.0

    # 4. For all other stocks, calculate market cap only for the new dates
    query = (
        select(
            RawPrice.security_id,
            RawPrice.trade_date,
            RawPrice.close,
            AdjustedPrice.adjustment_factor
        )
        .join(
            AdjustedPrice,
            (RawPrice.security_id == AdjustedPrice.security_id) & (RawPrice.trade_date == AdjustedPrice.trade_date)
        )
        .where(RawPrice.trade_date >= start_date)
        .where(RawPrice.trade_date <= end_date)
    )
    if affected_sec_ids:
        query = query.where(RawPrice.security_id.not_in(affected_sec_ids))

    results = session.execute(query).all()
    records = []
    for row in results:
        sec_id = row.security_id
        trade_date = row.trade_date
        raw_close = float(row.close)
        factor = float(row.adjustment_factor) if row.adjustment_factor else 1.0
        if factor <= 0:
            factor = 1.0

        current_shares = stock_shares_map.get(sec_id)
        hist_shares = shares_by_sec.get(sec_id, [])

        # Try to resolve shares from closest preceding quarter
        resolved_shares = None
        for q_date, shares in reversed(hist_shares):
            if q_date <= trade_date:
                q_factor = factor_map.get((sec_id, q_date), 1.0)
                if q_factor <= 0:
                    q_factor = 1.0
                resolved_shares = int(round(shares * (q_factor / factor)))
                break

        # Fallback 1: earlier than first quarter record
        if resolved_shares is None and hist_shares:
            q_date, shares = hist_shares[0]
            q_factor = factor_map.get((sec_id, q_date), 1.0)
            if q_factor <= 0:
                q_factor = 1.0
            resolved_shares = int(round(shares * (q_factor / factor)))

        # Fallback 2: no quarterly records exist at all
        if resolved_shares is None:
            if not current_shares:
                continue
            resolved_shares = int(round(current_shares / factor))

        mcap_value = truncate_decimal((resolved_shares * raw_close) / CRORE, 2)
        records.append({
            "security_id": sec_id,
            "trade_date": trade_date,
            "close_price": truncate_decimal(raw_close, 2),
            "issued_shares": resolved_shares,
            "market_cap": mcap_value,
            "shares_source": "NSE_XBRL_SHP" if hist_shares else "REVERSE_ENGINEERED"
        })

    # Always delete existing records in range for non-affected securities to prevent stale data
    delete_query = (
        delete(MarketCap)
        .where(MarketCap.trade_date >= start_date)
        .where(MarketCap.trade_date <= end_date)
    )
    if affected_sec_ids:
        delete_query = delete_query.where(MarketCap.security_id.not_in(affected_sec_ids))
    session.execute(delete_query)

    if records:
        session.bulk_insert_mappings(MarketCap, records)
        session.commit()
        total_written += len(records)
    else:
        session.commit()

    logger.info(f"Incremental market cap calculation completed. Total records written: {total_written}")
    return total_written
