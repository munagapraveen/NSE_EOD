from datetime import date, datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from loguru import logger
import pandas as pd

from src.models import Security, SymbolChange
from src.services.nse_client import NSEClient


def parse_change_date(date_str: str) -> date:
    """Helper to parse effective_date in DD-MMM-YYYY or YYYY-MM-DD format."""
    if not date_str or not isinstance(date_str, str):
        return None
    
    date_str_clean = date_str.strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str_clean, fmt).date()
        except ValueError:
            continue
    return None


def _merge_securities(session: Session, old_sec: Security, new_sec: Security):
    """
    Merge all price history, indicators, corporate actions, and symbol change logs
    from the old security record into the new security record, then delete the old one.
    """
    from sqlalchemy import delete, update
    from src.models import RawPrice, AdjustedPrice, Indicator, MarketCap, CorporateAction, SymbolChange

    logger.info(f"Merging price history and metadata from ID {old_sec.id} ({old_sec.symbol}) into ID {new_sec.id} ({new_sec.symbol})...")

    # Fetch all dates that already exist in the target security to prevent duplicate key violations
    target_dates = set(session.execute(
        select(RawPrice.trade_date).where(RawPrice.security_id == new_sec.id)
    ).scalars().all())

    # Delete overlapping dates from the old security before updating IDs
    if target_dates:
        target_dates_list = list(target_dates)
        session.execute(
            delete(RawPrice)
            .where(RawPrice.security_id == old_sec.id)
            .where(RawPrice.trade_date.in_(target_dates_list))
        )
        session.execute(
            delete(AdjustedPrice)
            .where(AdjustedPrice.security_id == old_sec.id)
            .where(AdjustedPrice.trade_date.in_(target_dates_list))
        )
        session.execute(
            delete(Indicator)
            .where(Indicator.security_id == old_sec.id)
            .where(Indicator.trade_date.in_(target_dates_list))
        )
        session.execute(
            delete(MarketCap)
            .where(MarketCap.security_id == old_sec.id)
            .where(MarketCap.trade_date.in_(target_dates_list))
        )

    # Update security_id for remaining prices and logs
    session.execute(
        update(RawPrice)
        .where(RawPrice.security_id == old_sec.id)
        .values(security_id=new_sec.id)
    )
    session.execute(
        update(AdjustedPrice)
        .where(AdjustedPrice.security_id == old_sec.id)
        .values(security_id=new_sec.id)
    )
    session.execute(
        update(Indicator)
        .where(Indicator.security_id == old_sec.id)
        .values(security_id=new_sec.id)
    )
    session.execute(
        update(MarketCap)
        .where(MarketCap.security_id == old_sec.id)
        .values(security_id=new_sec.id)
    )
    session.execute(
        update(CorporateAction)
        .where(CorporateAction.security_id == old_sec.id)
        .values(security_id=new_sec.id)
    )
    session.execute(
        update(SymbolChange)
        .where(SymbolChange.security_id == old_sec.id)
        .values(security_id=new_sec.id)
    )

    # Backfill any missing metadata from old to new
    if old_sec.industry and not new_sec.industry:
        new_sec.industry = old_sec.industry
    if old_sec.face_value and not new_sec.face_value:
        new_sec.face_value = old_sec.face_value
    if old_sec.issued_shares and not new_sec.issued_shares:
        new_sec.issued_shares = old_sec.issued_shares
    if old_sec.company_name and not new_sec.company_name:
        new_sec.company_name = old_sec.company_name

    # Commit the updates first so DuckDB's foreign key checks register the reassigned security IDs
    session.commit()

    # Delete the old security record directly via SQL to prevent SQLAlchemy from cascade-deleting the merged child rows
    session.execute(
        delete(Security).where(Security.id == old_sec.id)
    )
    session.commit()
    # Expunge from session if still present so SQLAlchemy forgets about the deleted object
    if old_sec in session:
        session.expunge(old_sec)


class SymbolChangesService:
    """Service to track and apply stock ticker symbol changes from NSE."""

    def __init__(self, client: NSEClient):
        self.client = client

    async def sync_symbol_changes(self, session: Session) -> int:
        """
        Download symbolchange.csv, record new changes, and apply them.
        
        Returns:
            Number of newly recorded symbol changes.
        """
        logger.info("Syncing symbol changes list from NSE...")
        try:
            df = await self.client.download_symbol_changes()
        except Exception as e:
            logger.error(f"Failed to download symbol changes: {e}")
            raise e

        if df.empty:
            logger.warning("Symbol changes dataframe is empty.")
            return 0

        logger.info(f"Downloaded {len(df)} symbol change records.")

        new_recorded_count = 0
        applied_count = 0

        for _, row in df.iterrows():
            comp_name = str(row.get("company_name", "")).strip()
            old_sym = str(row.get("old_symbol", "")).strip()
            new_sym = str(row.get("new_symbol", "")).strip()
            eff_date_str = str(row.get("effective_date", "")).strip()

            if not old_sym or not new_sym:
                continue

            eff_date = parse_change_date(eff_date_str)

            # Check if this change record is already in symbol_changes table
            existing_change = session.execute(
                select(SymbolChange)
                .where(SymbolChange.old_symbol == old_sym)
                .where(SymbolChange.new_symbol == new_sym)
            ).scalar_one_or_none()

            change_rec = existing_change
            if not change_rec:
                change_rec = SymbolChange(
                    security_id=None,
                    old_symbol=old_sym,
                    new_symbol=new_sym,
                    effective_date=eff_date,
                    is_applied=False
                )
                session.add(change_rec)
                session.flush()  # get ID
                new_recorded_count += 1

            # If not applied yet, try to apply it
            if not change_rec.is_applied:
                # 1. Look up if we have the old security in our database
                old_sec = session.execute(
                    select(Security).where(Security.symbol == old_sym)
                ).scalar_one_or_none()

                if old_sec:
                    # 2. Check if a security with the new symbol already exists
                    # (to prevent unique constraint conflict on symbol)
                    new_sec_exists = session.execute(
                        select(Security).where(Security.symbol == new_sym)
                    ).scalar_one_or_none()

                    if new_sec_exists:
                        _merge_securities(session, old_sec, new_sec_exists)
                        change_rec.security_id = new_sec_exists.id
                        change_rec.is_applied = True
                        change_rec.applied_at = datetime.now()
                        applied_count += 1
                    else:
                        logger.info(f"Applying symbol change: {old_sym} -> {new_sym} (Security ID: {old_sec.id})")
                        old_sec.symbol = new_sym
                        if comp_name and not old_sec.company_name:
                            old_sec.company_name = comp_name
                        
                        change_rec.security_id = old_sec.id
                        change_rec.is_applied = True
                        change_rec.applied_at = datetime.now()
                        applied_count += 1

        if new_recorded_count > 0 or applied_count > 0:
            session.commit()
            logger.info(f"Recorded {new_recorded_count} new symbol changes; applied {applied_count} changes.")
        else:
            logger.info("No new symbol changes found or applied.")

        return new_recorded_count

    async def scan_and_apply_pending(self, session: Session) -> int:
        """
        Scan symbol_changes table for any unapplied changes and try to apply them.
        Useful when a security was recently added (e.g. from history) and has pending renames.
        """
        pending_changes = session.execute(
            select(SymbolChange).where(SymbolChange.is_applied == False)
        ).scalars().all()

        if not pending_changes:
            return 0

        applied_count = 0
        for change in pending_changes:
            old_sec = session.execute(
                select(Security).where(Security.symbol == change.old_symbol)
            ).scalar_one_or_none()

            if old_sec:
                new_sec_exists = session.execute(
                    select(Security).where(Security.symbol == change.new_symbol)
                ).scalar_one_or_none()

                if new_sec_exists:
                    _merge_securities(session, old_sec, new_sec_exists)
                    change.security_id = new_sec_exists.id
                    change.is_applied = True
                    change.applied_at = datetime.now()
                    applied_count += 1
                else:
                    logger.info(f"Applying pending symbol change: {change.old_symbol} -> {change.new_symbol} (ID: {old_sec.id})")
                    old_sec.symbol = change.new_symbol
                    change.security_id = old_sec.id
                    change.is_applied = True
                    change.applied_at = datetime.now()
                    applied_count += 1

        if applied_count > 0:
            session.commit()
            logger.info(f"Applied {applied_count} pending symbol changes.")

        return applied_count
