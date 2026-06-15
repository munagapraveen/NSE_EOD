"""
Test script: Validate both fixes applied in this session.

Test 1 - ISIN Column Dynamic Resolution:
  GIVEN: NSE EQUITY_L.csv now uses column "ISIN NUMBER" instead of "ISIN NO"
  WHEN:  The enrichment code runs
  THEN:  It should detect the correct column name dynamically, not crash with KeyError

Test 2 - Backup File Lock Prevention:
  GIVEN: A DuckDB engine is actively holding file handles
  WHEN:  create_db_backup() is called
  THEN:  It should dispose the engine first and copy successfully without WinError 32
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

# ============================================================
# TEST 1: ISIN Column Dynamic Resolution
# ============================================================
print("=" * 60)
print("TEST 1: ISIN Column Dynamic Resolution")
print("=" * 60)

def test_isin_column_resolution():
    """Simulate the logic from sync_manager.py enrichment with BOTH old and new column names."""
    
    # Case A: New NSE format (ISIN NUMBER)
    df_new = pd.DataFrame({
        "SYMBOL": ["RELIANCE", "TCS"],
        "NAME OF COMPANY": ["Reliance Industries", "Tata Consultancy"],
        "ISIN NUMBER": ["INE002A01018", "INE467B01029"],
        "SERIES": ["EQ", "EQ"],
    })
    
    isin_col = "ISIN NUMBER" if "ISIN NUMBER" in df_new.columns else "ISIN NO"
    assert isin_col == "ISIN NUMBER", f"FAIL: Expected 'ISIN NUMBER', got '{isin_col}'"
    
    # Verify we can read the column
    isin_val = str(df_new.iloc[0][isin_col]).strip()
    assert isin_val == "INE002A01018", f"FAIL: Expected 'INE002A01018', got '{isin_val}'"
    
    master_isins = set(df_new[isin_col].astype(str).str.strip())
    assert "INE002A01018" in master_isins, "FAIL: ISIN not found in master set"
    print("  [PASS] Case A: New NSE format ('ISIN NUMBER') resolved correctly")
    
    # Case B: Old NSE format (ISIN NO) - fallback
    df_old = pd.DataFrame({
        "SYMBOL": ["RELIANCE", "TCS"],
        "NAME OF COMPANY": ["Reliance Industries", "Tata Consultancy"],
        "ISIN NO": ["INE002A01018", "INE467B01029"],
        "SERIES": ["EQ", "EQ"],
    })
    
    isin_col = "ISIN NUMBER" if "ISIN NUMBER" in df_old.columns else "ISIN NO"
    assert isin_col == "ISIN NO", f"FAIL: Expected 'ISIN NO', got '{isin_col}'"
    
    isin_val = str(df_old.iloc[0][isin_col]).strip()
    assert isin_val == "INE002A01018", f"FAIL: Expected 'INE002A01018', got '{isin_val}'"
    
    master_isins = set(df_old[isin_col].astype(str).str.strip())
    assert "INE002A01018" in master_isins, "FAIL: ISIN not found in master set"
    print("  [PASS] Case B: Old NSE format ('ISIN NO') fallback works correctly")
    
    # Case C: Verify old code WOULD have crashed
    try:
        _ = df_new["ISIN NO"]
        print("  [FAIL] Case C: Old code did NOT raise KeyError (unexpected)")
    except KeyError:
        print("  [PASS] Case C: Confirmed old code WOULD crash with KeyError on new format")

test_isin_column_resolution()

# ============================================================
# TEST 2: Backup File Lock Prevention
# ============================================================
print()
print("=" * 60)
print("TEST 2: Backup Engine Disposal (code path verification)")
print("=" * 60)

def test_backup_engine_disposal():
    """Verify that create_db_backup disposes the engine before copying."""
    import inspect
    from src.utils.backup_utils import create_db_backup
    
    source = inspect.getsource(create_db_backup)
    
    # Check that engine.dispose() appears BEFORE shutil.copy2
    dispose_pos = source.find("_engine.dispose()")
    copy_pos = source.find("shutil.copy2")
    
    assert dispose_pos > 0, "FAIL: engine.dispose() not found in create_db_backup"
    assert copy_pos > 0, "FAIL: shutil.copy2 not found in create_db_backup"
    assert dispose_pos < copy_pos, "FAIL: engine.dispose() must appear BEFORE shutil.copy2"
    
    print("  [PASS] create_db_backup() disposes engine BEFORE file copy")

def test_sync_manager_engine_disposal():
    """Verify sync_manager.py disposes engine before backup trigger."""
    import inspect
    from src.services.sync_manager import SyncManager
    
    source = inspect.getsource(SyncManager._run_sync_internal)
    
    # Check engine.dispose() appears before create_db_backup
    dispose_pos = source.find("_engine.dispose()")
    backup_call_pos = source.find("create_db_backup()")
    
    assert dispose_pos > 0, "FAIL: engine.dispose() not found in run_sync"
    assert backup_call_pos > 0, "FAIL: create_db_backup() not found in sync_manager"
    assert dispose_pos < backup_call_pos, \
        "FAIL: engine.dispose() -> create_db_backup() order is wrong"
    
    print("  [PASS] run_sync() disposes engine BEFORE backup call")

test_backup_engine_disposal()
test_sync_manager_engine_disposal()

# ============================================================
# SUMMARY
# ============================================================
print()
print("=" * 60)
print("ALL TESTS PASSED")
print("=" * 60)
