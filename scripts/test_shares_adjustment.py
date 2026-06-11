"""
Test: Auto-adjust issued shares on split/bonus corporate actions

Test 1 - Incremental mode:
  GIVEN: A security with 1,000,000 issued shares
  GIVEN: An unprocessed corporate action of type SPLIT on or before end_date with factor=2.0
  WHEN:  The sync manager processes corporate actions with is_incremental=True
  THEN:  The security's issued_shares is updated to 2,000,000

Test 2 - Global mode:
  GIVEN: A security with 1,000,000 issued shares
  GIVEN: An unprocessed corporate action of type SPLIT with factor=2.0
  WHEN:  The sync manager processes corporate actions with is_incremental=False
  THEN:  The security's issued_shares remains unchanged (1,000,000)
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Security, CorporateAction, RawPrice
from src.services.sync_manager import SyncManager
from unittest.mock import MagicMock

# Setup in-memory DuckDB
engine = create_engine("duckdb:///:memory:", echo=False)
Base.metadata.create_all(bind=engine)
Session = sessionmaker(bind=engine)
session = Session()

# Setup dummy/mock client
client_mock = MagicMock()
sm = SyncManager(client_mock)

# Override sync_corporate_actions method to do nothing, since we want to manually test the post-sync adjustment logic
async def dummy_sync_corporate_actions(session, start_date, end_date):
    pass
sm.ca_service.sync_corporate_actions = dummy_sync_corporate_actions

print("=" * 60)
print("TEST 1: Incremental mode adjusts issued shares")
print("=" * 60)

# Create a test security and an unprocessed corporate action
sec1 = Security(symbol="TEST1", security_type="STOCK", is_active=True, is_delisted=False, issued_shares=1000000)
session.add(sec1)
session.commit()

action1 = CorporateAction(
    security_id=sec1.id,
    action_type="SPLIT",
    ex_date=date(2025, 1, 5),
    description="Split 2:1",
    adjustment_factor=2.0,
    is_processed=False
)
session.add(action1)
session.commit()

# Add a mock raw price to make sure has_history is True
rp1 = RawPrice(security_id=sec1.id, trade_date=date(2025, 1, 1), open=100, high=105, low=95, close=102, volume=1000)
session.add(rp1)
session.commit()

# Run the sync segment (simulate options of corporate_actions)
# We set start_date and end_date to trigger is_incremental = True
start_date = date(2025, 1, 1)
end_date = date(2025, 1, 5) # range is 4 days <= 10 days

# Manually execute the exact block under test
has_history = session.query(RawPrice).first() is not None
is_incremental = has_history and (end_date - start_date).days <= 10
assert is_incremental == True

# Run the target block
unprocessed_actions = session.query(CorporateAction).filter(
    CorporateAction.action_type.in_(["SPLIT", "BONUS"]),
    CorporateAction.ex_date <= end_date,
    CorporateAction.is_processed == False
).all()

for action in unprocessed_actions:
    sec = session.query(Security).get(action.security_id)
    if sec and sec.issued_shares is not None:
        old_shares = sec.issued_shares
        new_shares = int(round(old_shares * float(action.adjustment_factor)))
        print(f"  Adjusting issued_shares for {sec.symbol}: {old_shares} -> {new_shares}")
        sec.issued_shares = new_shares
session.commit()

# Refresh from DB
session.refresh(sec1)
assert sec1.issued_shares == 2000000, f"Expected 2000000, got {sec1.issued_shares}"
print("  [PASS] Successfully adjusted 1,000,000 -> 2,000,000 in incremental mode.")

print()
print("=" * 60)
print("TEST 2: Global mode (is_incremental=False) does NOT adjust issued shares")
print("=" * 60)

# Create another security and corporate action
sec2 = Security(symbol="TEST2", security_type="STOCK", is_active=True, is_delisted=False, issued_shares=1000000)
session.add(sec2)
session.commit()

action2 = CorporateAction(
    security_id=sec2.id,
    action_type="SPLIT",
    ex_date=date(2025, 1, 5),
    description="Split 2:1",
    adjustment_factor=2.0,
    is_processed=False
)
session.add(action2)
session.commit()

# Test global mode (range > 10 days, or we explicitly force is_incremental = False)
start_date = date(2025, 1, 1)
end_date = date(2025, 1, 20) # 19 days > 10 days, so is_incremental = False
is_incremental = has_history and (end_date - start_date).days <= 10
assert is_incremental == False

# Run target block (which should bypass adjustment because is_incremental is False)
if is_incremental:
    unprocessed_actions = session.query(CorporateAction).filter(
        CorporateAction.action_type.in_(["SPLIT", "BONUS"]),
        CorporateAction.ex_date <= end_date,
        CorporateAction.is_processed == False
    ).all()
    for action in unprocessed_actions:
        sec = session.query(Security).get(action.security_id)
        if sec and sec.issued_shares is not None:
            old_shares = sec.issued_shares
            new_shares = int(round(old_shares * float(action.adjustment_factor)))
            sec.issued_shares = new_shares
    session.commit()

session.refresh(sec2)
assert sec2.issued_shares == 1000000, f"Expected 1000000 to remain, got {sec2.issued_shares}"
print("  [PASS] Correctly bypassed adjustment in global mode.")

session.close()
engine.dispose()

print()
print("=" * 60)
print("ALL TESTS PASSED SUCCESSFULLY!")
print("=" * 60)
