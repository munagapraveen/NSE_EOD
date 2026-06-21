import asyncio
import os
import sys
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.sync_manager import SyncManager
from src.services.nse_client import NSEClient
from src.models import Base
from src.db.engine import create_engine, sessionmaker

class TestEmptyEtfSymbolsGuard(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("duckdb:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

    def tearDown(self):
        self.session.close()

    def test_run_sync_raises_value_error_if_etf_symbols_empty(self):
        client = NSEClient()
        manager = SyncManager(client)

        # Mock sync_etf_master_list to fail (simulating error)
        manager.etf_downloader.sync_etf_master_list = AsyncMock(side_effect=Exception("API limit"))
        # Mock get_all_etf_symbols to return empty set (since table is empty)
        manager.etf_downloader.get_all_etf_symbols = AsyncMock(return_value=set())

        # Options requesting stocks/ETFs ingestion
        options = {"stocks": True, "etfs": True}

        # Verify that ValueError is raised
        with self.assertRaises(ValueError) as context:
            asyncio.run(manager.run_sync(
                session=self.session,
                start_date=date(2026, 6, 12),
                end_date=date(2026, 6, 12),
                options=options
            ))
        
        self.assertIn("ETF Master list is empty", str(context.exception))

if __name__ == "__main__":
    unittest.main()
