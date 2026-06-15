from sqlalchemy import text
from loguru import logger

def align_database_sequences(engine):
    """
    Check all primary key sequences in the database and align them with the
    maximum ID present in their respective tables. This resolves constraint
    errors due to manually restored tables with sequences out of sync.
    """
    tables_and_seqs = [
        ("securities", "securities_id_seq"),
        ("raw_prices", "raw_prices_id_seq"),
        ("adjusted_prices", "adjusted_prices_id_seq"),
        ("market_cap", "market_cap_id_seq"),
        ("indicators", "indicators_id_seq"),
        ("corporate_actions", "corporate_actions_id_seq"),
        ("symbol_changes", "symbol_changes_id_seq"),
        ("sync_log", "sync_log_id_seq"),
        ("historical_shares", "historical_shares_id_seq"),
    ]

    try:
        logger.info("Starting database sequence alignment check...")
        with engine.begin() as conn:
            for table, seq in tables_and_seqs:
                # Check if table exists
                exists_query = text(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = :table"
                )
                exists = conn.execute(exists_query, {"table": table}).fetchone()
                if not exists:
                    continue

                # Get max ID in the table
                max_id = conn.execute(text(f"SELECT max(id) FROM {table}")).scalar()
                if max_id is None:
                    continue

                # Query current nextval of the sequence
                # Note: this increments the sequence by 1 in DuckDB
                curr_nextval = conn.execute(text(f"SELECT nextval('{seq}')")).scalar()

                if curr_nextval <= max_id:
                    needed = max_id - curr_nextval
                    if needed > 0:
                        logger.info(
                            f"Database sequence out of sync for table '{table}'. "
                            f"max(id)={max_id}, nextval={curr_nextval}. Advancing sequence by {needed}..."
                        )
                        conn.execute(
                            text(f"SELECT count(nextval('{seq}')) FROM range(1, {needed} + 1);")
                        ).scalar()
                        logger.info(f"Aligned sequence '{seq}' successfully for table '{table}'.")
                    else:
                        logger.info(f"Sequence '{seq}' is already aligned (curr_nextval={curr_nextval}, max(id)={max_id}).")
                else:
                    logger.debug(f"Sequence '{seq}' (nextval={curr_nextval}) is already ahead of max(id)={max_id}.")
        logger.info("Database sequence alignment check completed.")
    except Exception as e:
        logger.error(f"Failed to align database sequences: {e}")
