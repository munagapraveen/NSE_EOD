import math
from datetime import date
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session
from loguru import logger

from src.models import Security
from src.db.repository import bulk_upsert_raw_prices
from src.services.nse_client import NSEClient
from config.constants import EQUITY_SERIES
from src.utils.math_utils import safe_float, safe_int




class StockDownloader:
    """Downloads and parses daily CM Bhavcopy and populates raw price tables for stocks."""

    def __init__(self, client: NSEClient):
        self.client = client

    def filter_stock_dataframe(self, df: pd.DataFrame, etf_symbols: set = None) -> pd.DataFrame:
        """
        Filters a raw bhavcopy DataFrame for regular stocks only.
        """
        # Strip columns
        df.columns = df.columns.str.strip()

        # Check for necessary UDiFF columns mapping
        required_cols = ["TradDt", "TckrSymb", "ISIN", "SctySrs", "OpnPric", "HghPric", "LwPric", "ClsPric", "TtlTradgVol"]
        for col_raw in required_cols:
            if col_raw not in df.columns:
                logger.error(f"Missing column '{col_raw}' in bhavcopy. Columns present: {list(df.columns)}")
                raise ValueError(f"Bhavcopy missing required UDiFF column: {col_raw}")

        series_col = "SctySrs"
        inst_type_col = "FinInstrmTp"
        symbol_col = "TckrSymb"

        # Filter for Equities (SctySrs in ['EQ', 'BE'])
        filtered_df = df[df[series_col].isin(EQUITY_SERIES)].copy()
        
        # Exclude based on FinInstrmTp == "ETF"
        if inst_type_col in filtered_df.columns:
            filtered_df = filtered_df[filtered_df[inst_type_col] == "STK"]
        
        # Exclude ETFs if we have an ETF symbol list
        if etf_symbols:
            filtered_df = filtered_df[~filtered_df[symbol_col].isin(etf_symbols)]

        return filtered_df

    async def import_stock_prices(self, session: Session, filtered_df: pd.DataFrame, trade_date: date) -> int:
        """
        Import filtered stock prices into the raw_prices table.
        """
        if filtered_df.empty:
            logger.info(f"No regular stock records found in bhavcopy on {trade_date.isoformat()}.")
            return 0

        symbol_col = "TckrSymb"
        series_col = "SctySrs"
        isin_col = "ISIN"

        records_to_insert = []
        
        # Bulk query matching securities by symbol or ISIN to avoid N+1 query overhead
        unique_stocks = filtered_df[[symbol_col, series_col, isin_col]].drop_duplicates()
        symbols = list({str(row[symbol_col]).strip() for _, row in unique_stocks.iterrows() if row[symbol_col]})
        isins = list({str(row[isin_col]).strip() for _, row in unique_stocks.iterrows() if row[isin_col]})

        db_securities = session.execute(
            select(Security)
            .where((Security.symbol.in_(symbols)) | (Security.isin.in_(isins)))
        ).scalars().all()

        sec_by_isin = {s.isin: s for s in db_securities if s.isin}
        sec_by_symbol = {s.symbol: s for s in db_securities}

        stock_id_map = {}
        for _, row in unique_stocks.iterrows():
            sym = str(row[symbol_col]).strip()
            srs = str(row[series_col]).strip()
            isin_val = str(row[isin_col]).strip()

            sec = sec_by_isin.get(isin_val) if isin_val else None
            if not sec:
                sec = sec_by_symbol.get(sym)

            if sec:
                # Update last_seen_date and detect symbol changes
                if sec.last_seen_date is None or trade_date > sec.last_seen_date:
                    sec.last_seen_date = trade_date

                if isin_val and sec.isin is None:
                    sec.isin = isin_val
                stock_id_map[(sym, isin_val)] = sec.id
            else:
                # Auto-create new stock record
                new_sec = Security(
                    symbol=sym,
                    company_name=None,
                    security_type="STOCK",
                    isin=isin_val if isin_val else None,
                    face_value=None,
                    is_active=True,
                    is_delisted=False,
                    data_source="BHAVCOPY_DISCOVERED",
                    first_seen_date=trade_date,
                    last_seen_date=trade_date,
                )
                session.add(new_sec)
                session.flush()  # get ID
                logger.info(f"Auto-discovered stock: {sym} (ISIN: {isin_val})")

                # Cache locally for subsequent lookups in the same batch
                sec_by_symbol[sym] = new_sec
                if isin_val:
                    sec_by_isin[isin_val] = new_sec
                stock_id_map[(sym, isin_val)] = new_sec.id

        # Parse prices and map to security_id
        for _, row in filtered_df.iterrows():
            sym = str(row[symbol_col]).strip()
            isin_val = str(row[isin_col]).strip()
            stock_id = stock_id_map.get((sym, isin_val))
            
            if not stock_id:
                continue

            record = {
                "security_id": stock_id,
                "trade_date": trade_date,
                "open": safe_float(row["OpnPric"]),
                "high": safe_float(row["HghPric"]),
                "low": safe_float(row["LwPric"]),
                "close": safe_float(row["ClsPric"]),
                "last_price": safe_float(row.get("LastPric", 0.0)),
                "prev_close": safe_float(row.get("PrvsClsgPric", 0.0)),
                "volume": safe_int(row["TtlTradgVol"]),
                "turnover": safe_float(row.get("TtlTrfVal", 0.0)),
                "total_trades": safe_int(row.get("TtlNbOfTxsExctd", 0)),
            }
            records_to_insert.append(record)

        inserted_count = bulk_upsert_raw_prices(session, records_to_insert)
        session.commit()
        logger.info(f"Imported {inserted_count} stock raw price records for {trade_date.isoformat()}")
        
        return inserted_count

    async def download_and_import_date(self, session: Session, trade_date: date, etf_symbols: set = None) -> int:
        """
        Download and import the CM bhavcopy for a given date.
        """
        date_str = trade_date.strftime("%Y%m%d")
        logger.info(f"Downloading CM Bhavcopy for date: {trade_date.isoformat()}")

        try:
            df = await self.client.download_bhavcopy_csv(date_str)
        except Exception as e:
            from src.services.nse_client import HttpNotFoundError
            if isinstance(e, HttpNotFoundError):
                logger.info(f"No bhavcopy found for {trade_date.isoformat()} (likely weekend or holiday)")
                return 0
            logger.error(f"Failed to download bhavcopy for {trade_date.isoformat()}: {e}")
            raise e

        # Filter and import
        filtered_df = self.filter_stock_dataframe(df, etf_symbols)
        return await self.import_stock_prices(session, filtered_df, trade_date)
