from datetime import date, datetime
from contextlib import contextmanager
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from loguru import logger

from src.models import Security, SymbolChange
from src.services.nse_client import NSEClient
from src.utils.date_utils import get_now_ist


@contextmanager
def temporary_index_drop(session: Session):
    """
    Temporarily drop the unique indexes on securities before updating symbols,
    then recreate them afterwards. This works around a known DuckDB bug where
    updating indexed columns on a table with active foreign keys causes
    spurious constraint check failures.
    """
    dialect_name = session.get_bind().dialect.name
    if dialect_name != "duckdb":
        yield
        return

    def index_exists(name):
        res = session.execute(
            text("SELECT COUNT(*) FROM duckdb_indexes WHERE index_name = :name"),
            {"name": name}
        ).scalar()
        return res > 0

    dropped_symbol = False
    dropped_isin = False
    
    try:
        if index_exists("ix_securities_symbol"):
            session.execute(text("DROP INDEX ix_securities_symbol"))
            dropped_symbol = True
        if index_exists("ix_securities_isin"):
            session.execute(text("DROP INDEX ix_securities_isin"))
            dropped_isin = True
            
        if dropped_symbol or dropped_isin:
            session.commit()
            
        yield
    except Exception as e:
        logger.warning(f"Error occurred during temporary index drop context: {e}. Rolling back transaction.")
        try:
            session.rollback()
        except Exception as rollback_err:
            logger.warning(f"Rollback failed: {rollback_err}")
        raise
    finally:
        recreate_needed = False
        if dropped_symbol and not index_exists("ix_securities_symbol"):
            session.execute(text("CREATE UNIQUE INDEX ix_securities_symbol ON securities(symbol)"))
            recreate_needed = True
        if dropped_isin and not index_exists("ix_securities_isin"):
            session.execute(text("CREATE UNIQUE INDEX ix_securities_isin ON securities(isin)"))
            recreate_needed = True
            
        if recreate_needed:
            session.commit()


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
    from src.models import RawPrice, AdjustedPrice, Indicator, MarketCap, CorporateAction, SymbolChange, HistoricalShare

    logger.info(f"Merging price history and metadata from ID {old_sec.id} ({old_sec.symbol}) into ID {new_sec.id} ({new_sec.symbol})...")

    # Identify dates that already exist in the target security
    target_dates = set(session.execute(
        select(RawPrice.trade_date).where(RawPrice.security_id == new_sec.id)
    ).scalars().all())

    # Helper to count non‑null data columns (exclude id, security_id, trade_date)
    def _non_null_count(row):
        return sum(1 for col in row.__table__.columns
                   if col.name not in {"id", "security_id", "trade_date"}
                   and getattr(row, col.name) is not None)

    # Reconcile overlapping rows for each price‑related model
    models = [RawPrice, AdjustedPrice, Indicator, MarketCap]
    for Model in models:
        if not target_dates:
            continue
        # Load old rows that overlap
        old_rows = session.execute(
            select(Model).where(Model.security_id == old_sec.id)
            .where(Model.trade_date.in_(list(target_dates)))
        ).scalars().all()
        for old in old_rows:
            # Load corresponding new row (there will be exactly one)
            new = session.execute(
                select(Model).where(Model.security_id == new_sec.id)
                .where(Model.trade_date == old.trade_date)
            ).scalar_one_or_none()
            if new is None:
                continue
            # Keep the row with more non‑null fields
            if _non_null_count(old) > _non_null_count(new):
                # Update the new row with values from the old row
                update_dict = {
                    col.name: getattr(old, col.name)
                    for col in Model.__table__.columns
                    if col.name not in {"id", "security_id", "trade_date"}
                }
                session.execute(
                    update(Model)
                    .where(Model.id == new.id)
                    .values(**update_dict)
                )
        # After reconciliation, delete the old overlapping rows
        session.execute(
            delete(Model)
            .where(Model.security_id == old_sec.id)
            .where(Model.trade_date.in_(list(target_dates)))
        )

    # Update security_id for remaining (non‑overlapping) rows
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

    # Resolve corporate action overlaps to prevent unique constraint violation on merge
    existing_cas = session.execute(
        select(CorporateAction.ex_date, CorporateAction.action_type)
        .where(CorporateAction.security_id == new_sec.id)
    ).all()
    if existing_cas:
        for ex_date, action_type in existing_cas:
            session.execute(
                delete(CorporateAction)
                .where(CorporateAction.security_id == old_sec.id)
                .where(CorporateAction.ex_date == ex_date)
                .where(CorporateAction.action_type == action_type)
            )

    session.execute(
        update(CorporateAction)
        .where(CorporateAction.security_id == old_sec.id)
        .values(security_id=new_sec.id)
    )

    # Resolve historical shares overlaps to prevent unique constraint violation on merge
    existing_shares = session.execute(
        select(HistoricalShare.quarter_date)
        .where(HistoricalShare.security_id == new_sec.id)
    ).scalars().all()
    if existing_shares:
        # Reconcile overlapping historical shares, keeping the richer record
        old_shares_rows = session.execute(
            select(HistoricalShare)
            .where(HistoricalShare.security_id == old_sec.id)
            .where(HistoricalShare.quarter_date.in_(existing_shares))
        ).scalars().all()
        
        def _non_null_share_count(row):
            return sum(1 for col in row.__table__.columns
                       if col.name not in {"id", "security_id", "quarter_date"}
                       and getattr(row, col.name) is not None)

        for old in old_shares_rows:
            new = session.execute(
                select(HistoricalShare)
                .where(HistoricalShare.security_id == new_sec.id)
                .where(HistoricalShare.quarter_date == old.quarter_date)
            ).scalar_one_or_none()
            if new is None:
                continue
            
            # Keep the row with more non-null fields
            if _non_null_share_count(old) > _non_null_share_count(new):
                update_dict = {
                    col.name: getattr(old, col.name)
                    for col in HistoricalShare.__table__.columns
                    if col.name not in {"id", "security_id", "quarter_date"}
                }
                session.execute(
                    update(HistoricalShare)
                    .where(HistoricalShare.id == new.id)
                    .values(**update_dict)
                )

        # After reconciliation, delete the old overlapping rows
        session.execute(
            delete(HistoricalShare)
            .where(HistoricalShare.security_id == old_sec.id)
            .where(HistoricalShare.quarter_date.in_(existing_shares))
        )

    session.execute(
        update(HistoricalShare)
        .where(HistoricalShare.security_id == old_sec.id)
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

        with temporary_index_drop(session):
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
                            change_rec.applied_at = get_now_ist()
                            applied_count += 1
                        else:
                            logger.info(f"Applying symbol change: {old_sym} -> {new_sym} (Security ID: {old_sec.id})")
                            old_sec.symbol = new_sym
                            if comp_name and not old_sec.company_name:
                                old_sec.company_name = comp_name
                            
                            change_rec.security_id = old_sec.id
                            change_rec.is_applied = True
                            change_rec.applied_at = get_now_ist()
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
        with temporary_index_drop(session):
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
                        change.applied_at = get_now_ist()
                        applied_count += 1
                    else:
                        logger.info(f"Applying pending symbol change: {change.old_symbol} -> {change.new_symbol} (ID: {old_sec.id})")
                        old_sec.symbol = change.new_symbol
                        change.security_id = old_sec.id
                        change.is_applied = True
                        change.applied_at = get_now_ist()
                        applied_count += 1

            if applied_count > 0:
                session.commit()
                logger.info(f"Applied {applied_count} pending symbol changes.")

        return applied_count
