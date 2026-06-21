import asyncio
import os
import sys
import unittest
from datetime import date
from sqlalchemy import select

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.price_adjuster import adjust_prices_for_security, adjust_incremental_prices
from src.models import Base, Security, CorporateAction, RawPrice, AdjustedPrice
from src.db.engine import create_engine, sessionmaker

class TestDivideByZeroGuard(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("duckdb:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

    def tearDown(self):
        self.session.close()

    def test_invalid_adjustment_factors_guarded(self):
        # 1. Create a security
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

        # Add RawPrice data (one record prior to ex_date)
        rp = RawPrice(
            security_id=sec.id,
            trade_date=date(2026, 6, 8),
            open=100.0, high=110.0, low=90.0, close=100.0, volume=1000
        )
        self.session.add(rp)
        self.session.flush()

        # 2. Add corporate actions with invalid factors (0.0 and None/empty)
        ca_zero = CorporateAction(
            security_id=sec.id,
            action_type="SPLIT",
            ex_date=date(2026, 6, 10),
            description="Invalid Split",
            adjustment_factor=0.0,
            is_processed=False
        )
        self.session.add(ca_zero)
        self.session.commit()

        # Run adjust_prices_for_security - should not crash!
        try:
            written = asyncio.run(adjust_prices_for_security(self.session, sec.id))
        except ZeroDivisionError:
            self.fail("ZeroDivisionError raised during price adjustment with 0.0 factor!")

        # Verify that factor defaulted to 1.0 (so price is unchanged)
        adjusted = self.session.execute(
            select(AdjustedPrice).where(AdjustedPrice.security_id == sec.id)
        ).scalar_one()
        
        self.assertEqual(float(adjusted.adj_close), 100.0)
        self.assertEqual(float(adjusted.adjustment_factor), 1.0)

    def test_incremental_copier_guarded_against_zero_factor(self):
        # 1. Create a security
        sec = Security(
            symbol="TEST2",
            company_name="Test Company 2",
            isin="INE111111111",
            security_type="STOCK",
            is_active=True,
            issued_shares=1000
        )
        self.session.add(sec)
        self.session.flush()

        # Add a historical adjusted price with an invalid/corrupted factor of 0.0
        hist_adj = AdjustedPrice(
            security_id=sec.id,
            trade_date=date(2026, 6, 8),
            adj_open=100.0, adj_high=110.0, adj_low=90.0, adj_close=100.0, adj_volume=1000,
            adjustment_factor=0.0
        )
        self.session.add(hist_adj)

        # Add new raw price to copy
        rp = RawPrice(
            security_id=sec.id,
            trade_date=date(2026, 6, 9),
            open=120.0, high=130.0, low=110.0, close=120.0, volume=1500
        )
        self.session.add(rp)
        self.session.commit()

        # Run adjust_incremental_prices - should not crash!
        try:
            written = asyncio.run(adjust_incremental_prices(self.session, date(2026, 6, 9), date(2026, 6, 9)))
        except ZeroDivisionError:
            self.fail("ZeroDivisionError raised in incremental copier with 0.0 factor!")

        # Verify that factor defaulted to 1.0 (so close is 120.0)
        adjusted = self.session.execute(
            select(AdjustedPrice)
            .where(AdjustedPrice.security_id == sec.id)
            .where(AdjustedPrice.trade_date == date(2026, 6, 9))
        ).scalar_one()

        self.assertEqual(float(adjusted.adj_close), 120.0)
        self.assertEqual(float(adjusted.adjustment_factor), 1.0)

if __name__ == "__main__":
    unittest.main()
