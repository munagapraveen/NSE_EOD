import os
import sys
import time
import duckdb
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import Base, Security, RawPrice, AdjustedPrice, MarketCap, Indicator, CorporateAction, SymbolChange, SyncLog, HistoricalShare
from src.utils.db_utils import align_database_sequences

# Map table name to Model (ordered by dependency)
TABLE_MODELS = [
    ("securities", Security),
    ("historical_shares", HistoricalShare),
    ("raw_prices", RawPrice),
    ("adjusted_prices", AdjustedPrice),
    ("market_cap", MarketCap),
    ("indicators", Indicator),
    ("corporate_actions", CorporateAction),
    ("symbol_changes", SymbolChange),
    ("sync_log", SyncLog),
]

def migrate():
    from dotenv import load_dotenv
    load_dotenv()
    
    duckdb_path = os.getenv("DUCKDB_DATABASE_PATH")
    if not duckdb_path:
        db_url = os.getenv("DATABASE_URL")
        if db_url and db_url.startswith("duckdb://"):
            # e.g., duckdb:////app/data/market.db -> /app/data/market.db
            # duckdb:///data/market.db -> data/market.db
            duckdb_path = db_url.replace("duckdb:///", "")
            if duckdb_path.startswith("db://"):
                duckdb_path = db_url.replace("duckdb://", "")
        else:
            duckdb_path = os.path.abspath("data/market.db")

    if not os.path.exists(duckdb_path):
        print(f"Error: DuckDB database not found at {duckdb_path}")
        sys.exit(1)

    postgres_url = os.environ.get("DATABASE_URL")
    if not postgres_url or not postgres_url.startswith("postgresql"):
        # Fallback to reading from .env manually if not in env
        from dotenv import load_dotenv
        load_dotenv()
        postgres_url = os.getenv("DATABASE_URL")
        if not postgres_url or not postgres_url.startswith("postgresql"):
            print("Error: DATABASE_URL environment variable must be set to a postgresql connection string.")
            sys.exit(1)

    print("=== Starting DuckDB to PostgreSQL Migration ===")
    print(f"Source DuckDB: {duckdb_path}")
    print(f"Target PostgreSQL: {postgres_url.split('@')[-1]}") # Print host/DB only for safety

    # 1. Initialize PostgreSQL schemas
    print("\nStep 1: Creating database schemas in PostgreSQL...")
    pg_engine = create_engine(postgres_url, echo=False)
    Base.metadata.create_all(bind=pg_engine)
    print("PostgreSQL schemas created successfully!")

    # 2. Connect to DuckDB
    con_duck = duckdb.connect(duckdb_path, read_only=True)
    
    # Session maker for Postgres
    SessionPg = sessionmaker(bind=pg_engine)
    session_pg = SessionPg()

    try:
        print("\nStep 2: Migrating data table by table...")
        for table_name, model in TABLE_MODELS:
            print(f"Migrating table '{table_name}'...")
            
            # Check if source table exists in DuckDB
            exists = con_duck.execute(
                f"SELECT 1 FROM information_schema.tables WHERE table_name = '{table_name}'"
            ).fetchone()
            if not exists:
                print(f"  Source table '{table_name}' does not exist in DuckDB. Skipping.")
                continue

            # Get total row count
            total_rows = con_duck.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            print(f"  Total rows to migrate: {total_rows:,}")
            
            if total_rows == 0:
                print("  Table is empty. Skipping.")
                continue

            # Clear target table in Postgres before inserting
            print(f"  Clearing target table '{table_name}' in PostgreSQL...")
            session_pg.execute(model.__table__.delete())
            session_pg.commit()

            # Retrieve column names
            model_columns = [col.name for col in model.__table__.columns]
            duck_cols = [row[1] for row in con_duck.execute(f"PRAGMA table_info('{table_name}')").fetchall()]
            columns = [col for col in model_columns if col in duck_cols]
            col_list_str = ", ".join([f'"{col}"' for col in columns])
            
            # Migrate in chunks
            chunk_size = 5000
            offset = 0
            start_time = time.time()
            
            while offset < total_rows:
                # Query chunk from DuckDB
                query = f"SELECT {col_list_str} FROM {table_name} LIMIT {chunk_size} OFFSET {offset}"
                rows = con_duck.execute(query).fetchall()
                
                # Convert rows to mappings (list of dicts)
                mappings = []
                for row in rows:
                    mapping = dict(zip(columns, row))
                    mappings.append(mapping)

                # Bulk insert into Postgres
                session_pg.bulk_insert_mappings(model, mappings)
                session_pg.commit()
                
                offset += len(rows)
                elapsed = time.time() - start_time
                pct = (offset / total_rows) * 100
                speed = offset / elapsed if elapsed > 0 else 0
                print(f"  Progress: {offset:,}/{total_rows:,} ({pct:.1f}%) | Speed: {speed:.0f} rows/sec", end="\r", flush=True)

            print(f"\n  Successfully migrated '{table_name}' in {time.time() - start_time:.1f} seconds.")

        # 3. Align sequences
        print("\nStep 3: Aligning database auto-increment sequences...")
        align_database_sequences(pg_engine)
        print("Sequence alignment completed successfully!")

        print("\nMigration completed SUCCESSFULLY!")

    except Exception as e:
        print(f"\nMigration FAILED due to error: {e}")
        import traceback
        traceback.print_exc()
        session_pg.rollback()
    finally:
        con_duck.close()
        session_pg.close()

if __name__ == "__main__":
    migrate()
