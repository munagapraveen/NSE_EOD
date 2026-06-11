import math
from datetime import date
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session
from loguru import logger

from src.models import Security, RawPrice
from src.db.repository import bulk_upsert_raw_prices
from src.services.nse_client import NSEClient


class IndexDownloader:
    """Downloads daily index closing values and imports them into raw_prices for all indexes dynamically."""

    def __init__(self, client: NSEClient):
        self.client = client

    async def get_or_create_index(self, session: Session, name: str) -> int:
        """Retrieve index security ID or create a new index entry in the securities table."""
        name_clean = name.strip()
        sec = session.execute(
            select(Security)
            .where(Security.symbol == name_clean)
            .where(Security.security_type == "INDEX")
        ).scalar_one_or_none()
        
        if sec:
            return sec.id

        sec = Security(
            symbol=name_clean,
            company_name=name_clean,
            security_type="INDEX",
            is_active=True,
            data_source="MASTER_LIST"
        )
        session.add(sec)
        session.flush()
        logger.info(f"Created Index master entry: '{name_clean}'")
        return sec.id

    async def download_and_import_date(self, session: Session, trade_date: date) -> int:
        """
        Download and import index close data for a given date.
        
        Args:
            session: SQLAlchemy database session
            trade_date: Date to download and import
            
        Returns:
            Number of index prices inserted.
        """
        # Date format for index URL is DDMMYYYY
        date_str = trade_date.strftime("%d%m%Y")
        logger.info(f"Downloading Index EOD data for date: {trade_date.isoformat()}")

        try:
            df = await self.client.download_index_csv(date_str)
        except Exception as e:
            import httpx
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 404:
                logger.warning(f"No index data found for {trade_date.isoformat()} (Weekend/Holiday/Delayed). Skipping.")
                return 0
            logger.error(f"Failed to download index data for {trade_date.isoformat()}: {e}")
            raise e

        if df.empty:
            logger.warning(f"Index data for {trade_date.isoformat()} is empty.")
            return 0

        # Strip column names
        df.columns = df.columns.str.strip()

        # Expected columns: Index Name, Index Date, Open Index Value, High Index Value, Low Index Value, Closing Index Value, Volume, Turnover (Rs. Cr.)
        name_col = "Index Name"
        open_col = "Open Index Value"
        high_col = "High Index Value"
        low_col = "Low Index Value"
        close_col = "Closing Index Value"
        vol_col = "Volume"
        turnover_col = "Turnover (Rs. Cr.)"

        for required_col in [name_col, open_col, high_col, low_col, close_col]:
            if required_col not in df.columns:
                logger.error(f"Index file missing column: {required_col}. Columns: {list(df.columns)}")
                raise ValueError(f"Index file missing required column: {required_col}")

        # Process all indexes dynamically (no whitelist filter)
        df_clean = df.copy()

        # Create index mapping for all unique index names in the file
        names_found = set(df_clean[name_col].str.strip())
        index_id_map = {}
        for name in names_found:
            idx_id = await self.get_or_create_index(session, name)
            index_id_map[name] = idx_id

        records_to_insert = []
        for _, row in df_clean.iterrows():
            name = str(row[name_col]).strip()
            idx_id = index_id_map.get(name)
            
            if not idx_id:
                continue

            # Safe conversions
            def safe_float(val, default=0.0):
                try:
                    f = float(val)
                    if math.isnan(f):
                        return 0.0
                    return f
                except:
                    return default

            def safe_int(val, default=0):
                try:
                    i = int(val)
                    if math.isnan(i):
                        return 0
                    return i
                except:
                    return default

            record = {
                "security_id": idx_id,
                "trade_date": trade_date,
                "open": safe_float(row[open_col]),
                "high": safe_float(row[high_col]),
                "low": safe_float(row[low_col]),
                "close": safe_float(row[close_col]),
                "volume": safe_int(row.get(vol_col, 0)),
                "turnover": safe_float(row.get(turnover_col, 0.0)),
            }
            records_to_insert.append(record)

        inserted_count = bulk_upsert_raw_prices(session, records_to_insert)
        logger.info(f"Imported {inserted_count} index raw price records for {trade_date.isoformat()}")
        
        return inserted_count
