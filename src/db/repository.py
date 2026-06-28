from sqlalchemy import select
from sqlalchemy.orm import Session
from src.models import RawPrice, AdjustedPrice
from sqlalchemy.dialects.postgresql import insert as pg_insert


def bulk_upsert_raw_prices(session: Session, records: list[dict]) -> int:
    """
    Bulk upsert raw prices checking for duplicates.
    Updates existing records and inserts new ones.
    Checks (security_id, trade_date) pairs.
    """
    if not records:
        return 0

    try:
        dialect_name = session.get_bind().dialect.name
    except Exception:
        dialect_name = "generic"

    if dialect_name == "postgresql":
        stmt = pg_insert(RawPrice).values(records)
        update_dict = {
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
            "prev_close": stmt.excluded.prev_close,
            "last_price": stmt.excluded.last_price,
            "turnover": stmt.excluded.turnover,
            "total_trades": stmt.excluded.total_trades,
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["security_id", "trade_date"],
            set_=update_dict
        )
        session.execute(stmt)
        return len(records)

    security_ids = list({r["security_id"] for r in records})
    trade_dates = list({r["trade_date"] for r in records})

    # Query existing records along with their primary keys
    existing = session.execute(
        select(RawPrice.id, RawPrice.security_id, RawPrice.trade_date)
        .where(RawPrice.security_id.in_(security_ids))
        .where(RawPrice.trade_date.in_(trade_dates))
    ).all()
    existing_map = {(row.security_id, row.trade_date): row.id for row in existing}

    new_records = []
    update_records = []

    for r in records:
        key = (r["security_id"], r["trade_date"])
        if key in existing_map:
            upd_dict = r.copy()
            upd_dict["id"] = existing_map[key]
            update_records.append(upd_dict)
        else:
            new_records.append(r)

    if update_records:
        session.bulk_update_mappings(RawPrice, update_records)
    if new_records:
        session.bulk_insert_mappings(RawPrice, new_records)

    return len(new_records) + len(update_records)



