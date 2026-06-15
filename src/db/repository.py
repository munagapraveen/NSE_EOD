from sqlalchemy import select
from sqlalchemy.orm import Session
from src.models import RawPrice, AdjustedPrice


def bulk_upsert_raw_prices(session: Session, records: list[dict]) -> int:
    """
    Bulk upsert raw prices checking for duplicates.
    Checks (security_id, trade_date) pairs.
    """
    if not records:
        return 0

    security_ids = list({r["security_id"] for r in records})
    trade_dates = list({r["trade_date"] for r in records})

    # Query existing records
    existing = session.execute(
        select(RawPrice.security_id, RawPrice.trade_date)
        .where(RawPrice.security_id.in_(security_ids))
        .where(RawPrice.trade_date.in_(trade_dates))
    ).all()
    existing_keys = {(row.security_id, row.trade_date) for row in existing}

    # Filter out duplicates
    new_records = [
        r for r in records
        if (r["security_id"], r["trade_date"]) not in existing_keys
    ]

    if new_records:
        session.bulk_insert_mappings(RawPrice, new_records)
        session.commit()

    return len(new_records)



