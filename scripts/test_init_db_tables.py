import sys
import os
from sqlalchemy import inspect

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.engine import engine

def test_init_db():
    print("Running init_db validation test...")
    
    # We will import and run scripts.init_db's functions
    from scripts.init_db import initialize_database, verify_tables
    
    # Run the database initialization (which runs metadata.create_all)
    initialize_database()
    
    # Verify that the tables exist
    success = verify_tables()
    
    assert success, "Verification failed! Tables were not successfully created."
    print("  [PASS] Database tables exist and are verified.")
    print("\ninit_db validation test PASSED successfully!")

if __name__ == "__main__":
    test_init_db()
