import asyncio
import os
import sys
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import Base, Security, RawPrice, AdjustedPrice, Indicator
from src.db.engine import create_engine, sessionmaker
from src.services.sync_manager import SyncManager
from src.services.nse_client import NSEClient

class TestTargetedHealing(unittest.TestCase):

    def setUp(self):
        # Setup in-memory DuckDB for database tests
        self.engine = create_engine("duckdb:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        
        # Instantiate SyncManager
        self.client = NSEClient()
        self.manager = SyncManager(self.client)

    def tearDown(self):
        self.session.close()

    def test_find_securities_with_adj_gaps(self):
        """Verify that _find_securities_with_adj_gaps correctly identifies missing adjusted prices."""
        # 1. Add securities
        sec_ok = Security(id=1, symbol="OK_STOCK", security_type="STOCK", is_active=True, data_source="BHAVCOPY_DISCOVERED")
        sec_gap = Security(id=2, symbol="GAP_STOCK", security_type="STOCK", is_active=True, data_source="BHAVCOPY_DISCOVERED")
        self.session.add_all([sec_ok, sec_gap])
        self.session.flush()

        # 2. Add raw prices
        raw_ok = RawPrice(security_id=sec_ok.id, trade_date=date(2026, 6, 1), open=100.0, high=105.0, low=95.0, close=102.0, volume=1000)
        raw_gap = RawPrice(security_id=sec_gap.id, trade_date=date(2026, 6, 1), open=200.0, high=205.0, low=195.0, close=202.0, volume=2000)
        self.session.add_all([raw_ok, raw_gap])
        self.session.flush()

        # 3. Add adjusted prices ONLY for the OK stock
        adj_ok = AdjustedPrice(security_id=sec_ok.id, trade_date=date(2026, 6, 1), adj_open=100.0, adj_high=105.0, adj_low=95.0, adj_close=102.0, adj_volume=1000, adjustment_factor=1.0)
        self.session.add_all([adj_ok])
        self.session.commit()

        # 4. Check gaps
        adj_gaps = self.manager._find_securities_with_adj_gaps(self.session)
        self.assertEqual(adj_gaps, {sec_gap.id})

    def test_find_securities_with_ind_gaps(self):
        """Verify that _find_securities_with_ind_gaps correctly identifies missing indicators."""
        # 1. Add securities
        sec_ok = Security(id=1, symbol="OK_STOCK", security_type="STOCK", is_active=True, data_source="BHAVCOPY_DISCOVERED")
        sec_gap = Security(id=2, symbol="GAP_STOCK", security_type="STOCK", is_active=True, data_source="BHAVCOPY_DISCOVERED")
        self.session.add_all([sec_ok, sec_gap])
        self.session.flush()

        # 2. Add adjusted prices
        adj_ok = AdjustedPrice(security_id=sec_ok.id, trade_date=date(2026, 6, 1), adj_open=100.0, adj_high=105.0, adj_low=95.0, adj_close=102.0, adj_volume=1000, adjustment_factor=1.0)
        adj_gap = AdjustedPrice(security_id=sec_gap.id, trade_date=date(2026, 6, 1), adj_open=200.0, adj_high=205.0, adj_low=195.0, adj_close=202.0, adj_volume=2000, adjustment_factor=1.0)
        self.session.add_all([adj_ok, adj_gap])
        self.session.flush()

        # 3. Add indicators ONLY for the OK stock
        ind_ok = Indicator(security_id=sec_ok.id, trade_date=date(2026, 6, 1), sma_5=100.0, sma_10=100.0, sma_20=100.0, sma_50=100.0, sma_200=100.0)
        self.session.add_all([ind_ok])
        self.session.commit()

        # 4. Check gaps
        ind_gaps = self.manager._find_securities_with_ind_gaps(self.session)
        self.assertEqual(ind_gaps, {sec_gap.id})

    @patch("src.services.sync_manager.adjust_incremental_prices", new_callable=AsyncMock)
    @patch("src.services.sync_manager.adjust_prices_for_security", new_callable=AsyncMock)
    @patch("src.services.sync_manager.calculate_incremental_market_caps_for_range", new_callable=AsyncMock)
    @patch("src.services.sync_manager.calculate_historical_market_cap", new_callable=AsyncMock)
    @patch("src.services.sync_manager.calculate_incremental_indicators_for_range", new_callable=AsyncMock)
    @patch("src.services.sync_manager.calculate_indicators_for_security", new_callable=AsyncMock)
    def test_run_sync_incremental_targeted_healing(
        self, mock_calc_ind_sec, mock_calc_ind_range, mock_calc_mcap_sec,
        mock_calc_mcap_range, mock_adj_sec, mock_adj_range
    ):
        """Verify that SyncManager incremental sync runs incremental methods and heals only targeted securities with gaps."""
        # 1. Setup mock gap detection methods
        self.manager._find_securities_with_adj_gaps = lambda s: {42}
        self.manager._find_securities_with_ind_gaps = lambda s: {99}

        # Setup mock security for market cap query
        sec_healed = Security(id=42, symbol="HEALED", security_type="STOCK", is_active=True, issued_shares=50000000)
        # Add a raw price so has_history is True
        raw_price = RawPrice(security_id=42, trade_date=date(2026, 6, 12), open=100.0, high=100.0, low=100.0, close=100.0, volume=100)
        self.session.add_all([sec_healed, raw_price])
        self.session.commit()

        # Mock corporate actions sync to avoid external network calls
        self.manager.ca_service.sync_corporate_actions = AsyncMock(return_value=0)

        # Mock options (omit stocks, etfs, indexes to skip unnecessary downloads)
        options = {
            "corporate_actions": True,
            "market_cap": True,
            "indicators": True
        }

        # Run sync manager for range of 1 day (should be incremental)
        res = asyncio.run(self.manager.run_sync(
            self.session,
            start_date=date(2026, 6, 12),
            end_date=date(2026, 6, 12),
            options=options
        ))

        self.assertEqual(res["status"], "SUCCESS")

        # Verify Block 7 (Price Adjustment):
        # - adjust_incremental_prices is called
        mock_adj_range.assert_called_once_with(self.session, date(2026, 6, 12), date(2026, 6, 12))
        # - adjust_prices_for_security is called for security 42
        mock_adj_sec.assert_called_once_with(self.session, 42)

        # Verify Block 8 (Market Cap):
        # - calculate_incremental_market_caps_for_range is called
        mock_calc_mcap_range.assert_called_once_with(self.session, date(2026, 6, 12), date(2026, 6, 12))
        # - calculate_historical_market_cap is called for security 42 (since it had adj price gaps healed)
        mock_calc_mcap_sec.assert_called_once_with(self.session, 42, 50000000)

        # Verify Block 9 (Indicators):
        # - calculate_incremental_indicators_for_range is called
        mock_calc_ind_range.assert_called_once_with(self.session, date(2026, 6, 12), date(2026, 6, 12))
        # - calculate_indicators_for_security is called for both 42 and 99 (union of adj gaps and ind gaps)
        called_sec_ids = {call.args[1] for call in mock_calc_ind_sec.call_args_list}
        self.assertEqual(called_sec_ids, {42, 99})

if __name__ == "__main__":
    unittest.main()
