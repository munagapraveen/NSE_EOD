import unittest
from src.services.corporate_actions import parse_corporate_action_text

class TestCorporateActionParsing(unittest.TestCase):

    def test_stock_splits_parsing(self):
        """Verify that various stock split strings are parsed correctly."""
        test_cases = [
            # text, expected_old_fv, expected_new_fv, expected_factor
            ("Sub-division of shares of Rs 10/- each to Rs 2/- each", 10.0, 2.0, 5.0),
            ("Subdivision of face value of Rs 10 to Rs 1 each", 10.0, 1.0, 10.0),
            ("Stock Split of face value Rs. 10/- to Rs. 5/-", 10.0, 5.0, 2.0),
            ("Sub-division of Equity Shares from face value of Rs. 10/- each to Re. 1/- each", 10.0, 1.0, 10.0),
            ("Sub-division of Equity Shares of face value of Rs. 10/- each into Equity Shares of Re. 1/- each.", 10.0, 1.0, 10.0),
            ("Sub-division of Rs. 10/- into 10 Equity Shares of Re. 1/- each.", 10.0, 1.0, 10.0),
            ("Face Value Split (Sub-Division) - From Rs. 10/- to Re. 1/-", 10.0, 1.0, 10.0),
            ("Split of Rs. 5 each to Rs. 2 each", 5.0, 2.0, 2.5),
        ]
        
        for text, old_fv, new_fv, factor in test_cases:
            with self.subTest(text=text):
                res = parse_corporate_action_text(text)
                self.assertIsNotNone(res)
                self.assertEqual(res["action_type"], "SPLIT")
                self.assertEqual(res["old_face_value"], old_fv)
                self.assertEqual(res["new_face_value"], new_fv)
                self.assertIsNone(res["bonus_ratio_new"])
                self.assertIsNone(res["bonus_ratio_existing"])
                self.assertAlmostEqual(res["adjustment_factor"], factor)

    def test_bonus_issues_parsing(self):
        """Verify that various bonus issue strings are parsed correctly."""
        test_cases = [
            # text, expected_new_ratio, expected_existing_ratio, expected_factor
            ("Bonus issue in the ratio of 1:1", 1, 1, 2.0),
            ("Bonus Issue 2:1", 2, 1, 3.0),
            ("Bonus issue of 3:2", 3, 2, 2.5),
            ("Bonus 10:1", 10, 1, 11.0),
            ("Bonus issue in ratio of 1:10", 1, 10, 1.1),
            ("Bonus shares issued 1 share for every 2 shares held", 1, 2, 1.5),
            ("Bonus 1 share for 5 shares", 1, 5, 1.2),
        ]
        
        for text, new_ratio, existing_ratio, factor in test_cases:
            with self.subTest(text=text):
                res = parse_corporate_action_text(text)
                self.assertIsNotNone(res)
                self.assertEqual(res["action_type"], "BONUS")
                self.assertIsNone(res["old_face_value"])
                self.assertIsNone(res["new_face_value"])
                self.assertEqual(res["bonus_ratio_new"], new_ratio)
                self.assertEqual(res["bonus_ratio_existing"], existing_ratio)
                self.assertAlmostEqual(res["adjustment_factor"], factor)

    def test_non_matching_texts(self):
        """Verify that irrelevant texts return None."""
        test_cases = [
            "Dividend of Rs. 5 per share",
            "Annual General Meeting on June 15, 2026",
            "Change of registered office address",
            "Book closure for dividend payout",
        ]
        
        for text in test_cases:
            with self.subTest(text=text):
                res = parse_corporate_action_text(text)
                self.assertIsNone(res)

if __name__ == "__main__":
    unittest.main()
