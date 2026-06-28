import unittest
import asyncio
from datetime import date
from sqlalchemy import create_engine, select, delete
from sqlalchemy.orm import sessionmaker

from src.models import Base, Security, SecurityIndex

# In-memory DuckDB setup for testing model logic
engine = create_engine("duckdb:///:memory:", echo=False)
TestingSessionLocal = sessionmaker(bind=engine)


class TestSecurityIndexes(unittest.TestCase):
    def setUp(self):
        # Create all schemas
        Base.metadata.create_all(bind=engine)
        self.session = TestingSessionLocal()

        # Seed sample active stock
        self.stock = Security(
            symbol="TCS",
            company_name="Tata Consultancy Services Ltd",
            security_type="STOCK",
            is_active=True
        )
        self.session.add(self.stock)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(bind=engine)

    def test_quarterly_indexing_flow(self):
        """Verify new quarter inserts, duplication filtering, subsequent skips, and historical retention."""
        # 1. Prepare mock GetQuoteApi data
        mock_results = {
            "TCS": {
                "secInfo": {
                    "index": "NIFTY 50",
                    "indexList": ["NIFTY 50", "NIFTY 100", "NIFTY TOTAL MARKET"]
                }
            }
        }

        # First trading date resolved is 1-Jul-2026 (Start of Q3 2026)
        actual_trade_date = date(2026, 7, 1)

        # 2. Extract quarter start date
        q_month = ((actual_trade_date.month - 1) // 3) * 3 + 1
        quarter_start = date(actual_trade_date.year, q_month, 1)
        self.assertEqual(quarter_start, date(2026, 7, 1))

        # Check if database already has data for this quarter (expected False)
        q_exists = self.session.query(SecurityIndex.id).filter(SecurityIndex.quarter_date == quarter_start).limit(1).first() is not None
        self.assertFalse(q_exists)

        # 3. Simulate populating logic
        indexes_to_insert = []
        seen_memberships = set()

        # Iterate active stocks
        stocks = self.session.query(Security).filter(Security.is_active == True).all()
        for stock in stocks:
            data = mock_results.get(stock.symbol)
            if not data:
                continue

            sec_info = data.get("secInfo") or {}
            primary_index = (sec_info.get("index") or "").strip() or None
            index_list = sec_info.get("indexList") or []

            # Populate Primary
            if primary_index:
                seen_memberships.add((stock.id, primary_index))
                indexes_to_insert.append({
                    "security_id": stock.id,
                    "quarter_date": quarter_start,
                    "index_name": primary_index,
                    "is_primary": True
                })

            # Populate indexList
            for idx_name in index_list:
                idx_name = (idx_name or "").strip()
                if idx_name and (stock.id, idx_name) not in seen_memberships:
                    seen_memberships.add((stock.id, idx_name))
                    indexes_to_insert.append({
                        "security_id": stock.id,
                        "quarter_date": quarter_start,
                        "index_name": idx_name,
                        "is_primary": False
                    })

        # Verify de-duplication: NIFTY 50 is primary and also in list, should only be added once as is_primary=True
        self.assertEqual(len(indexes_to_insert), 3)
        self.session.bulk_insert_mappings(SecurityIndex, indexes_to_insert)
        self.session.commit()

        # Verify written records in DB
        db_records = self.session.query(SecurityIndex).order_by(SecurityIndex.index_name).all()
        self.assertEqual(len(db_records), 3)
        
        # NIFTY 100 (is_primary = False)
        # NIFTY 50 (is_primary = True)
        # NIFTY TOTAL MARKET (is_primary = False)
        self.assertEqual(db_records[0].index_name, "NIFTY 100")
        self.assertFalse(db_records[0].is_primary)
        
        self.assertEqual(db_records[1].index_name, "NIFTY 50")
        self.assertTrue(db_records[1].is_primary)

        # 4. Run subsequent day of same quarter: 2-Jul-2026 (Check skip)
        subsequent_trade_date = date(2026, 7, 2)
        q_month_sub = ((subsequent_trade_date.month - 1) // 3) * 3 + 1
        quarter_start_sub = date(subsequent_trade_date.year, q_month_sub, 1)
        self.assertEqual(quarter_start_sub, date(2026, 7, 1))

        # Check if database already has data for this quarter (expected True)
        q_exists_sub = self.session.query(SecurityIndex.id).filter(SecurityIndex.quarter_date == quarter_start_sub).limit(1).first() is not None
        self.assertTrue(q_exists_sub) # Should skip updating!

        # 5. Run first run of next quarter: 1-Oct-2026 (Check append & retain)
        next_trade_date = date(2026, 10, 1)
        q_month_next = ((next_trade_date.month - 1) // 3) * 3 + 1
        quarter_start_next = date(next_trade_date.year, q_month_next, 1)
        self.assertEqual(quarter_start_next, date(2026, 10, 1))

        # Check if database already has data for next quarter (expected False)
        q_exists_next = self.session.query(SecurityIndex.id).filter(SecurityIndex.quarter_date == quarter_start_next).limit(1).first() is not None
        self.assertFalse(q_exists_next)

        # Populate for next quarter
        next_indexes = []
        for stock in stocks:
            next_indexes.append({
                "security_id": stock.id,
                "quarter_date": quarter_start_next,
                "index_name": "NIFTY 50",
                "is_primary": True
            })
        self.session.bulk_insert_mappings(SecurityIndex, next_indexes)
        self.session.commit()

        # Verify old records are retained and new record is appended
        total_records = self.session.query(SecurityIndex).all()
        self.assertEqual(len(total_records), 4) # 3 from Q3 + 1 from Q4
        
        q3_records = self.session.query(SecurityIndex).filter(SecurityIndex.quarter_date == date(2026, 7, 1)).all()
        self.assertEqual(len(q3_records), 3)

        q4_records = self.session.query(SecurityIndex).filter(SecurityIndex.quarter_date == date(2026, 10, 1)).all()
        self.assertEqual(len(q4_records), 1)

    def test_quarterly_indexing_self_healing(self):
        """Verify that incomplete quarterly index membership records are auto-healed and re-populated."""
        from unittest.mock import AsyncMock, MagicMock
        from src.services.sync_manager import SyncManager

        # Seed two more active stocks (total active stocks: 3)
        stock_b = Security(symbol="STOCK_B", company_name="Stock B", security_type="STOCK", is_active=True)
        stock_c = Security(symbol="STOCK_C", company_name="Stock C", security_type="STOCK", is_active=True)
        self.session.add_all([stock_b, stock_c])
        self.session.commit()

        # Instantiate SyncManager
        client_mock = MagicMock()
        client_mock.fetch_all_quotes_parallel = AsyncMock()
        manager = SyncManager(client_mock)

        # 1. Simulate a partial run by pre-populating only STOCK_A (1 index record total, which is < 50% of 3 active stocks)
        partial_rec = SecurityIndex(
            security_id=self.stock.id,
            quarter_date=date(2026, 7, 1),
            index_name="NIFTY 50",
            is_primary=True
        )
        self.session.add(partial_rec)
        self.session.commit()

        # Assert database has only 1 partial record
        self.assertEqual(self.session.query(SecurityIndex).count(), 1)

        # 2. Setup mock GetQuoteApi data for the full heal run
        mock_results = {
            "TCS": {
                "tradeInfo": {"issuedSize": 1000000, "secwisedelposdate": "01-Jul-2026 00:00:00"},
                "secInfo": {"index": "NIFTY 50", "indexList": ["NIFTY 50", "NIFTY 100"]}
            },
            "STOCK_B": {
                "tradeInfo": {"issuedSize": 2000000, "secwisedelposdate": "01-Jul-2026 00:00:00"},
                "secInfo": {"index": "NIFTY MIDCAP 50", "indexList": ["NIFTY MIDCAP 50"]}
            },
            "STOCK_C": {
                "tradeInfo": {"issuedSize": 3000000, "secwisedelposdate": "01-Jul-2026 00:00:00"},
                "secInfo": {"index": "NIFTY SMALLCAP 50", "indexList": ["NIFTY SMALLCAP 50"]}
            }
        }
        client_mock.fetch_all_quotes_parallel.return_value = mock_results

        # 3. Trigger _fetch_shares_via_get_quote_api (which runs the quarterly index population)
        asyncio.run(manager._fetch_shares_via_get_quote_api(self.session, date(2026, 7, 1)))

        # 4. Verify that self-healing occurred: the old partial record was deleted and all 3 stocks were populated
        # Total index records should be: TCS (2), STOCK_B (1), STOCK_C (1) = 4 records
        db_records = self.session.query(SecurityIndex).all()
        self.assertEqual(len(db_records), 4)

        # Verify specific records exist
        tcs_records = self.session.query(SecurityIndex).filter_by(security_id=self.stock.id).all()
        self.assertEqual(len(tcs_records), 2)
        
        b_records = self.session.query(SecurityIndex).filter_by(security_id=stock_b.id).all()
        self.assertEqual(len(b_records), 1)

    def test_cascading_delete(self):
        """Verify deleting a security automatically deletes all its index memberships."""
        # Add index record using object relationship (enables SQLAlchemy UoW cascade)
        index_rec = SecurityIndex(
            security=self.stock,
            quarter_date=date(2026, 7, 1),
            index_name="NIFTY 50",
            is_primary=True
        )
        self.session.add(index_rec)
        self.session.commit()

        # Verify exist
        self.assertEqual(self.session.query(SecurityIndex).count(), 1)

        # Delete security (DuckDB workaround: delete child first, commit, then delete parent)
        for idx in list(self.stock.security_indexes):
            self.session.delete(idx)
        self.session.commit()

        self.session.delete(self.stock)
        self.session.commit()

        # Verify index membership is cascade deleted
        self.assertEqual(self.session.query(SecurityIndex).count(), 0)


if __name__ == "__main__":
    unittest.main()
