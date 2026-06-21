import asyncio
import os
import sys
import unittest
from datetime import date
import re

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.sync_manager import SyncManager
from src.services.nse_client import NSEClient
from src.services.symbol_changes import _merge_securities
from src.ui.layout import check_db_integrity
from src.models import Base, Security, CorporateAction
from src.db.engine import create_engine, sessionmaker

class TestExtendedFixes(unittest.TestCase):

    def setUp(self):
        # Setup in-memory DuckDB for database tests
        self.engine = create_engine("duckdb:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

    def tearDown(self):
        self.session.close()

    def test_bse_scripcode_regex(self):
        """Verify that the tightened BSE scripcode lookup regex handles the JavaScript click handler."""
        # Simulated response from BSE smart search
        response_text = (
            "<li class='quotemenu quotemenuselect' ng-click=\"liclick('500325','RELIANCE INDUSTRIES LTD')\">"
            "<a>RELIANCE INDUSTRIES LTD<br /><span>RELIANCE&nbsp;&nbsp;&nbsp;<strong>INE002A01018</strong>&nbsp;&nbsp;&nbsp;500325</span>"
            "</a></li>"
        )
        # Verify the primary pattern match
        match = re.search(r'(\d{6})</a>', response_text)
        # Should fail due to </span> between 500325 and </a>
        self.assertIsNone(match)

        # Verify our new fallback pattern
        match2 = re.search(r"liclick\('(\d{6})'", response_text)
        self.assertIsNotNone(match2)
        self.assertEqual(match2.group(1), "500325")

    def test_sync_manager_is_running_flag(self):
        """Verify that SyncManager wrapper correctly sets and resets the is_running flag."""
        client = NSEClient()
        manager = SyncManager(client)
        self.assertFalse(manager.is_running)

        # Mock _run_sync_internal to verify the flag stays True during run
        async def mock_internal(*args, **kwargs):
            self.assertTrue(manager.is_running)
            return {"status": "SUCCESS", "message": "mocked"}

        manager._run_sync_internal = mock_internal
        
        # Run wrapper
        res = asyncio.run(manager.run_sync(self.session, date(2026, 6, 1), date(2026, 6, 1), {}))
        
        self.assertEqual(res["status"], "SUCCESS")
        self.assertFalse(manager.is_running)

    def test_db_lock_vs_corruption(self):
        """Verify that a database file lock is not misclassified as index/data corruption."""
        # Standard check passes on a healthy, non-locked DB
        # If we raise a locked exception, check_db_integrity should return False, ""
        from unittest.mock import patch
        
        with patch("src.ui.layout.SessionLocal") as mock_session_maker:
            mock_session = mock_session_maker.return_value
            # Simulate a DuckDB connection lock exception
            import duckdb
            mock_session.query.side_effect = duckdb.IOException("Could not set lock: Database is locked by another process.")
            
            is_corrupt, err_msg = check_db_integrity()
            self.assertFalse(is_corrupt)
            self.assertEqual(err_msg, "")

    def test_merge_duplicate_corporate_actions(self):
        """Verify that merging two securities deletes duplicate corporate actions from the old security first to avoid constraint conflicts."""
        # 1. Create two securities
        old_sec = Security(symbol="OLD", security_type="STOCK", is_active=True, data_source="BHAVCOPY_DISCOVERED")
        new_sec = Security(symbol="NEW", security_type="STOCK", is_active=True, data_source="BHAVCOPY_DISCOVERED")
        self.session.add_all([old_sec, new_sec])
        self.session.flush()

        # 2. Add duplicate corporate action to both
        ca_old = CorporateAction(
            security_id=old_sec.id,
            action_type="SPLIT",
            ex_date=date(2026, 6, 10),
            description="Split 10 to 5",
            adjustment_factor=2.0
        )
        ca_new = CorporateAction(
            security_id=new_sec.id,
            action_type="SPLIT",
            ex_date=date(2026, 6, 10),
            description="Split 10 to 5",
            adjustment_factor=2.0
        )
        
        # Add a unique non-overlapping action to old to ensure it gets updated correctly
        ca_unique = CorporateAction(
            security_id=old_sec.id,
            action_type="BONUS",
            ex_date=date(2026, 6, 11),
            description="Bonus 1:1",
            adjustment_factor=2.0
        )
        
        self.session.add_all([ca_old, ca_new, ca_unique])
        self.session.commit()

        # 3. Call _merge_securities (should successfully merge without unique constraint violations)
        try:
            _merge_securities(self.session, old_sec, new_sec)
        except Exception as e:
            self.fail(f"_merge_securities failed with unique constraint exception: {e}")

        # 4. Verify results
        # Old security is deleted
        old_sec_db = self.session.query(Security).filter(Security.id == old_sec.id).first()
        self.assertIsNone(old_sec_db)

        # Corporate actions under new security ID
        actions = self.session.query(CorporateAction).filter(CorporateAction.security_id == new_sec.id).all()
        # There should be exactly 2 actions (the SPLIT which was deduplicated and the unique BONUS)
        self.assertEqual(len(actions), 2)
        action_types = {a.action_type for a in actions}
        self.assertIn("SPLIT", action_types)
        self.assertIn("BONUS", action_types)

if __name__ == "__main__":
    unittest.main()
