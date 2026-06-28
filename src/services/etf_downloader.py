import math
from datetime import date
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session
from loguru import logger

from src.models import Security
from src.db.repository import bulk_upsert_raw_prices
from src.services.nse_client import NSEClient
from src.utils.math_utils import safe_float, safe_int


class ETFDownloader:
    """Manages ETF lists and parses ETF prices from daily CM Bhavcopy, storing them in raw_prices."""

    def __init__(self, client: NSEClient):
        self.client = client

    async def sync_etf_master_list(self, session: Session) -> int:
        """
        Download ETF master list and seed/update the securities table.
        
        Returns:
            Number of ETF records created or updated.
        """
        logger.info("Syncing ETF master list from NSE...")
        try:
            df = await self.client.download_etf_list()
        except Exception as e:
            logger.error(f"Failed to download ETF master list: {e}")
            raise e

        if df.empty:
            logger.warning("ETF master list is empty.")
            return 0

        # Strip spaces from columns
        df.columns = df.columns.str.strip()
        
        symbol_col = None
        name_col = None
        isin_col = None
        underlying_col = None
        
        for col in df.columns:
            col_lower = col.lower()
            if "symbol" in col_lower:
                symbol_col = col
            elif "name" in col_lower or "security" in col_lower:
                name_col = col
            elif "isin" in col_lower:
                isin_col = col
            elif "underlying" in col_lower or "index" in col_lower:
                underlying_col = col

        if not symbol_col:
            raise ValueError("ETF master list missing 'symbol' column")

        # Seeding
        processed_count = 0
        for _, row in df.iterrows():
            sym = str(row[symbol_col]).strip() if symbol_col else None
            name = str(row[name_col]).strip() if name_col else None
            isin_val = str(row[isin_col]).strip() if isin_col else None
            underlying = str(row[underlying_col]).strip() if underlying_col else None
            
            if not sym:
                continue
                
            # Query existing ETF in securities by symbol or ISIN
            # Query existing ETF in securities by symbol or ISIN globally
            sec = None
            if isin_val:
                sec = session.execute(
                    select(Security)
                    .where(Security.isin == isin_val)
                ).scalar_one_or_none()
            if not sec:
                sec = session.execute(
                    select(Security)
                    .where(Security.symbol == sym)
                ).scalar_one_or_none()
                
            if sec:
                # Update details and ensure security_type is set to ETF
                sec.symbol = sym
                sec.company_name = name
                sec.security_type = "ETF"
                if isin_val:
                    sec.isin = isin_val
                sec.is_active = True
                # we don't have underlying_index column in securities directly, but we can store it in industry/remarks
                sec.industry = underlying
            else:
                sec = Security(
                    symbol=sym,
                    company_name=name,
                    security_type="ETF",
                    isin=isin_val,
                    industry=underlying,
                    is_active=True,
                    data_source="MASTER_LIST"
                )
                session.add(sec)
                
            processed_count += 1
            
        session.commit()
        logger.info(f"Synchronized {processed_count} ETFs in the securities master.")
        return processed_count

    async def get_all_etf_symbols(self, session: Session) -> set[str]:
        """Get set of all active ETF symbols currently in database."""
        result = session.execute(
            select(Security.symbol)
            .where(Security.security_type == "ETF")
            .where(Security.is_active == True)
        ).scalars().all()
        return {str(s).strip() for s in result}

    def filter_etf_dataframe(self, df: pd.DataFrame, etf_symbols: set[str]) -> pd.DataFrame:
        """
        Filters a raw bhavcopy DataFrame for ETF records only.
        """
        df.columns = df.columns.str.strip()
        symbol_col = "TckrSymb"  # Use TckrSymb (ticker symbol) instead of FinInstrmId
        inst_type_col = "FinInstrmTp"

        # Filter: row is ETF if symbol is in ETF set OR instrument type is ETF
        is_etf_mask = df[symbol_col].isin(etf_symbols)
        if inst_type_col in df.columns:
            is_etf_mask = is_etf_mask | (df[inst_type_col] == "ETF")
            
        etf_df = df[is_etf_mask].copy()
        return etf_df

    async def import_etf_prices(self, session: Session, filtered_df: pd.DataFrame, trade_date: date) -> int:
        """
        Import filtered ETF prices into the raw_prices table.
        """
        if filtered_df.empty:
            logger.info(f"No ETF prices found in bhavcopy on {trade_date.isoformat()}.")
            return 0

        symbol_col = "TckrSymb"
        isin_col = "ISIN"

        # Create ETF id map
        symbols_found = set(filtered_df[symbol_col].str.strip())
        db_etfs = session.execute(
            select(Security)
            .where(Security.symbol.in_(list(symbols_found)))
            .where(Security.security_type == "ETF")
        ).scalars().all()
        etf_id_map = {e.symbol: e.id for e in db_etfs}

        records_to_insert = []
        for _, row in filtered_df.iterrows():
            sym = str(row[symbol_col]).strip()
            etf_id = etf_id_map.get(sym)
            
            # If ETF is not in our ETF master list yet, create it dynamically
            if not etf_id:
                isin_val = str(row[isin_col]).strip() if isin_col in row else None
                
                # Check globally to prevent unique constraint failures
                global_sec = None
                if isin_val:
                    global_sec = session.execute(
                        select(Security).where(Security.isin == isin_val)
                    ).scalar_one_or_none()
                if not global_sec:
                    global_sec = session.execute(
                        select(Security).where(Security.symbol == sym)
                    ).scalar_one_or_none()
                
                if global_sec:
                    global_sec.security_type = "ETF"
                    if isin_val and global_sec.isin is None:
                        global_sec.isin = isin_val
                    etf_id = global_sec.id
                    etf_id_map[sym] = etf_id
                    logger.info(f"Promoted existing security to ETF: {sym} (ISIN: {isin_val})")
                else:
                    new_etf = Security(
                        symbol=sym,
                        company_name=sym,
                        security_type="ETF",
                        isin=isin_val,
                        is_active=True,
                        data_source="BHAVCOPY_DISCOVERED"
                    )
                    session.add(new_etf)
                    session.flush()
                    etf_id = new_etf.id
                    etf_id_map[sym] = etf_id
                    logger.info(f"Discovered new ETF in bhavcopy: {sym} (ISIN: {isin_val})")


            record = {
                "security_id": etf_id,
                "trade_date": trade_date,
                "open": safe_float(row["OpnPric"]),
                "high": safe_float(row["HghPric"]),
                "low": safe_float(row["LwPric"]),
                "close": safe_float(row["ClsPric"]),
                "volume": safe_int(row["TtlTradgVol"]),
                "turnover": safe_float(row.get("TtlTrfVal", 0.0)),
            }
            records_to_insert.append(record)

        inserted_count = bulk_upsert_raw_prices(session, records_to_insert)
        session.commit()
        logger.info(f"Imported {inserted_count} ETF raw price records for {trade_date.isoformat()}")
        
        return inserted_count

    async def download_and_import_date(self, session: Session, trade_date: date, etf_symbols: set[str] = None) -> int:
        """
        Extract ETF price rows from daily CM bhavcopy and save to raw_prices.
        """
        if etf_symbols is None:
            etf_symbols = await self.get_all_etf_symbols(session)
            
        date_str = trade_date.strftime("%Y%m%d")
        
        try:
            df = await self.client.download_bhavcopy_csv(date_str)
        except Exception as e:
            from src.services.nse_client import HttpNotFoundError
            if isinstance(e, HttpNotFoundError):
                return 0
            logger.error(f"Failed to fetch bhavcopy for ETF parsing on {trade_date.isoformat()}: {e}")
            raise e

        filtered_df = self.filter_etf_dataframe(df, etf_symbols)
        return await self.import_etf_prices(session, filtered_df, trade_date)

