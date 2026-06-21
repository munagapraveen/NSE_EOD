import os
import sys
from datetime import date, datetime
import openpyxl
import pandas as pd

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.screener import export_screener_to_excel

def test_excel_target_date_header():
    print("Running test_excel_target_date_header...")
    
    # 1. Create mock dataframes
    df_all = pd.DataFrame([
        {
            "symbol": "TESTSTOCK",
            "company_name": "Test Stock Ltd",
            "industry": "IT",
            "close": 150.25,
            "dma_20": 145.0,
            "dma_50": 140.0,
            "dma_100": 135.0,
            "dma_200": 130.0,
            "away_52wh": -2.5,
            "total_circuit_hits_3m": 2,
            "Avg_sharpe_6_3_Rank": 4,
            "sharpe_6": 1.5,
            "sharpe_3": 1.8,
            "ROC_6": 12.0,
            "ROC_3": 5.0,
            "week_52_high": 155.0,
            "market_cap_cr": 1200.0,
            "ROC_annual": 25.0,
            "median_turnover_cr": 1.5,
            "sharpe_6_rank": 2,
            "sharpe_3_rank": 2,
            "isin": "INE123A01015"
        }
    ])
    
    # High Conviction filtered match
    df_filtered = df_all.copy()
    
    target_date = date(2026, 6, 12)
    
    # 2. Run the export function
    print("Calling export_screener_to_excel with target_date = 2026-06-12...")
    out_path = export_screener_to_excel(df_all, df_filtered, target_date, long_months=6, short_months=3)
    print(f"Excel report generated at: {out_path}")
    
    # 3. Read generated Excel file and verify headers
    wb = openpyxl.load_workbook(out_path)
    
    today_str = datetime.today().strftime("%d %b %Y")
    expected_target_str = "12 Jun 2026"
    
    # Check sheet 1: All Stocks
    ws1 = wb["All Stocks"]
    title1 = ws1["A1"].value
    print(f"Sheet 1 title cell A1 value: '{title1}'")
    
    expected_title1 = f"NSE Sharpe Screener -- All Ranked Stocks (6M / 3M)  |  Screening Date: {expected_target_str}  |  Exported: {today_str}"
    assert title1 == expected_title1, f"Expected Sheet 1 A1: '{expected_title1}', got: '{title1}'"
    print("Sheet 1 header looks perfect!")
    
    # Check sheet 2: Filtered
    ws2 = wb["Filtered"]
    title2 = ws2["A1"].value
    print(f"Sheet 2 title cell A1 value: '{title2}'")
    
    expected_title2 = f"Filtered Watchlist  |  3M ROC > 20%  |  Close >= 75% of 52WH  |  Circuit Hits <= 10  |  Screening Date: {expected_target_str}  |  Exported: {today_str}"
    assert title2 == expected_title2, f"Expected Sheet 2 A1: '{expected_title2}', got: '{title2}'"
    print("Sheet 2 header looks perfect!")
    
    print("\nALL HEADER TESTS PASSED SUCCESSFULLY!")
    
    # Clean up file
    try:
        os.remove(out_path)
        print("Cleaned up generated test report file.")
    except Exception as e:
        print(f"Could not clean up test file: {e}")

if __name__ == "__main__":
    test_excel_target_date_header()
