import asyncio
import os
import sys
import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.sync_manager import SyncManager
from src.services.nse_client import NSEClient
from src.models import Base, Security, CorporateAction, RawPrice
from src.db.engine import create_engine, sessionmaker

class TestCorporateActionProcessed(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("duckdb:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

    def tearDown(self):
        self.session.close()

    def test_incremental_corporate_actions_are_marked_processed(self):
        # 1. Create a security with issued_shares
        sec = Security(
            symbol="TEST",
            company_name="Test Company",
            isin="INE000000000",
            security_type="STOCK",
            is_active=True,
            issued_shares=1000
        )
        self.session.add(sec)
        self.session.flush()
        sec_id = sec.id

        # Add a dummy RawPrice to make sure is_incremental detection works (needs has_history)
        rp = RawPrice(
            security_id=sec_id,
            trade_date=date(2026, 6, 8),
            open=100.0, high=110.0, low=90.0, close=100.0, volume=1000
        )
        self.session.add(rp)
        self.session.flush()

        # 2. Add an unprocessed corporate action ex-dating on 2026-06-10
        action = CorporateAction(
            security_id=sec_id,
            action_type="SPLIT",
            ex_date=date(2026, 6, 10),
            description="Split 1 to 2",
            adjustment_factor=2.0,
            is_processed=False
        )
        self.session.add(action)
        self.session.commit()
        action_id = action.id

        client = NSEClient()
        manager = SyncManager(client)

        # Mock sync_corporate_actions and apply_price_adjustments_incremental to do nothing
        manager.ca_service.sync_corporate_actions = AsyncMock(return_value=0)
        manager.ca_service.apply_price_adjustments_incremental = AsyncMock(return_value=0)

        # 3. First sync run - should process the action, update shares, and mark it as processed
        start_date = date(2026, 6, 10)
        end_date = date(2026, 6, 10)
        options = {"corporate_actions": True}
        
        # Execute run_sync
        res = asyncio.run(manager.run_sync(
            session=self.session,
            start_date=start_date,
            end_date=end_date,
            options=options
        ))

        # Check if the session is closed by SyncManager (so we need a new session)
        self.session = self.Session()
        
        # Verify the security's issued_shares was adjusted
        sec_updated = self.session.get(Security, sec_id)
        self.assertEqual(sec_updated.issued_shares, 2000)

        # Verify the corporate action was marked as processed
        action_updated = self.session.get(CorporateAction, action_id)
        self.assertTrue(action_updated.is_processed)
        self.assertIsNotNone(action_updated.processed_at)

        # 4. Second sync run - should NOT re-apply the adjustment (shares remain 2000)
        res2 = asyncio.run(manager.run_sync(
            session=self.session,
            start_date=start_date,
            end_date=end_date,
            options=options
        ))

        self.session = self.Session()
        sec_updated_2 = self.session.get(Security, sec_id)
        self.assertEqual(sec_updated_2.issued_shares, 2000)  # Should still be 2000, not 4000!

if __name__ == "__main__":
    unittest.main()
