import os
import sys
import glob
import pytest
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from src.models import Base, Security, RawPrice
from src.utils.backup_utils import create_db_backup, prune_old_backups
from scripts.db_recovery import run_db_rebuild

def test_database_resilience():
    # Store original configuration
    original_db_url = settings.database_url
    
    # Define paths for testing
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    test_db_path = os.path.join(project_dir, "data", "test_market.db")
    test_db_url = f"duckdb:///{test_db_path}"
    
    # Ensure clean state for test
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        
    backup_dir = os.path.join(project_dir, "backups")
    # Clean existing test backups
    for f in glob.glob(os.path.join(backup_dir, "market_backup_*.db")):
        try:
            os.remove(f)
        except:
            pass

    print("\n[TEST] 1. Initializing test database schema...")
    # Update settings to point to test database
    settings.database_url = test_db_url
    
    # Create engine and tables
    test_engine = create_engine(test_db_url)
    Base.metadata.create_all(bind=test_engine)
    
    # Create session
    SessionLocal = sessionmaker(bind=test_engine)
    session = SessionLocal()
    
    print("[TEST] 2. Seeding test database records...")
    # Add dummy securities
    sec1 = Security(symbol="TEST1", company_name="Test Company 1", security_type="STOCK", isin="INE000000001", is_active=True)
    sec2 = Security(symbol="TEST2", company_name="Test Company 2", security_type="STOCK", isin="INE000000002", is_active=True)
    sec3 = Security(symbol="TEST3", company_name="Test Company 3", security_type="STOCK", isin="INE000000003", is_active=True)
    session.add_all([sec1, sec2, sec3])
    session.commit()
    
    # Add raw prices
    p1 = RawPrice(security_id=sec1.id, trade_date=date(2025, 1, 1), open=100.0, high=105.0, low=99.0, close=102.0, volume=10000)
    p2 = RawPrice(security_id=sec2.id, trade_date=date(2025, 1, 1), open=200.0, high=210.0, low=198.0, close=205.0, volume=20000)
    session.add_all([p1, p2])
    session.commit()
    
    # Fetch counts
    original_securities_count = session.query(Security).count()
    original_prices_count = session.query(RawPrice).count()
    assert original_securities_count == 3
    assert original_prices_count == 2
    print(f"  [OK] Seeded {original_securities_count} securities and {original_prices_count} prices successfully.")
    
    # Close session and engine so files are unlocked for backups
    session.close()
    test_engine.dispose()
    
    print("[TEST] 3. Testing automated backups and pruning...")
    # Trigger 3 backups
    b1 = create_db_backup()
    b2 = create_db_backup()
    b3 = create_db_backup()
    
    assert os.path.exists(b1)
    assert os.path.exists(b2)
    assert os.path.exists(b3)
    print("  [OK] Backup files successfully created.")
    
    # Prune keeping only 2
    prune_old_backups(keep_count=2)
    remaining_backups = glob.glob(os.path.join(backup_dir, "market_backup_*.db"))
    assert len(remaining_backups) == 2
    print("  [OK] Pruned old backups correctly (retained exactly 2).")
    
    print("[TEST] 4. Testing database recovery and index rebuild...")
    # Run the rebuild script on the test database
    rebuild_success = run_db_rebuild(db_path=test_db_path)
    assert rebuild_success is True
    print("  [OK] Rebuild tool completed successfully.")
    
    # Reopen and check data consistency
    test_engine = create_engine(test_db_url)
    SessionLocal = sessionmaker(bind=test_engine)
    session = SessionLocal()
    
    rebuilt_securities_count = session.query(Security).count()
    rebuilt_prices_count = session.query(RawPrice).count()
    assert rebuilt_securities_count == 3
    assert rebuilt_prices_count == 2
    print(f"  [OK] Data verified. Securities count: {rebuilt_securities_count}, Prices count: {rebuilt_prices_count}")
    
    # Verify unique constraint is active by attempting to insert a duplicate ISIN
    print("[TEST] 5. Verifying database unique constraint integrity...")
    duplicate_sec = Security(symbol="DUP", company_name="Duplicate ISIN", security_type="STOCK", isin="INE000000001")
    session.add(duplicate_sec)
    
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError) as excinfo:
        session.commit()
    print("  [OK] Correctly caught unique constraint violation on duplicate ISIN.")
    session.rollback()
    
    # Clean up test session and engine
    session.close()
    test_engine.dispose()
    
    # Restore settings database URL
    settings.database_url = original_db_url
    
    # Delete temporary files
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    for f in remaining_backups:
        if os.path.exists(f):
            os.remove(f)
            
    print("\n[ALL TESTS PASSED SUCCESSFULLY!]")

if __name__ == "__main__":
    test_database_resilience()
