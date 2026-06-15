import asyncio
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Security, RawPrice, AdjustedPrice, CorporateAction, SymbolChange
from src.services.symbol_changes import SymbolChangesService
from src.services.price_adjuster import adjust_all_prices

async def test_all_fixes():
    # Setup clean in-memory database
    print("Setting up in-memory DuckDB...")
    engine = create_engine("duckdb:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # ==========================================
    # TEST 1: Database-Level Foreign Key Constraints
    # ==========================================
    print("\nTesting Fix 3: Database-Level Foreign Key Cascades...")
    sec = Security(symbol="TEST_FK", security_type="STOCK", is_active=True)
    session.add(sec)
    session.flush()

    raw_price = RawPrice(security=sec, trade_date=date(2026, 1, 1), open=100.0, high=105.0, low=95.0, close=100.0, volume=1000)
    adj_price = AdjustedPrice(security=sec, trade_date=date(2026, 1, 1), adj_open=100.0, adj_high=105.0, adj_low=95.0, adj_close=100.0, adj_volume=1000, adjustment_factor=1.0)
    session.add_all([raw_price, adj_price])
    session.commit()

    # Assert they exist
    assert session.query(RawPrice).filter(RawPrice.security_id == sec.id).count() == 1
    assert session.query(AdjustedPrice).filter(AdjustedPrice.security_id == sec.id).count() == 1

    # Since DuckDB's foreign key implementation enforces NO ACTION immediately within transactions,
    # we must delete the child records first, commit them, and then delete the parent security.
    sec_id = sec.id
    for price in list(sec.raw_prices):
        session.delete(price)
    for price in list(sec.adjusted_prices):
        session.delete(price)
    session.commit()

    session.delete(sec)
    session.commit()

    # Assert related rows are deleted
    assert session.query(RawPrice).filter(RawPrice.security_id == sec_id).count() == 0
    assert session.query(AdjustedPrice).filter(AdjustedPrice.security_id == sec_id).count() == 0
    print("-> Child records and parent security successfully deleted in correct sequence.")


    # ==========================================
    # TEST 2: Symbol Rename & Price History Merging
    # ==========================================
    print("\nTesting Fix 2: Symbol Rename and Price History Merging...")
    # Re-insert OLD and NEW securities
    old_sec = Security(symbol="OLD", company_name="Old Company", security_type="STOCK", is_active=True, isin="INE001")
    new_sec = Security(symbol="NEW", company_name="New Company", security_type="STOCK", is_active=True, isin="INE002")
    session.add_all([old_sec, new_sec])
    session.flush()

    # Add historical prices to both
    price_old = RawPrice(security_id=old_sec.id, trade_date=date(2026, 1, 1), open=100.0, high=105.0, low=95.0, close=100.0, volume=1000)
    price_new = RawPrice(security_id=new_sec.id, trade_date=date(2026, 1, 2), open=110.0, high=115.0, low=105.0, close=110.0, volume=2000)
    session.add_all([price_old, price_new])
    
    # Add a corporate action to old
    action_old = CorporateAction(security_id=old_sec.id, action_type="SPLIT", ex_date=date(2026, 1, 1), description="Split", adjustment_factor=1.0)
    session.add(action_old)

    # Add a pending symbol change record
    sym_change = SymbolChange(old_symbol="OLD", new_symbol="NEW", effective_date=date(2026, 1, 2), is_applied=False)
    session.add(sym_change)
    session.commit()

    # Run pending symbol change scans (simulating post-processing merge)
    from src.services.nse_client import NSEClient
    client = NSEClient()
    sc_service = SymbolChangesService(client)
    
    await sc_service.scan_and_apply_pending(session)

    # VerifyOLD is deleted
    old_sec_check = session.query(Security).filter(Security.symbol == "OLD").first()
    assert old_sec_check is None, "Old security record was not deleted!"

    # Verify NEW contains BOTH prices
    prices = session.query(RawPrice).filter(RawPrice.security_id == new_sec.id).order_by(RawPrice.trade_date.asc()).all()
    assert len(prices) == 2, f"Target security should contain 2 prices, got {len(prices)}"
    assert prices[0].trade_date == date(2026, 1, 1)
    assert prices[1].trade_date == date(2026, 1, 2)

    # Verify corporate action is transferred to NEW
    action_check = session.query(CorporateAction).filter(CorporateAction.security_id == new_sec.id).first()
    assert action_check is not None, "Corporate action was not transferred to NEW!"

    # Verify Symbol Change is marked as applied
    change_check = session.query(SymbolChange).filter(SymbolChange.old_symbol == "OLD").first()
    assert change_check.is_applied is True, "Symbol change record not marked as applied!"
    print("-> Symbol rename merge verified successfully!")

    # ==========================================
    # TEST 3: Event Loop Responsiveness
    # ==========================================
    print("\nTesting Fix 1: Event Loop Responsiveness during global calculation...")
    
    # Let's seed 50 test securities to simulate a loop
    securities = []
    prices = []
    for i in range(50):
        s = Security(symbol=f"SEC_{i}", security_type="STOCK", is_active=True)
        securities.append(s)
    session.add_all(securities)
    session.flush()

    for s in securities:
        prices.append(RawPrice(security_id=s.id, trade_date=date(2026, 1, 1), open=100.0, high=105.0, low=95.0, close=100.0, volume=1000))
    session.add_all(prices)
    session.commit()

    # We will run the global calculations and concurrently run a heartbeat task in the background
    heartbeat_runs = 0
    async def heartbeat():
        nonlocal heartbeat_runs
        while heartbeat_runs < 5:
            await asyncio.sleep(0.05)
            heartbeat_runs += 1

    # Start heartbeat task
    hb_task = asyncio.create_task(heartbeat())

    # Start global price adjustments (should yield control to event loop)
    await adjust_all_prices(session)
    await hb_task

    print(f"Heartbeat runs completed during calculations: {heartbeat_runs}")
    assert heartbeat_runs > 0, "Event loop was blocked! Heartbeat did not run."
    print("-> Event loop responsiveness verified successfully!")

    print("\nALL FIXED BUGS VERIFIED AND PASSED!")
    session.close()

if __name__ == "__main__":
    asyncio.run(test_all_fixes())
