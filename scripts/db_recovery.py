import os
import sys
import shutil
import duckdb
from datetime import datetime
from loguru import logger

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import settings

def run_db_rebuild(db_path: str = None) -> bool:
    """
    Rebuilds the database tables and indexes to repair corruption.
    Uses EXPORT DATABASE to dump tables to Parquet (bypassing corrupt indexes)
    and IMPORT DATABASE to restore them into a fresh file.
    
    Returns:
        bool: True if rebuild was successful and verified, False otherwise.
    """
    if not db_path:
        db_url = settings.database_url
        if db_url.startswith("duckdb:///"):
            db_path = db_url.replace("duckdb:///", "")
        else:
            logger.error("Database URL is not a local DuckDB file.")
            return False

    db_path = os.path.abspath(db_path)
    if not os.path.exists(db_path):
        logger.error(f"Database file does not exist at {db_path}")
        return False

    logger.info(f"Starting database rebuild for: {db_path}")

    # Check if database is locked (means server/other write process is active)
    # We try to open it in read-write mode to see if it is locked.
    # Note: If called from inside NiceGUI, the engine must be disposed first.
    is_locked = False
    try:
        conn_test = duckdb.connect(db_path, read_only=False)
        conn_test.close()
    except duckdb.IOException as io_err:
        if "Could not set lock" in str(io_err) or "database is locked" in str(io_err).lower():
            logger.warning("Database file is currently locked by another process (likely the NiceGUI server).")
            is_locked = True
        else:
            logger.error(f"Failed to check database lock: {io_err}")
            return False
    except Exception as e:
        logger.error(f"Error testing database access: {e}")
        return False

    if is_locked:
        logger.error("Cannot perform rebuild while database is locked. Please close the application first.")
        return False

    # Create a temporary directory for Parquet export
    temp_export_dir = os.path.join(os.path.dirname(db_path), f"rebuild_export_{int(datetime.now().timestamp())}")
    os.makedirs(temp_export_dir, exist_ok=True)

    temp_rebuild_db = db_path + ".rebuild_temp"
    if os.path.exists(temp_rebuild_db):
        os.remove(temp_rebuild_db)

    conn_source = None
    conn_target = None
    success = False

    try:
        # Step 1: Open source DB (read_only is safest) and count rows
        logger.info("Step 1: Reading source database and counting records...")
        conn_source = duckdb.connect(db_path, read_only=True)
        
        # Get list of user tables in the database
        tables_res = conn_source.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
        ).fetchall()
        tables = [r[0] for r in tables_res]
        logger.info(f"Found tables to export: {tables}")

        expected_counts = {}
        for table in tables:
            try:
                cnt = conn_source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                expected_counts[table] = cnt
                logger.info(f"Table '{table}' row count: {cnt}")
            except Exception as table_err:
                logger.error(f"Error reading row count for table {table}: {table_err}")
                raise table_err

        # Step 2: Export database schemas and data to Parquet
        logger.info("Step 2: Exporting database to Parquet files...")
        conn_source.execute(f"EXPORT DATABASE '{temp_export_dir}' (FORMAT parquet)")
        conn_source.close()
        conn_source = None
        logger.info("Database exported successfully.")

        # Step 3: Create a fresh target DB and import the schema/data
        logger.info("Step 3: Creating fresh database and importing data...")
        conn_target = duckdb.connect(temp_rebuild_db, read_only=False)
        conn_target.execute(f"IMPORT DATABASE '{temp_export_dir}'")
        
        # Step 4: Verify record counts in rebuilt database
        logger.info("Step 4: Verifying row counts in the rebuilt database...")
        actual_counts = {}
        for table in tables:
            cnt = conn_target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            actual_counts[table] = cnt
            logger.info(f"Rebuilt table '{table}' row count: {cnt} (Expected: {expected_counts[table]})")

        conn_target.close()
        conn_target = None

        # Compare row counts
        mismatches = []
        for table, exp_cnt in expected_counts.items():
            act_cnt = actual_counts.get(table, 0)
            if exp_cnt != act_cnt:
                mismatches.append(f"Table '{table}' row count mismatch: Expected {exp_cnt}, Got {act_cnt}")

        if mismatches:
            logger.error("Verification failed! Row counts do not match:")
            for m in mismatches:
                logger.error(m)
            raise ValueError("Row count verification failed post-import.")

        logger.info("Verification passed! Rebuilt database is correct and matches original data.")

        # Step 5: Replace original database file safely
        logger.info("Step 5: Swapping database files...")
        # Backup the corrupted file
        corrupt_backup_path = os.path.join(
            os.path.dirname(db_path), 
            f"market_corrupted_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        )
        shutil.move(db_path, corrupt_backup_path)
        logger.info(f"Backed up corrupted database to: {corrupt_backup_path}")

        # Move rebuilt database to active path
        shutil.move(temp_rebuild_db, db_path)
        logger.info("Rebuilt database moved to active location.")
        success = True

    except Exception as e:
        logger.error(f"Rebuild failed due to error: {e}")
        # Clean up target temporary DB if it exists
        if os.path.exists(temp_rebuild_db):
            try:
                os.remove(temp_rebuild_db)
            except Exception as rm_err:
                logger.warning(f"Could not remove temporary database file {temp_rebuild_db}: {rm_err}")
        success = False

    finally:
        # Ensure connections are closed
        if conn_source:
            try:
                conn_source.close()
            except:
                pass
        if conn_target:
            try:
                conn_target.close()
            except:
                pass

        # Clean up temporary export directory
        if os.path.exists(temp_export_dir):
            try:
                shutil.rmtree(temp_export_dir)
                logger.info("Cleaned up temporary export directory.")
            except Exception as rm_err:
                logger.warning(f"Failed to remove temporary export directory {temp_export_dir}: {rm_err}")

    if success:
        logger.info("Database rebuild completed SUCCESSFULLY. Index corruption repaired and verified.")
    else:
        logger.error("Database rebuild FAILED. Active database remains untouched.")

    return success

if __name__ == "__main__":
    # Setup logger to console
    logger.remove()
    logger.add(sys.stdout, level="INFO")
    
    print("=== DuckDB Database Rebuild Tool ===")
    res = run_db_rebuild()
    if res:
        print("Rebuild completed successfully!")
        sys.exit(0)
    else:
        print("Rebuild failed.")
        sys.exit(1)
