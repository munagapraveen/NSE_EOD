import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ui.pages.corporate_actions import get_corporate_actions
from src.models import Base, Security, CorporateAction
from src.db.engine import create_engine, sessionmaker

class TestUiCorporateActions(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("duckdb:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

    def tearDown(self):
        self.session.close()

    def test_get_corporate_actions_guards_none_and_fractional(self):
        # 1. Create a security
        sec = Security(
            symbol="TESTSEC",
            company_name="Test Company",
            isin="INE123456789",
            security_type="STOCK",
            is_active=True
        )
        self.session.add(sec)
        self.session.flush()

        # 2. Add corporate actions:
        # A. None face values
        ca_none = CorporateAction(
            security_id=sec.id,
            action_type="SPLIT",
            ex_date=date(2026, 6, 10),
            description="Split with None face values",
            old_face_value=None,
            new_face_value=None,
            adjustment_factor=2.0
        )
        # B. Normal integer face values
        ca_int = CorporateAction(
            security_id=sec.id,
            action_type="SPLIT",
            ex_date=date(2026, 6, 11),
            description="Split with integer face values",
            old_face_value=10.0,
            new_face_value=5.0,
            adjustment_factor=2.0
        )
        # C. Fractional face values (if any, though rare)
        ca_float = CorporateAction(
            security_id=sec.id,
            action_type="SPLIT",
            ex_date=date(2026, 6, 12),
            description="Split with fractional face values",
            old_face_value=7.5,
            new_face_value=2.5,
            adjustment_factor=3.0
        )
        # D. Bonus action (no face values used in formatting)
        ca_bonus = CorporateAction(
            security_id=sec.id,
            action_type="BONUS",
            ex_date=date(2026, 6, 13),
            description="Bonus 1:1",
            bonus_ratio_new=1,
            bonus_ratio_existing=1,
            adjustment_factor=2.0
        )
        self.session.add_all([ca_none, ca_int, ca_float, ca_bonus])
        self.session.commit()

        # Patch the SessionLocal in src.ui.pages.corporate_actions to return our in-memory session
        with patch("src.ui.pages.corporate_actions.SessionLocal", return_value=self.session):
            actions_data = get_corporate_actions()

        # Should retrieve all 4 records and not crash!
        self.assertEqual(len(actions_data), 4)

        # Let's map records by description to verify detail strings
        mapped = {a["description"]: a["detail"] for a in actions_data}

        self.assertEqual(mapped["Split with None face values"], "Split - → - (Factor: 2.0)")
        self.assertEqual(mapped["Split with integer face values"], "Split 10 → 5 (Factor: 2.0)")
        self.assertEqual(mapped["Split with fractional face values"], "Split 7.5 → 2.5 (Factor: 3.0)")
        self.assertEqual(mapped["Bonus 1:1"], "Bonus 1:1 (Factor: 2.0)")

if __name__ == "__main__":
    unittest.main()
