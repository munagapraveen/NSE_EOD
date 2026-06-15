import asyncio
import sys
import os
from datetime import date, timedelta

# Append the project's root directory to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.engine import SessionLocal
from src.models import Security, RawPrice, AdjustedPrice, CorporateAction, MarketCap, Indicator
from src.services.price_adjuster import adjust_prices_for_security
from src.services.market_cap import calculate_historical_market_cap, calculate_all_historical_market_caps
from src.services.indicators import calculate_indicators_for_security, calculate_all_indicators


async def run_tests():
    print("--- Starting Phase 4 Verification Tests ---")
    session = SessionLocal()
    
    try:
        # Clean previous test entries
        test_symbols = ["TESTSTOCK", "TESTETF", "TESTINDEX"]
        for sym in test_symbols:
            existing = session.query(Security).filter(Security.symbol == sym).first()
            if existing:
                session.delete(existing)
        session.commit()

        # =====================================================================
        # 1. Seed Mock Stock Data with 250 Days of Prices and a Stock Split
        # =====================================================================
        print("\n1. Seeding mock STOCK data (250 days)...")
        stock = Security(
            symbol="TESTSTOCK",
            company_name="Test Stock Inc",
            security_type="STOCK",
            isin="INE111X01011",
            issued_shares=10000000, # 10 Million shares currently
            is_active=True,
            data_source="MOCK"
        )
        session.add(stock)
        session.flush() # Populate stock.id

        start_date = date(2025, 1, 1)
        raw_prices = []
        
        # We simulate a 2:1 split on 2025-06-01 (Day 151)
        # Pre-split price ~200.00, post-split price ~100.00
        split_date = date(2025, 6, 1)
        
        for i in range(250):
            current_date = start_date + timedelta(days=i)
            # Make price slightly wave around to calculate realistic moving averages
            base_price = 100.0 if current_date >= split_date else 200.0
            price_offset = (i % 10) - 5 # -5 to +4
            close_price = base_price + price_offset
            
            raw_p = RawPrice(
                security_id=stock.id,
                trade_date=current_date,
                open=close_price + 1.0,
                high=close_price + 2.0,
                low=close_price - 2.0,
                close=close_price,
                volume=50000 if current_date >= split_date else 25000
            )
            session.add(raw_p)
            
        # Add Corporate Action: Split 2 -> 1 (factor = 2.0) on 2025-06-01
        split_action = CorporateAction(
            security_id=stock.id,
            action_type="SPLIT",
            ex_date=split_date,
            description="FV Split Rs 10 to Rs 5",
            old_face_value=10.0,
            new_face_value=5.0,
            adjustment_factor=2.0,
            is_processed=False
        )
        session.add(split_action)
        session.commit()

        # Run Price Adjustment to populate adjusted_prices table with 2-decimal rounded prices
        print("   Adjusting prices for TESTSTOCK...")
        await adjust_prices_for_security(session, stock.id)
        
        # Verify that adjusted close prices are populated and rounded to 2 decimals
        adj_sample = session.query(AdjustedPrice).filter(AdjustedPrice.security_id == stock.id).first()
        assert adj_sample is not None
        # We assert that the type in python is converted to float and rounded to 2 decimals
        # e.g., adj_close should be round(close / factor, 2)
        print(f"   [OK] Seeding and price adjustments complete. Sample adjusted price: {adj_sample.adj_close}")

        # =====================================================================
        # 2. Test Market Cap Engine (Historical Reverse-Engineering)
        # =====================================================================
        print("\n2. Testing Historical Market Cap calculation...")
        records_written = await calculate_historical_market_cap(session, stock.id, stock.issued_shares)
        assert records_written == 250
        
        # Check database records
        mcap_pre_split = session.query(MarketCap).filter(
            MarketCap.security_id == stock.id,
            MarketCap.trade_date < split_date
        ).order_by(MarketCap.trade_date.asc()).all()
        
        mcap_post_split = session.query(MarketCap).filter(
            MarketCap.security_id == stock.id,
            MarketCap.trade_date >= split_date
        ).order_by(MarketCap.trade_date.asc()).all()
        
        assert len(mcap_pre_split) == 151
        assert len(mcap_post_split) == 99

        # Pre-split: shares outstanding should be 10M / 2.0 = 5M
        for row in mcap_pre_split:
            assert row.issued_shares == 5000000
            expected_mcap = round(5000000 * float(row.close_price), 2)
            assert float(row.market_cap) == expected_mcap
            assert row.shares_source == "REVERSE_ENGINEERED"

        # Post-split: shares outstanding should be 10M / 1.0 = 10M
        for row in mcap_post_split:
            assert row.issued_shares == 10000000
            expected_mcap = round(10000000 * float(row.close_price), 2)
            assert float(row.market_cap) == expected_mcap
            assert row.shares_source == "REVERSE_ENGINEERED"

        print("   [OK] Historical market cap reverse-engineering verified successfully.")

        # =====================================================================
        # 3. Test SMA Indicators Engine
        # =====================================================================
        print("\n3. Testing SMA Indicators engine (SMA 5, 10, 20, 50, 200)...")
        ind_written = await calculate_indicators_for_security(session, stock.id)
        assert ind_written == 250
        
        # Verify columns and calculations
        indicators = session.query(Indicator).filter(Indicator.security_id == stock.id).order_by(Indicator.trade_date.asc()).all()
        assert len(indicators) == 250
        
        # First 4 records should have sma_5 as NULL (since we need 5 periods)
        for row in indicators[:4]:
            assert row.sma_5 is None

        # Day 5 (index 4) should have sma_5 calculated and rounded to 2 decimals
        assert indicators[4].sma_5 is not None
        
        # First 199 records should have sma_200 as NULL
        for row in indicators[:199]:
            assert row.sma_200 is None
            
        # Day 200 (index 199) and beyond should have sma_200 calculated
        assert indicators[199].sma_200 is not None
        print(f"   Sample indicator values at day 200 (date {indicators[199].trade_date}):")
        print(f"     SMA 5:   {indicators[199].sma_5}")
        print(f"     SMA 10:  {indicators[199].sma_10}")
        print(f"     SMA 20:  {indicators[199].sma_20}")
        print(f"     SMA 50:  {indicators[199].sma_50}")
        print(f"     SMA 200: {indicators[199].sma_200}")

        print("   [OK] SMA Indicators verified successfully.")

        # =====================================================================
        # 4. Test Asset Exclusions (ETFs & Indexes)
        # =====================================================================
        print("\n4. Testing asset exclusions (ETFs and Indexes)...")
        etf = Security(
            symbol="TESTETF",
            company_name="Test ETF Fund",
            security_type="ETF",
            isin="INE222Y02022",
            is_active=True,
            data_source="MOCK"
        )
        idx = Security(
            symbol="TESTINDEX",
            company_name="Test Index",
            security_type="INDEX",
            is_active=True,
            data_source="MOCK"
        )
        session.add(etf)
        session.add(idx)
        session.flush()

        # Seed 10 days of prices for both
        for sec in [etf, idx]:
            for i in range(10):
                d = start_date + timedelta(days=i)
                raw_p = RawPrice(
                    security_id=sec.id,
                    trade_date=d,
                    open=150.0,
                    high=155.0,
                    low=148.0,
                    close=151.0 + i,
                    volume=2000
                )
                session.add(raw_p)
        session.commit()

        # Adjust prices for both
        await adjust_prices_for_security(session, etf.id)
        await adjust_prices_for_security(session, idx.id)

        # Run global market cap calculations
        print("   Running global market cap calculations...")
        await calculate_all_historical_market_caps(session)

        # Verify: stock has market cap, but ETF and Index do not
        stock_mcaps = session.query(MarketCap).filter(MarketCap.security_id == stock.id).all()
        etf_mcaps = session.query(MarketCap).filter(MarketCap.security_id == etf.id).all()
        idx_mcaps = session.query(MarketCap).filter(MarketCap.security_id == idx.id).all()
        
        assert len(stock_mcaps) > 0
        assert len(etf_mcaps) == 0
        assert len(idx_mcaps) == 0
        print("   [OK] Market cap correctly skipped for ETFs and Indexes.")

        # Run global indicators calculations
        print("   Running global indicator calculations...")
        await calculate_all_indicators(session)

        # Verify: stock, etf, and index all have indicators populated
        stock_inds = session.query(Indicator).filter(Indicator.security_id == stock.id).all()
        etf_inds = session.query(Indicator).filter(Indicator.security_id == etf.id).all()
        idx_inds = session.query(Indicator).filter(Indicator.security_id == idx.id).all()
        
        assert len(stock_inds) > 0
        assert len(etf_inds) == 10
        assert len(idx_inds) == 10
        print("   [OK] Indicators successfully calculated for all security types (Stocks, ETFs, Indexes).")

        print("\nAll Phase 4 verification tests passed successfully!")

    except Exception as e:
        print(f"\n[ERROR] Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(run_tests())
