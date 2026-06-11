import os
import sys
from datetime import date

# Add workspace root to python path so we can import src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.db.engine import SessionLocal
from src.services.screener import run_sharpe_screener, export_screener_to_excel, get_closest_trading_date


def test_screener():
    print("=" * 60)
    print("RUNNING AUTOMATED TEST FOR SHARPE SCREENER")
    print("=" * 60)
    
    session = SessionLocal()
    try:
        # Check target date
        target = date(2025, 5, 21)
        closest_date = get_closest_trading_date(session, target)
        print(f"Target date: {target} | Closest trade date: {closest_date}")
        
        # Run screener calculations
        print("Running Sharpe Screener calculations...")
        df_all = run_sharpe_screener(
            session=session,
            target_date=closest_date,
            long_months=6,
            short_months=3,
            mcap_filter_cr=1000.0,
            roc_annual_filter=6.5,
            turnover_filter_cr=1.0
        )
        
        if df_all.empty:
            print("FAILED: Sharpe Screener returned empty dataframe.")
            return
            
        print(f"\nSUCCESS: Sharpe Screener returned {len(df_all)} passing stocks.")
        
        # Verify columns exist
        required_cols = [
            "symbol", "company_name", "close", "sharpe_6", "sharpe_3",
            "Avg_sharpe_6_3_Rank", "ROC_annual", "median_turnover_cr",
            "total_circuit_hits_3m", "dma_200", "away_52wh"
        ]
        missing_cols = [c for c in required_cols if c not in df_all.columns]
        if missing_cols:
            print(f"FAILED: Missing columns in results: {missing_cols}")
            return
        print("SUCCESS: All required metrics columns are present.")
        
        # Print top 5 stocks
        print("\n--- TOP 5 RANKED STOCKS ---")
        top_5 = df_all.head(5)
        for idx, row in top_5.iterrows():
            print(
                f"{idx+1}. {row['symbol']} ({row['company_name'][:20]}) | "
                f"Close: Rs.{row['close']:.2f} | "
                f"Combined Rank Sum: {row['Avg_sharpe_6_3_Rank']} | "
                f"Sharpe 6M: {row['sharpe_6']:.4f} (Rank {row['sharpe_6_rank']}) | "
                f"Sharpe 3M: {row['sharpe_3']:.4f} (Rank {row['sharpe_3_rank']}) | "
                f"Annual ROC: {row['ROC_annual']:.2f}% | "
                f"Med Turnover: Rs.{row['median_turnover_cr']:.2f} Cr"
            )
            
        # Apply High-Conviction watchlist filters
        df_filtered = df_all.copy()
        df_filtered = df_filtered[
            (df_filtered["ROC_3"] > 20.0) &
            (df_filtered["away_52wh"] >= -25.0) &
            (df_filtered["total_circuit_hits_3m"] <= 10)
        ].reset_index(drop=True)
        
        print(f"\nSUCCESS: High-Conviction Watchlist has {len(df_filtered)} stocks.")
        if not df_filtered.empty:
            print("--- TOP 3 WATCHLIST STOCKS ---")
            for idx, row in df_filtered.head(3).iterrows():
                print(
                    f"{idx+1}. {row['symbol']} | 3M ROC: {row['ROC_3']:.2f}% | "
                    f"Circuit Hits: {row['total_circuit_hits_3m']} | "
                    f"Away 52WH: {row['away_52wh']:.2f}%"
                )
                
        # Test Excel Export
        print("\nTesting Excel workbook export...")
        file_path = export_screener_to_excel(
            df_all=df_all,
            df_filtered=df_filtered,
            target_date=closest_date,
            long_months=6,
            short_months=3
        )
        
        if os.path.exists(file_path):
            print(f"SUCCESS: Excel workbook successfully saved to {file_path}")
            print(f"File Size: {os.path.getsize(file_path):,} bytes")
        else:
            print("FAILED: Excel export did not write the file.")
            
    except Exception as e:
        print(f"FAILED: Exception occurred: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()
    print("=" * 60)


if __name__ == "__main__":
    test_screener()
