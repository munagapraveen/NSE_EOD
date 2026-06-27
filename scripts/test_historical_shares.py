import unittest
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Security, RawPrice, AdjustedPrice, HistoricalShare, MarketCap
from src.services.historical_shares import parse_xbrl_shares
from src.services.market_cap import calculate_historical_market_cap, calculate_incremental_market_caps_for_range

class TestHistoricalShares(unittest.TestCase):
    def setUp(self):
        # Create in-memory sqlite/duckdb engine for testing models
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

    def tearDown(self):
        self.session.close()

    def test_xbrl_share_parsing(self):
        """Verify that NSE XBRL XML is correctly parsed to extract share counts and report dates."""
        # Minimal XBRL with both context variants
        xbrl_template = '''<?xml version="1.0" encoding="UTF-8"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance">
  <DateOfReport>{date}</DateOfReport>
  <NumberOfFullyPaidUpEquityShares contextRef="{ctx}">{shares}</NumberOfFullyPaidUpEquityShares>
</xbrl>'''

        test_cases = [
            # (context, date_str, shares_str, expected_date, expected_shares)
            ("ShareholdingPattern_ContextI", "2026-03-31", "1234567890", date(2026, 3, 31), 1234567890),
            ("ShareholdingPatternI",         "2025-09-30", "987654321",  date(2025, 9, 30), 987654321),
            # Wrong context → shares not extracted
            ("WrongContext",                 "2025-06-30", "111111111",  date(2025, 6, 30), None),
        ]
        for ctx, date_str, shares_str, exp_date, exp_shares in test_cases:
            xml = xbrl_template.format(ctx=ctx, date=date_str, shares=shares_str).encode()
            shares, qdate = parse_xbrl_shares(xml)
            self.assertEqual(qdate, exp_date, f"Date mismatch for context={ctx}")
            self.assertEqual(shares, exp_shares, f"Shares mismatch for context={ctx}")

        # Malformed XML → returns (None, None) without raising
        bad_shares, bad_date = parse_xbrl_shares(b"<not valid xml")
        self.assertIsNone(bad_shares)
        self.assertIsNone(bad_date)



    def test_run(self):
        import asyncio
        asyncio.run(self._test_calculations_async())

    async def _test_calculations_async(self):
        # 1. Create Security
        sec = Security(
            symbol="INFY",
            company_name="Infosys Limited",
            security_type="STOCK",
            isin="INE009A01021",
            issued_shares=200000000,
            is_active=True
        )
        self.session.add(sec)
        self.session.commit()

        # Seed price history around a 2-for-1 split on 2025-11-14
        dates_data = [
            (date(2025, 11, 10), 1000.0, 500.0, 2.0), # Pre-split
            (date(2025, 11, 12), 1000.0, 500.0, 2.0),
            (date(2025, 11, 14), 500.0, 500.0, 1.0),  # Post-split (ex-date)
            (date(2025, 11, 15), 500.0, 500.0, 1.0),
        ]

        for d, raw_close, adj_close, factor in dates_data:
            rp = RawPrice(
                security_id=sec.id,
                trade_date=d,
                open=raw_close,
                high=raw_close,
                low=raw_close,
                close=raw_close,
                volume=10000
            )
            ap = AdjustedPrice(
                security_id=sec.id,
                trade_date=d,
                adj_open=adj_close,
                adj_high=adj_close,
                adj_low=adj_close,
                adj_close=adj_close,
                adj_volume=20000,
                adjustment_factor=factor
            )
            self.session.add(rp)
            self.session.add(ap)
        self.session.commit()

        # Seed historical quarterly shares
        # Quarter ended Sep 30, 2025 had 100M shares (pre-split)
        q1 = HistoricalShare(
            security_id=sec.id,
            quarter_date=date(2025, 9, 30),
            issued_shares=100000000,
            source="BSE_QUARTERLY_SHP"
        )
        self.session.add(q1)
        self.session.commit()

        # Execute market cap calculations
        await calculate_historical_market_cap(self.session, sec.id, sec.issued_shares)

        # Check records written
        records = self.session.query(MarketCap).order_by(MarketCap.trade_date.asc()).all()
        self.assertEqual(len(records), 4)

        # Case 1: Pre-split date (2025-11-10)
        # Outstanding shares resolved: 100M
        # Market Cap (Cr) = (100M * 1000) / 1e7 = 10,000 Cr
        rec_pre = records[0]
        self.assertEqual(rec_pre.trade_date, date(2025, 11, 10))
        self.assertEqual(rec_pre.issued_shares, 100000000)
        self.assertEqual(float(rec_pre.market_cap), 10000.0)

        # Case 2: Post-split date (2025-11-14)
        # Preceding quarter is Sep 30 (100M). Trade date factor = 1.0, quarter factor = 2.0.
        # Resolved shares: 100M * (2.0 / 1.0) = 200M shares.
        # Market Cap (Cr) = (200M * 500) / 1e7 = 10,000 Cr
        rec_post = records[2]
        self.assertEqual(rec_post.trade_date, date(2025, 11, 14))
        self.assertEqual(rec_post.issued_shares, 200000000)
        self.assertEqual(float(rec_post.market_cap), 10000.0)
        self.assertEqual(rec_post.shares_source, "NSE_XBRL_SHP")

        # Now test incremental calculations
        # Add new date: 2025-11-18
        rp_new = RawPrice(
            security_id=sec.id,
            trade_date=date(2025, 11, 18),
            open=500.0,
            high=500.0,
            low=500.0,
            close=500.0,
            volume=10000
        )
        ap_new = AdjustedPrice(
            security_id=sec.id,
            trade_date=date(2025, 11, 18),
            adj_open=500.0,
            adj_high=500.0,
            adj_low=500.0,
            adj_close=500.0,
            adj_volume=20000,
            adjustment_factor=1.0
        )
        self.session.add(rp_new)
        self.session.add(ap_new)
        self.session.commit()

        await calculate_incremental_market_caps_for_range(self.session, date(2025, 11, 18), date(2025, 11, 18))
        
        rec_incr = self.session.query(MarketCap).filter(MarketCap.trade_date == date(2025, 11, 18)).first()
        self.assertIsNotNone(rec_incr)
        self.assertEqual(rec_incr.issued_shares, 200000000)
        self.assertEqual(float(rec_incr.market_cap), 10000.0)

if __name__ == "__main__":
    unittest.main()
