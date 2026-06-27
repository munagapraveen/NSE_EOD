import sys
import os

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.corporate_actions import parse_corporate_action_text

def test_split_parsing_edge_cases():
    print("Running split parsing edge case tests...")
    
    # 1. Text with a clause number matching the old pattern
    text_with_clause = "Sub-division of shares of Rs 10/- each to Rs 2/- each under clause 4/-"
    res = parse_corporate_action_text(text_with_clause)
    
    assert res is not None, "Failed to parse split with clause"
    assert res["action_type"] == "SPLIT"
    assert res["old_face_value"] == 10.0, f"Expected old FV 10.0, got {res['old_face_value']}"
    assert res["new_face_value"] == 2.0, f"Expected new FV 2.0, got {res['new_face_value']}"
    assert res["adjustment_factor"] == 5.0, f"Expected factor 5.0, got {res['adjustment_factor']}"
    print("  [PASS] Successfully ignored clause number formatted with '/-'")

    # 2. Text with standard split text
    text_standard = "Subdivision of face value of Rs 10 to Rs 1 each"
    res2 = parse_corporate_action_text(text_standard)
    assert res2 is not None
    assert res2["old_face_value"] == 10.0
    assert res2["new_face_value"] == 1.0
    print("  [PASS] Successfully parsed standard split text")

    # 3. Text with section and clause number
    text_complex = "Stock Split of face value Rs. 10/- to Rs. 5/- pursuant to Section 18/- and Clause 2/-"
    res3 = parse_corporate_action_text(text_complex)
    assert res3 is not None
    assert res3["old_face_value"] == 10.0
    assert res3["new_face_value"] == 5.0
    print("  [PASS] Successfully ignored Section and Clause numbers in complex split text")
    
    print("\nAll split parsing edge case tests PASSED successfully!")

if __name__ == "__main__":
    test_split_parsing_edge_cases()
