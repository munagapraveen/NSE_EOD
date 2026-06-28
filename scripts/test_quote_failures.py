import unittest
import asyncio
from datetime import date, datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import AsyncMock, MagicMock, patch

from src.models import Base, Security, SyncLog
from src.services.sync_manager import SyncManager


class TestQuoteFailures(unittest.TestCase):

    def setUp(self):
        # Create an in-memory DuckDB database for testing
        self.engine = create_engine("duckdb:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

        # Seed sample active stock
        self.stock = Security(
            id=1,
            symbol="INFY",
            company_name="Infosys Limited",
            security_type="STOCK",
            is_active=True,
            data_source="BHAVCOPY_DISCOVERED"
        )
        self.session.add(self.stock)
        self.session.commit()

        # Seed a historical raw price to force incremental sync mode
        from src.models import RawPrice
        raw_price = RawPrice(
            security_id=1,
            trade_date=date(2026, 6, 11),
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=100
        )
        self.session.add(raw_price)
        self.session.commit()

        # Instantiate SyncManager with mock client
        self.client_mock = MagicMock()
        self.client_mock.download_bhavcopy_csv = AsyncMock(return_value="dummy_path")
        self.client_mock.download_index_csv = AsyncMock(return_value="dummy_path")
        self.client_mock.download_etf_list = AsyncMock()
        self.client_mock.download_symbol_changes = AsyncMock()
        self.client_mock.download_equity_list = AsyncMock()
        self.manager = SyncManager(self.client_mock)
        self.manager.ca_service.sync_corporate_actions = AsyncMock(return_value=0)

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_sync_fails_systemically_on_quote_api_outage(self):
        """Verify that systemic Quote API failures abort the stage and skip market-cap calculations."""
        # 1. Mock _fetch_shares_via_get_quote_api to simulate a systemic outage (0 success, 5 failures)
        self.manager._fetch_shares_via_get_quote_api = AsyncMock(return_value=(0, 5))

        # Setup mock options
        options = {
            "market_cap": True,
            "indicators": True
        }

        # Mock download / import dependencies to prevent actual downloads
        with patch.object(self.manager.stock_downloader, "download_and_import_date", AsyncMock(return_value=(None, False))), \
             patch.object(self.manager.index_downloader, "download_and_import_date", AsyncMock(return_value=5)), \
             patch("src.services.sync_manager.adjust_incremental_prices", AsyncMock()), \
             patch("src.services.sync_manager.calculate_incremental_indicators_for_range", AsyncMock()) as mock_calc_ind:

            # Run sync manager
            with self.assertRaises(RuntimeError):
                asyncio.run(self.manager.run_sync(
                    self.session,
                    start_date=date(2026, 6, 12),
                    end_date=date(2026, 6, 12),
                    options=options
                ))
            
            # Query log from database
            log = self.session.query(SyncLog).filter_by(sync_date=date(2026, 6, 12)).first()
            self.assertIsNotNone(log)
            self.assertEqual(log.status, "FAILED")

            # Assert failed stages contains GetQuoteApi systemic failure
            self.assertIn("GetQuoteApi systemic failure", log.error_message)


if __name__ == "__main__":
    unittest.main()
