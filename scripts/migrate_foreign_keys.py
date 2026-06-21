import os
import sys
import shutil
from datetime import datetime
from sqlalchemy import text

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from src.db.engine import engine
from src.models import Base
from src.utils.db_utils import align_database_sequences

def main():
    print("=== DuckDB Foreign Key Migration Script (SQLAlchemy Transaction Strategy) ===")
    
    # Get database path
    db_url = settings.database_url
    if not db_url.startswith("duckdb:///"):
        print("Error: Database URL is not a local DuckDB file.")
        sys.exit(1)
        
    db_path = os.path.abspath(db_url.replace("duckdb:///", ""))
    if not os.path.exists(db_path):
        print(f"Error: Database file does not exist at {db_path}")
        sys.exit(1)
        
    # 1. Create a database backup
    backup_dir = os.path.join(os.path.dirname(os.path.dirname(db_path)), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"market_backup_before_fk_migration_{timestamp}.db")
    
    print(f"Creating database backup: {backup_path}")
    try:
        engine.dispose()
        shutil.copy2(db_path, backup_path)
        print("Database backup completed successfully.")
    except Exception as e:
        print(f"Error creating backup: {e}")
        sys.exit(1)
        
    # 2. Perform the migration using SQLAlchemy
    tables_to_migrate = ["securities", "raw_prices", "adjusted_prices", "indicators", "market_cap", "corporate_actions", "symbol_changes", "historical_shares"]
    child_tables = ["raw_prices", "adjusted_prices", "indicators", "market_cap", "corporate_actions", "symbol_changes", "historical_shares"]
    
    try:
        # Step 2A: Create temp tables, clean orphans, drop original tables
        print("Step 2: Backing up data to temp tables and dropping originals...")
        with engine.begin() as conn:
            for table in tables_to_migrate:
                print(f"  Creating temp_{table}...")
                conn.execute(text(f"CREATE TABLE temp_{table} AS SELECT * FROM {table}"))
                
            print("  Cleaning up orphan references in temporary tables...")
            # symbol_changes security_id is nullable: set orphans to NULL
            orphans_sc = conn.execute(text("""
                SELECT COUNT(*) FROM temp_symbol_changes 
                WHERE security_id IS NOT NULL AND security_id NOT IN (SELECT id FROM temp_securities)
            """)).scalar()
            if orphans_sc > 0:
                print(f"    Found {orphans_sc} orphan symbol_changes records. Setting security_id = NULL...")
                conn.execute(text("""
                    UPDATE temp_symbol_changes 
                    SET security_id = NULL 
                    WHERE security_id IS NOT NULL AND security_id NOT IN (SELECT id FROM temp_securities)
                """))
                
            # Other child tables: delete any orphan records
            for table in child_tables:
                orphans = conn.execute(text(f"""
                    SELECT COUNT(*) FROM temp_{table} 
                    WHERE security_id NOT IN (SELECT id FROM temp_securities)
                """)).scalar()
                if orphans > 0:
                    print(f"    Found {orphans} orphan records in temp_{table}. Deleting them...")
                    conn.execute(text(f"""
                        DELETE FROM temp_{table} 
                        WHERE security_id NOT IN (SELECT id FROM temp_securities)
                    """))
                    
            print("  Dropping original tables...")
            for table in child_tables:
                print(f"    Dropping child table {table}...")
                conn.execute(text(f"DROP TABLE {table}"))
            print("    Dropping parent table securities...")
            conn.execute(text("DROP TABLE securities"))
            
        # Step 2B: Recreate tables with updated schema via SQLAlchemy (including FK constraints)
        print("Step 3: Re-creating tables with updated schemas using SQLAlchemy...")
        # Dispose the engine to release connections before schema creation to be safe
        engine.dispose()
        Base.metadata.create_all(bind=engine)
        
        # Step 2C: Copy data from temp tables back to newly created tables
        print("Step 4: Restoring data from temporary tables...")
        with engine.begin() as conn:
            # 1. Parent table must be populated first
            print("  Restoring securities...")
            columns_sec = conn.execute(text("DESCRIBE securities")).fetchall()
            cols_sec_str = ", ".join([col[0] for col in columns_sec])
            conn.execute(text(f"INSERT INTO securities ({cols_sec_str}) SELECT {cols_sec_str} FROM temp_securities"))
            
            # 2. Child tables
            for table in child_tables:
                print(f"  Restoring {table}...")
                columns = conn.execute(text(f"DESCRIBE {table}")).fetchall()
                cols_str = ", ".join([col[0] for col in columns])
                conn.execute(text(f"INSERT INTO {table} ({cols_str}) SELECT {cols_str} FROM temp_{table}"))
                
            # Drop temp tables
            print("  Dropping temporary tables...")
            for table in tables_to_migrate:
                conn.execute(text(f"DROP TABLE temp_{table}"))
                
        # Align database sequences
        print("Step 5: Aligning database sequences...")
        engine.dispose()
        align_database_sequences(engine)
        
        print("\n=== MIGRATION COMPLETED SUCCESSFULLY ===")
        
    except Exception as e:
        print(f"\nMigration failed due to error: {e}")
        restore_backup(backup_path, db_path)
        sys.exit(1)

def restore_backup(backup_path: str, db_path: str):
    print(f"[ROLLBACK] Restoring original database from backup: {backup_path}")
    try:
        engine.dispose()
        if os.path.exists(db_path):
            os.remove(db_path)
        shutil.copy2(backup_path, db_path)
        print("[ROLLBACK] Restore completed successfully.")
    except Exception as e:
        print(f"[FATAL] Failed to restore database from backup: {e}")

if __name__ == "__main__":
    main()
