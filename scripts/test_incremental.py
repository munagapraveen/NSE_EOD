import asyncio
import time
from datetime import date, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.models import Base, Security, RawPrice, AdjustedPrice, MarketCap, Indicator, CorporateAction
from src.services.price_adjuster import adjust_incremental_prices, adjust_all_prices
from src.services.market_cap import calculate_incremental_market_caps_for_range, calculate_all_historical_market_caps
from src.services.indicators import calculate_incremental_indicators_for_range, calculate_all_indicators

async def test_incremental_processing():
    print("Initializing in-memory database for testing...")
    # Use in-memory DuckDB for fast and non-locking tests
    engine = create_engine("duckdb:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    print("Seeding test securities...")
    infy = Security(
        symbol="INFY",
        company_name="Infosys Limited",
        security_type="STOCK",
        is_active=True,
        is_delisted=False,
        issued_shares=1000000,
        isin="INE009A01021"
    )
    tcs = Security(
        symbol="TCS",
        company_name="Tata Consultancy Services Limited",
        security_type="STOCK",
        is_active=True,
        is_delisted=False,
        issued_shares=2000000,
        isin="INE467B01029"
    )
    session.add_all([infy, tcs])
    session.commit()

    # Generate 220 historical days
    start_history = date(2025, 6, 1)
    end_history = date(2026, 5, 31)
    
    print("Generating 220 days of historical raw prices...")
    current_date = start_history
    raw_prices = []
    
    while current_date <= end_history:
        # Avoid weekends
        if current_date.weekday() < 5:
            # INFY raw price (around 1000)
            raw_prices.append(RawPrice(
                security_id=infy.id,
                trade_date=current_date,
                open=1000.0,
                high=1020.0,
                low=990.0,
                close=1000.0,
                volume=10000
            ))
            # TCS raw price (around 2000)
            raw_prices.append(RawPrice(
                security_id=tcs.id,
                trade_date=current_date,
                open=2000.0,
                high=2040.0,
                low=1980.0,
                close=2000.0,
                volume=5000
            ))
        current_date += timedelta(days=1)
        
    session.add_all(raw_prices)
    session.commit()

    print("Running initial global post-processing...")
    await adjust_all_prices(session)
    await calculate_all_historical_market_caps(session)
    await calculate_all_indicators(session)

    # Let's verify we have initial data
    adj_count = session.query(AdjustedPrice).count()
    mcap_count = session.query(MarketCap).count()
    ind_count = session.query(Indicator).count()
    print(f"Initial DB counts: AdjustedPrice={adj_count}, MarketCap={mcap_count}, Indicator={ind_count}")
    assert adj_count > 0, "No adjusted price records created!"
    assert mcap_count > 0, "No market cap records created!"
    assert ind_count > 0, "No indicator records created!"

    # Now add some corporate action and new dates to test incremental path
    print("\nAdding corporate action for INFY...")
    # Add a 2-for-1 Split for INFY ex-dating on 2026-06-03
    split_action = CorporateAction(
        security_id=infy.id,
        ex_date=date(2026, 6, 3),
        action_type="SPLIT",
        description="Split 2:1",
        adjustment_factor=2.0,
        is_processed=False
    )
    session.add(split_action)
    session.commit()

    print("Seeding new daily data (2026-06-01 to 2026-06-05)...")
    new_dates = [
        date(2026, 6, 1),
        date(2026, 6, 2),
        date(2026, 6, 3),  # INFY Split ex-date
        date(2026, 6, 4),
        date(2026, 6, 5)
    ]
    new_prices = []
    for d in new_dates:
        # INFY raw price (remains around 1000 raw; post-split it represents split price)
        new_prices.append(RawPrice(
            security_id=infy.id,
            trade_date=d,
            open=1000.0,
            high=1020.0,
            low=990.0,
            close=1000.0,
            volume=10000
        ))
        # TCS raw price
        new_prices.append(RawPrice(
            security_id=tcs.id,
            trade_date=d,
            open=2000.0,
            high=2040.0,
            low=1980.0,
            close=2000.0,
            volume=5000
        ))
    session.add_all(new_prices)
    session.commit()

    # Time incremental run
    print("\nExecuting Incremental Calculations for 2026-06-01 to 2026-06-05...")
    start_time = time.time()
    
    # 1. Price Adjustment
    await adjust_incremental_prices(session, date(2026, 6, 1), date(2026, 6, 5))
    # 2. Market Cap
    await calculate_incremental_market_caps_for_range(session, date(2026, 6, 1), date(2026, 6, 5))
    # 3. Indicators
    await calculate_incremental_indicators_for_range(session, date(2026, 6, 1), date(2026, 6, 5))
    
    elapsed = time.time() - start_time
    print(f"Incremental execution finished in {elapsed:.4f} seconds!")

    # Verify Correctness
    print("\nVerifying Correctness...")
    
    # INFY checks
    # For dates before 2026-06-03, INFY adjusted close should be halved (close = 1000.0 / 2.0 = 500.0)
    # Because of the 2:1 split on 2026-06-03
    sample_pre_split = session.execute(
        select(AdjustedPrice)
        .where(AdjustedPrice.security_id == infy.id)
        .where(AdjustedPrice.trade_date == date(2026, 5, 29))
    ).scalar_one()
    print(f"INFY Pre-Split on 2026-05-29: adj_close={sample_pre_split.adj_close}, adjustment_factor={sample_pre_split.adjustment_factor}")
    assert float(sample_pre_split.adj_close) == 500.0, "INFY adjusted price before split ex-date is incorrect!"
    assert float(sample_pre_split.adjustment_factor) == 2.0, "INFY adjustment factor is incorrect!"

    # For dates on or after 2026-06-03, INFY adjusted close should be equal to raw close (close = 1000.0 / 1.0 = 1000.0)
    sample_post_split = session.execute(
        select(AdjustedPrice)
        .where(AdjustedPrice.security_id == infy.id)
        .where(AdjustedPrice.trade_date == date(2026, 6, 4))
    ).scalar_one()
    print(f"INFY Post-Split on 2026-06-04: adj_close={sample_post_split.adj_close}, adjustment_factor={sample_post_split.adjustment_factor}")
    assert float(sample_post_split.adj_close) == 1000.0, "INFY adjusted price on/after split ex-date is incorrect!"
    assert float(sample_post_split.adjustment_factor) == 1.0, "INFY adjustment factor is incorrect!"

    # TCS checks (no split, adjustment factor should be 1.0 throughout)
    sample_tcs = session.execute(
        select(AdjustedPrice)
        .where(AdjustedPrice.security_id == tcs.id)
        .where(AdjustedPrice.trade_date == date(2026, 6, 4))
    ).scalar_one()
    print(f"TCS on 2026-06-04: adj_close={sample_tcs.adj_close}, adjustment_factor={sample_tcs.adjustment_factor}")
    assert float(sample_tcs.adj_close) == 2000.0, "TCS adjusted price is incorrect!"
    assert float(sample_tcs.adjustment_factor) == 1.0, "TCS adjustment factor is incorrect!"

    # Market Cap checks
    # TCS market cap on 2026-06-04: shares = 2,000,000, close = 2000.0, mcap = 4,000,000,000.00
    tcs_mcap = session.execute(
        select(MarketCap)
        .where(MarketCap.security_id == tcs.id)
        .where(MarketCap.trade_date == date(2026, 6, 4))
    ).scalar_one()
    print(f"TCS Market Cap on 2026-06-04: {tcs_mcap.market_cap} (shares: {tcs_mcap.issued_shares})")
    assert tcs_mcap.market_cap == 400.00, "TCS Market Cap is incorrect!"

    # INFY market cap on 2026-06-04 (post split): shares = 1,000,000, close = 1000.0, mcap = 1,000,000,000.00 / 10,000,000 = 100.00 Crores
    infy_mcap_post = session.execute(
        select(MarketCap)
        .where(MarketCap.security_id == infy.id)
        .where(MarketCap.trade_date == date(2026, 6, 4))
    ).scalar_one()
    print(f"INFY Post-Split Market Cap on 2026-06-04: {infy_mcap_post.market_cap} (shares: {infy_mcap_post.issued_shares})")
    assert infy_mcap_post.market_cap == 100.00, "INFY Post-Split Market Cap is incorrect!"

    # INFY market cap on 2026-05-29 (pre split): shares = 1,000,000 / 2 = 500,000, close = 1000.0, mcap = 500,000,000.00 / 10,000,000 = 50.00 Crores
    infy_mcap_pre = session.execute(
        select(MarketCap)
        .where(MarketCap.security_id == infy.id)
        .where(MarketCap.trade_date == date(2026, 5, 29))
    ).scalar_one()
    print(f"INFY Pre-Split Market Cap on 2026-05-29: {infy_mcap_pre.market_cap} (shares: {infy_mcap_pre.issued_shares})")
    assert infy_mcap_pre.market_cap == 50.00, "INFY Pre-Split Market Cap is incorrect!"

    # Indicator checks
    # TCS SMA 5 on 2026-06-05 should be 2000.0 (since all history is 2000)
    tcs_ind = session.execute(
        select(Indicator)
        .where(Indicator.security_id == tcs.id)
        .where(Indicator.trade_date == date(2026, 6, 5))
    ).scalar_one()
    print(f"TCS Indicators on 2026-06-05: SMA 5={tcs_ind.sma_5}, SMA 200={tcs_ind.sma_200}")
    assert float(tcs_ind.sma_5) == 2000.0, "TCS SMA 5 calculation is incorrect!"
    assert float(tcs_ind.sma_200) == 2000.0, "TCS SMA 200 calculation is incorrect!"

    print("\nALL TESTS PASSED SUCCESSFULLY!")
    session.close()

if __name__ == "__main__":
    asyncio.run(test_incremental_processing())
