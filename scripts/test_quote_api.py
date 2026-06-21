"""
Test script to verify BSE outstanding shares lookup and parser flow.
Tests with 3 well-known stocks via their ISIN: RELIANCE, TCS, INFY.

Run with: python -m scripts.test_quote_api
"""
import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.bse_client import BSEClient


TEST_ISINS = {
    "RELIANCE": "INE002A01018",
    "TCS": "INE467B01029",
    "INFY": "INE009A01021"
}


async def test_bse_shares():
    client = BSEClient()
    
    print("=" * 70)
    print("BSE Outstanding Shares Client Test")
    print("=" * 70)
    
    results = []
    
    for symbol, isin in TEST_ISINS.items():
        print(f"\n--- Testing {symbol} (ISIN: {isin}) ---")
        try:
            # 1. Resolve scrip code
            scrip_code = await client.lookup_scripcode_by_isin(isin)
            if not scrip_code:
                print(f"  [FAIL] Could not resolve scrip code for ISIN {isin}")
                results.append({"symbol": symbol, "status": "FAILED", "error": "No scrip code"})
                continue
                
            print(f"  [OK] Resolved scrip code: {scrip_code}")
            
            # 2. Fetch outstanding shares
            shares, qtr_date = await client.fetch_outstanding_shares(scrip_code)
            
            if shares and shares > 0:
                print(f"  [OK] Outstanding Shares: {shares:,}")
                print(f"  [OK] Shareholding Pattern Quarter: {qtr_date}")
                results.append({"symbol": symbol, "status": "OK", "shares": shares})
            else:
                print(f"  [FAIL] Outstanding shares parsed invalid value: {shares}")
                results.append({"symbol": symbol, "status": "FAILED", "error": f"Invalid shares value: {shares}"})
                
        except Exception as e:
            print(f"  [FAIL] Exception raised: {e}")
            results.append({"symbol": symbol, "status": "FAILED", "error": str(e)})
        
        # Jitter pause
        await asyncio.sleep(1.0)
        
    await client.close()
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    ok_count = sum(1 for r in results if r["status"] == "OK")
    fail_count = sum(1 for r in results if r["status"] == "FAILED")
    print(f"  Passed: {ok_count}/{len(results)}")
    print(f"  Failed: {fail_count}/{len(results)}")
    
    if fail_count > 0:
        print("\n  WARNING: Some tests failed. Check the errors above.")
        return False
    else:
        print("\n  SUCCESS: All tests passed! BSE client pipeline is working correctly.")
        return True


if __name__ == "__main__":
    success = asyncio.run(test_bse_shares())
    sys.exit(0 if success else 1)
