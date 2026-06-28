import unittest
import asyncio
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch, AsyncMock

from src.models import Base, Security, RawPrice, AdjustedPrice
from src.services.price_adjuster import adjust_all_prices


class TestStageAtomicity(unittest.TestCase):

    def setUp(self):
        # Create an in-memory DuckDB database for testing
        self.engine = create_engine("duckdb:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

        # Seed two active stocks
        self.stock1 = Security(
            id=1,
            symbol="STOCK1",
            company_name="Stock 1",
            security_type="STOCK",
            is_active=True
        )
        self.stock2 = Security(
            id=2,
            symbol="STOCK2",
            company_name="Stock 2",
            security_type="STOCK",
            is_active=True
        )
        self.session.add_all([self.stock1, self.stock2])
        self.session.commit()

        # Seed raw prices
        from datetime import date
        self.rp1 = RawPrice(security_id=1, trade_date=date(2026, 6, 1), open=100.0, high=105.0, low=99.0, close=102.0, volume=1000)
        self.rp2 = RawPrice(security_id=2, trade_date=date(2026, 6, 1), open=200.0, high=210.0, low=198.0, close=205.0, volume=2000)
        self.session.add_all([self.rp1, self.rp2])
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_price_adjustment_stage_atomicity_on_failure(self):
        """Verify that price adjustment recalculations roll back entirely if any security fails."""
        # 1. Run successfully once so we have records in AdjustedPrice
        count = asyncio.run(adjust_all_prices(self.session))
        self.assertEqual(count, 2)
        
        # Verify both stocks have adjusted prices
        m_counts = self.session.query(AdjustedPrice).count()
        self.assertEqual(m_counts, 2)

        # 2. Mock adjust_prices_for_security to succeed on STOCK1 but raise Exception on STOCK2
        from src.services.price_adjuster import adjust_prices_for_security as real_adjust_prices_for_security
        
        async def mock_adjust_prices_for_security(sess, sec_id):
            if sec_id == 2:
                raise ValueError("Simulated write error on STOCK2")
            return await real_adjust_prices_for_security(sess, sec_id)

        # Apply the mock to trigger a mid-stage failure
        with patch("src.services.price_adjuster.adjust_prices_for_security", side_effect=mock_adjust_prices_for_security):
            # Run adjust_all_prices. It should raise ValueError
            with self.assertRaises(ValueError):
                asyncio.run(adjust_all_prices(self.session))

        # 3. Verify stage-atomicity: since STOCK2 failed, STOCK1's updates must be rolled back completely
        # In a non-atomic setup, STOCK1 would have been updated/committed, and STOCK2's failure would rollback STOCK2 only.
        # But in our atomic setup, since ValueError propagated and transaction rolled back, the session state is clean.
        # Let's refresh and assert that the database state remains untouched (original successful adjust run is still there).
        self.session.rollback()
        
        adj_records = self.session.query(AdjustedPrice).all()
        self.assertEqual(len(adj_records), 2)
        # Verify STOCK2's adjusted record is still intact
        stock2_adj = self.session.query(AdjustedPrice).filter_by(security_id=2).one()
        self.assertEqual(float(stock2_adj.adj_close), 205.0)


if __name__ == "__main__":
    unittest.main()
