import sys
import os
import unittest
from datetime import date
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Append the project's root directory to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import Base, Security, SyncLog
from src.utils.db_utils import align_database_sequences

class TestSequenceAlignment(unittest.TestCase):
    def setUp(self):
        # Create an in-memory DuckDB database
        self.engine = create_engine("duckdb:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_sequence_alignment_recovery(self):
        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO securities (id, symbol, security_type, is_active, is_delisted) VALUES (1, 'SEC_A', 'STOCK', True, False);")
            )
            conn.execute(
                text("INSERT INTO securities (id, symbol, security_type, is_active, is_delisted) VALUES (2, 'SEC_B', 'STOCK', True, False);")
            )
            conn.execute(
                text("INSERT INTO sync_log (id, sync_type, sync_date, status, started_at) VALUES (50, 'FULL_SYNC', '2026-06-12', 'SUCCESS', CURRENT_TIMESTAMP);")
            )

        # Confirm max IDs are 2 and 50 respectively
        max_sec_id = self.session.execute(text("SELECT max(id) FROM securities")).scalar()
        max_log_id = self.session.execute(text("SELECT max(id) FROM sync_log")).scalar()
        self.assertEqual(max_sec_id, 2)
        self.assertEqual(max_log_id, 50)

        # 2. Verify that inserting a new Security using SQLAlchemy triggers constraint violation
        # because sequence nextval starts at 1, which conflicts with manually inserted IDs
        new_sec1 = Security(symbol="SEC_C", security_type="STOCK", is_active=True)
        self.session.add(new_sec1)
        
        # Flashing/committing should fail with Constraint Error
        with self.assertRaises(Exception) as context:
            self.session.commit()
        self.session.rollback()
        self.assertTrue("constraint" in str(context.exception).lower() or "duplicate" in str(context.exception).lower())

        # 3. Run sequence alignment utility
        align_database_sequences(self.engine)

        # 4. Try inserting new records again — they should succeed now!
        new_sec2 = Security(symbol="SEC_D", security_type="STOCK", is_active=True)
        new_sec3 = Security(symbol="SEC_E", security_type="STOCK", is_active=True)
        
        from datetime import datetime
        new_log = SyncLog(sync_type="DAILY_SYNC", sync_date=date(2026, 6, 13), status="SUCCESS", started_at=datetime.now())

        self.session.add(new_sec2)
        self.session.add(new_sec3)
        self.session.add(new_log)
        self.session.commit()

        # 5. Check that the assigned IDs are correct (next values should be 3, 4, and 51 respectively)
        self.assertEqual(new_sec2.id, 3)
        self.assertEqual(new_sec3.id, 4)
        self.assertEqual(new_log.id, 51)

        print("\n[SUCCESS] TestSequenceAlignment: Database sequences successfully aligned and verified!")

if __name__ == "__main__":
    unittest.main()
