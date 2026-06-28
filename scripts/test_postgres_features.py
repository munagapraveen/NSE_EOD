import unittest
import asyncio
from datetime import date
from unittest.mock import MagicMock, patch, AsyncMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Security, RawPrice, SyncLog
from src.db.repository import bulk_upsert_raw_prices
from src.services.sync_manager import SyncManager


class TestPostgresFeatures(unittest.TestCase):

    def setUp(self):
        # Create an in-memory database for testing the fallback paths and mocks
        self.engine = create_engine("duckdb:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_postgres_native_upsert_compiles_correctly(self):
        """Verify that bulk_upsert_raw_prices issues pg_insert statement on postgresql dialect."""
        mock_session = MagicMock()
        mock_session.get_bind.return_value.dialect.name = "postgresql"

        records = [
            {"security_id": 1, "trade_date": date(2026, 6, 1), "open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 1000, "prev_close": 98.0, "last_price": 102.0}
        ]

        # Call bulk_upsert_raw_prices with mocked session
        count = bulk_upsert_raw_prices(mock_session, records)
        
        self.assertEqual(count, 1)
        # Verify that session.execute was called with a statement referencing on_conflict_do_update
        self.assertTrue(mock_session.execute.called)
        called_stmt = mock_session.execute.call_args[0][0]
        
        # Compile statement using a real PostgreSQL dialect to check the output SQL
        from sqlalchemy.dialects.postgresql import dialect as pg_dialect
        compiled_sql = str(called_stmt.compile(dialect=pg_dialect()))
        self.assertIn("ON CONFLICT", compiled_sql)
        self.assertTrue(mock_session.commit.called)

    @patch("src.services.sync_manager.create_db_backup")
    @patch("src.services.sync_manager.prune_old_backups")
    @patch("src.services.sync_manager.adjust_incremental_prices")
    @patch("src.services.sync_manager.calculate_incremental_market_caps_for_range")
    @patch("src.services.sync_manager.calculate_incremental_indicators_for_range")
    def test_quote_api_failures_classification(self, mock_ind, mock_mcap, mock_adj, mock_prune, mock_backup):
        """Verify that Quote API failures <= 10% yield PARTIAL status, while > 10% raise error (FAILED)."""
        sm = SyncManager(client=MagicMock())
        
        # Mock dependencies inside SyncManager
        sm.etf_downloader = AsyncMock()
        sm.etf_downloader.get_all_etf_symbols.return_value = {"TCS"}
        sm.stock_downloader = AsyncMock()
        sm.stock_downloader.import_stock_prices.return_value = 5
        sm.client = AsyncMock()
        sm.client.download_bhavcopy_csv.return_value = None
        
        # Test Case 1: 5% failure rate (minor failure) -> should complete with status PARTIAL
        # Mock _fetch_shares_via_get_quote_api to return 95 successes and 5 failures (5% failure)
        sm._fetch_shares_via_get_quote_api = AsyncMock(return_value=(95, 5))
        
        options = {
            "stocks": True,
            "etfs": False,
            "indexes": False,
            "corporate_actions": False,
            "market_cap": True,
            "indicators": True
        }
        
        # Run sync inside a patch to prevent external API calls
        with patch.object(sm, "ca_service") as mock_ca:
            summary = asyncio.run(sm._run_sync_internal_impl(
                session=self.session,
                start_date=date(2026, 6, 1),
                end_date=date(2026, 6, 1),
                options=options
            ))
            
            # The sync should succeed but return PARTIAL status
            self.assertEqual(summary["status"], "PARTIAL")
            
            # Verify database sync log has PARTIAL status
            log_record = self.session.query(SyncLog).first()
            self.assertIsNotNone(log_record)
            self.assertEqual(log_record.status, "PARTIAL")
            self.assertIn("Shares Outstanding: partial failure", log_record.error_message)

        # Clear DB logs
        self.session.query(SyncLog).delete()
        self.session.commit()

        # Test Case 2: 20% failure rate (systemic failure) -> should raise RuntimeError (FAILED sync status)
        sm._fetch_shares_via_get_quote_api = AsyncMock(return_value=(80, 20))
        
        with patch.object(sm, "ca_service") as mock_ca:
            # Running the sync should raise RuntimeError due to systemic failure
            with self.assertRaises(RuntimeError):
                asyncio.run(sm._run_sync_internal_impl(
                    session=self.session,
                    start_date=date(2026, 6, 1),
                    end_date=date(2026, 6, 1),
                    options=options
                ))
            
            # Verify database sync log has FAILED status
            log_record = self.session.query(SyncLog).first()
            self.assertIsNotNone(log_record)
            self.assertEqual(log_record.status, "FAILED")
            self.assertIn("GetQuoteApi systemic failure", log_record.error_message)


if __name__ == "__main__":
    unittest.main()
