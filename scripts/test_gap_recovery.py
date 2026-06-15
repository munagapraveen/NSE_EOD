"""
Test: Post-Processing Gap Detection & Recovery

Test 1 - No gaps (clean state):
  GIVEN: Raw prices, adjusted prices, and indicators all have equal record counts
  WHEN:  _detect_post_processing_gaps() is called
  THEN:  has_adj_gap=False, has_ind_gap=False

Test 2 - Adjusted price gap:
  GIVEN: 10 raw price records exist but only 5 adjusted price records
  WHEN:  _detect_post_processing_gaps() is called
  THEN:  has_adj_gap=True

Test 3 - Indicator gap:
  GIVEN: 10 adjusted price records exist but only 5 indicator records
  WHEN:  _detect_post_processing_gaps() is called
  THEN:  has_ind_gap=True

Test 4 - is_incremental forced to False on gaps:
  GIVEN: Gaps detected
  WHEN:  The sync pipeline evaluates post-processing mode
  THEN:  is_incremental is forced to False (global recalculation)
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Security, RawPrice, AdjustedPrice, Indicator

# Setup in-memory DuckDB
engine = create_engine("duckdb:///:memory:", echo=False)
Base.metadata.create_all(bind=engine)
Session = sessionmaker(bind=engine)
session = Session()

# Create a test security
sec = Security(symbol="TEST", security_type="STOCK", is_active=True, is_delisted=False)
session.add(sec)
session.commit()

# Import gap detection method
from src.services.sync_manager import SyncManager

# We need a dummy SyncManager - pass None for client since we won't use it
sm = SyncManager.__new__(SyncManager)

print("=" * 60)
print("TEST 1: No data at all (empty state)")
print("=" * 60)
gaps = sm._detect_post_processing_gaps(session)
assert gaps["raw_count"] == 0
assert gaps["adj_count"] == 0
assert gaps["ind_count"] == 0
assert gaps["has_adj_gap"] == False
assert gaps["has_ind_gap"] == False
print(f"  [PASS] Empty state: raw={gaps['raw_count']}, adj={gaps['adj_count']}, ind={gaps['ind_count']}")

print()
print("=" * 60)
print("TEST 2: Raw prices exist, no adjusted prices (gap)")
print("=" * 60)

# Add raw prices for 5 dates
for i in range(5):
    rp = RawPrice(
        security_id=sec.id,
        trade_date=date(2025, 1, 1 + i),
        open=100, high=105, low=95, close=102, volume=1000
    )
    session.add(rp)
session.commit()

gaps = sm._detect_post_processing_gaps(session)
assert gaps["raw_count"] == 5
assert gaps["adj_count"] == 0
assert gaps["has_adj_gap"] == True, f"Expected adj gap, got {gaps}"
print(f"  [PASS] Adj gap detected: raw={gaps['raw_count']}, adj={gaps['adj_count']}")

print()
print("=" * 60)
print("TEST 3: Adjusted prices added (partial - gap remains)")
print("=" * 60)

# Add adjusted prices for only 3 out of 5 dates
for i in range(3):
    ap = AdjustedPrice(
        security_id=sec.id,
        trade_date=date(2025, 1, 1 + i),
        adj_open=100, adj_high=105, adj_low=95, adj_close=102, adj_volume=1000,
        adjustment_factor=1.0
    )
    session.add(ap)
session.commit()

gaps = sm._detect_post_processing_gaps(session)
assert gaps["raw_count"] == 5
assert gaps["adj_count"] == 3
assert gaps["has_adj_gap"] == True
print(f"  [PASS] Partial adj gap: raw={gaps['raw_count']}, adj={gaps['adj_count']}")

print()
print("=" * 60)
print("TEST 4: All adjusted prices filled, no indicators (ind gap)")
print("=" * 60)

# Fill remaining 2 adjusted prices
for i in range(3, 5):
    ap = AdjustedPrice(
        security_id=sec.id,
        trade_date=date(2025, 1, 1 + i),
        adj_open=100, adj_high=105, adj_low=95, adj_close=102, adj_volume=1000,
        adjustment_factor=1.0
    )
    session.add(ap)
session.commit()

gaps = sm._detect_post_processing_gaps(session)
assert gaps["raw_count"] == 5
assert gaps["adj_count"] == 5
assert gaps["ind_count"] == 0
assert gaps["has_adj_gap"] == False
assert gaps["has_ind_gap"] == True
print(f"  [PASS] Ind gap detected: adj={gaps['adj_count']}, ind={gaps['ind_count']}")

print()
print("=" * 60)
print("TEST 5: All indicators filled (no gaps)")
print("=" * 60)

for i in range(5):
    ind = Indicator(
        security_id=sec.id,
        trade_date=date(2025, 1, 1 + i),
        sma_5=100.0
    )
    session.add(ind)
session.commit()

gaps = sm._detect_post_processing_gaps(session)
assert gaps["raw_count"] == 5
assert gaps["adj_count"] == 5
assert gaps["ind_count"] == 5
assert gaps["has_adj_gap"] == False
assert gaps["has_ind_gap"] == False
print(f"  [PASS] No gaps: raw={gaps['raw_count']}, adj={gaps['adj_count']}, ind={gaps['ind_count']}")

print()
print("=" * 60)
print("TEST 6: is_incremental forced to False when gaps exist")
print("=" * 60)

# Simulate: add raw price for new date without adjusted price
rp = RawPrice(
    security_id=sec.id,
    trade_date=date(2025, 1, 10),
    open=110, high=115, low=105, close=112, volume=1500
)
session.add(rp)
session.commit()

# Simulate the pipeline logic
has_history = True
is_incremental = has_history and True  # Normally would be True for small range

gaps = sm._detect_post_processing_gaps(session)
if gaps["has_adj_gap"] or gaps["has_ind_gap"]:
    is_incremental = False

assert is_incremental == False, f"Expected is_incremental=False, got {is_incremental}"
print(f"  [PASS] is_incremental forced to False (gaps: adj={gaps['has_adj_gap']}, ind={gaps['has_ind_gap']})")

session.close()
engine.dispose()

print()
print("=" * 60)
print("ALL 6 TESTS PASSED")
print("=" * 60)
