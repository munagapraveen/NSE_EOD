import sys
import os

# Append the project's root directory to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.base import Base
from src.db.engine import engine
from sqlalchemy import inspect


def initialize_database():
    """Create all tables in the database."""
    print("Initializing database...")
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully!")


def verify_tables():
    """Verify that all required tables exist in the database."""
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    
    required_tables = [
        "securities",
        "raw_prices",
        "adjusted_prices",
        "market_cap",
        "indicators",
        "corporate_actions",
        "symbol_changes",
        "sync_log",
    ]
    
    print("\nVerifying database schema...")
    missing_tables = []
    for table in required_tables:
        if table in existing_tables:
            print(f"  [OK] Table '{table}' exists.")
        else:
            print(f"  [MISSING] Table '{table}' does NOT exist.")
            missing_tables.append(table)
            
    if missing_tables:
        print(f"\nError: The following tables were not created: {missing_tables}")
        return False
    else:
        print("\nAll database tables verified successfully!")
        return True


if __name__ == "__main__":
    initialize_database()
    success = verify_tables()
    sys.exit(0 if success else 1)
