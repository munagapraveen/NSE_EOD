import unittest
from datetime import date
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.models import Base, Security, RawPrice
from src.db.repository import bulk_upsert_raw_prices


class TestRepositoryUpsert(unittest.TestCase):

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

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_bulk_upsert_raw_prices_insert_and_update(self):
        """Verify that bulk_upsert_raw_prices inserts new rows and updates existing rows."""
        # 1. First import: Insert new raw price
        records_first = [
            {
                "security_id": 1,
                "trade_date": date(2026, 6, 1),
                "open": 1400.0,
                "high": 1420.0,
                "low": 1395.0,
                "close": 1410.0,
                "volume": 100000,
                "prev_close": 1390.0,
                "last_price": 1412.0
            }
        ]
        
        count_first = bulk_upsert_raw_prices(self.session, records_first)
        self.assertEqual(count_first, 1)

        # Assert inserted values
        db_price = self.session.execute(
            select(RawPrice).where(RawPrice.security_id == 1, RawPrice.trade_date == date(2026, 6, 1))
        ).scalar_one()
        self.assertEqual(float(db_price.close), 1410.0)
        self.assertEqual(db_price.volume, 100000)

        # 2. Second import: Upsert same row with updated values (Close changes from 1410 to 1435, Volume from 100000 to 150000)
        records_second = [
            {
                "security_id": 1,
                "trade_date": date(2026, 6, 1),
                "open": 1400.0,
                "high": 1440.0,
                "low": 1395.0,
                "close": 1435.0,  # Updated
                "volume": 150000,  # Updated
                "prev_close": 1390.0,
                "last_price": 1432.0
            }
        ]

        count_second = bulk_upsert_raw_prices(self.session, records_second)
        self.assertEqual(count_second, 1)

        # Refresh database session
        self.session.expire_all()

        # Assert updated values
        db_price_updated = self.session.execute(
            select(RawPrice).where(RawPrice.security_id == 1, RawPrice.trade_date == date(2026, 6, 1))
        ).scalar_one()
        self.assertEqual(float(db_price_updated.close), 1435.0)
        self.assertEqual(db_price_updated.volume, 150000)

        # 3. Third import: Mix of existing row update and a new row insert
        records_third = [
            {
                "security_id": 1,
                "trade_date": date(2026, 6, 1),  # Existing (should update close to 1450)
                "open": 1400.0,
                "high": 1460.0,
                "low": 1395.0,
                "close": 1450.0,
                "volume": 200000,
                "prev_close": 1390.0,
                "last_price": 1452.0
            },
            {
                "security_id": 1,
                "trade_date": date(2026, 6, 2),  # New (should insert)
                "open": 1450.0,
                "high": 1470.0,
                "low": 1445.0,
                "close": 1460.0,
                "volume": 80000,
                "prev_close": 1450.0,
                "last_price": 1462.0
            }
        ]

        count_third = bulk_upsert_raw_prices(self.session, records_third)
        self.assertEqual(count_third, 2)

        self.session.expire_all()

        # Verify both rows
        price_1 = self.session.execute(
            select(RawPrice).where(RawPrice.security_id == 1, RawPrice.trade_date == date(2026, 6, 1))
        ).scalar_one()
        price_2 = self.session.execute(
            select(RawPrice).where(RawPrice.security_id == 1, RawPrice.trade_date == date(2026, 6, 2))
        ).scalar_one()

        self.assertEqual(float(price_1.close), 1450.0)
        self.assertEqual(price_1.volume, 200000)
        self.assertEqual(float(price_2.close), 1460.0)
        self.assertEqual(price_2.volume, 80000)


if __name__ == "__main__":
    unittest.main()
