import asyncio
import sys
import os
from datetime import date

# Append the project's root directory to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.engine import SessionLocal
from src.services.nse_client import NSEClient
from src.services.stock_downloader import StockDownloader
from src.services.etf_downloader import ETFDownloader
from src.services.index_downloader import IndexDownloader
from src.models import Security, RawPrice
from sqlalchemy import select, func
from loguru import logger


async def test_downloads():
    # February 1, 2025 was a Saturday but trading was active due to the Union Budget presentation.
    test_date = date(2025, 2, 1)
    
    client = NSEClient()
    session = SessionLocal()
    
    try:
        print(f"--- Starting Download Test for Budget Day: {test_date.isoformat()} ---")
        
        # 1. Sync ETF Master first so we can separate ETFs from stocks
        etf_downloader = ETFDownloader(client)
        print("\n1. Seeding ETF master list...")
        etf_master_count = await etf_downloader.sync_etf_master_list(session)
        print(f"   Seed complete: {etf_master_count} ETFs registered.")
        etf_symbols = await etf_downloader.get_all_etf_symbols(session)
        
        # Download the bhavcopy ZIP once
        date_str = test_date.strftime("%Y%m%d")
        print(f"\nDownloading Bhavcopy ZIP for date {test_date.isoformat()} once...")
        bhavcopy_df = await client.download_bhavcopy_csv(date_str)
        
        # 2. Filter and import Stock prices
        stock_downloader = StockDownloader(client)
        print("\n2. Filtering and importing stock prices from bhavcopy...")
        filtered_stocks_df = stock_downloader.filter_stock_dataframe(bhavcopy_df, etf_symbols=etf_symbols)
        stock_price_count = await stock_downloader.import_stock_prices(session, filtered_stocks_df, test_date)
        print(f"   Import complete: {stock_price_count} stock prices saved.")
        
        # 3. Filter and import ETF prices
        print("\n3. Filtering and importing ETF prices from bhavcopy...")
        filtered_etfs_df = etf_downloader.filter_etf_dataframe(bhavcopy_df, etf_symbols=etf_symbols)
        etf_price_count = await etf_downloader.import_etf_prices(session, filtered_etfs_df, test_date)
        print(f"   Import complete: {etf_price_count} ETF prices saved.")

        # 4. Download Indices
        index_downloader = IndexDownloader(client)
        print("\n4. Downloading and importing index closing prices...")
        index_price_count = await index_downloader.download_and_import_date(session, test_date)
        print(f"   Import complete: {index_price_count} index prices saved.")

        # 5. Database Verification
        print("\n--- Database Seeding Verification (Unified Schema) ---")
        
        # Count Stocks discovered
        total_stocks = session.execute(
            select(func.count(Security.id))
            .where(Security.security_type == "STOCK")
        ).scalar()
        print(f"  Total Auto-Discovered Stocks in DB: {total_stocks}")
        
        # Verify specific stock (e.g. RELIANCE)
        reliance = session.execute(
            select(Security)
            .where(Security.symbol == "RELIANCE")
            .where(Security.security_type == "STOCK")
        ).scalar_one_or_none()
        
        if reliance:
            print(f"  [OK] Found RELIANCE (ISIN: {reliance.isin}) in Securities master.")
            reliance_price = session.execute(
                select(RawPrice)
                .where(RawPrice.security_id == reliance.id)
                .where(RawPrice.trade_date == test_date)
            ).scalar_one_or_none()
            if reliance_price:
                print(f"  [OK] Found RELIANCE price on {test_date}: Open={reliance_price.open}, Close={reliance_price.close}, Volume={reliance_price.volume}")
            else:
                print("  [ERROR] RELIANCE price record missing.")
        else:
            print("  [ERROR] RELIANCE stock not found in master.")

        # Verify Index case-insensitively
        nifty50 = session.execute(
            select(Security)
            .where(func.upper(Security.symbol) == "NIFTY 50")
            .where(Security.security_type == "INDEX")
        ).scalar_one_or_none()
        
        if nifty50:
            print(f"  [OK] Found NIFTY 50 index master: symbol='{nifty50.symbol}'.")
            nifty_price = session.execute(
                select(RawPrice)
                .where(RawPrice.security_id == nifty50.id)
                .where(RawPrice.trade_date == test_date)
            ).scalar_one_or_none()
            if nifty_price:
                print(f"  [OK] Found NIFTY 50 price on {test_date}: Open={nifty_price.open}, Close={nifty_price.close}, Volume={nifty_price.volume}")
            else:
                print("  [ERROR] NIFTY 50 price record missing.")
        else:
            print("  [ERROR] NIFTY 50 index not found in master.")

        # Verify ETF
        some_etf = session.execute(
            select(Security)
            .where(Security.security_type == "ETF")
        ).first()
        
        if some_etf:
            some_etf = some_etf[0]
            print(f"  [OK] Found ETF master example: {some_etf.symbol} ({some_etf.company_name})")
            etf_price = session.execute(
                select(RawPrice)
                .where(RawPrice.security_id == some_etf.id)
                .where(RawPrice.trade_date == test_date)
            ).scalar_one_or_none()
            if etf_price:
                print(f"  [OK] Found price for {some_etf.symbol} on {test_date}: Close={etf_price.close}, Volume={etf_price.volume}")
            else:
                print(f"  [INFO] Price for ETF {some_etf.symbol} not active/found in this day's bhavcopy.")
        else:
            print("  [ERROR] ETF master list is empty.")
            
        print("\nAll download and import functions passed successfully!")

    except Exception:
        logger.exception("Test failed with exception:")
        sys.exit(1)
    finally:
        session.close()
        await client.close()


if __name__ == "__main__":
    asyncio.run(test_downloads())
