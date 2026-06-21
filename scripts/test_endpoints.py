"""
Test various NSE API endpoints that might return issued shares data
without requiring Akamai-solved cookies.
"""
import asyncio
from curl_cffi.requests import AsyncSession


async def test():
    s = AsyncSession(impersonate="chrome120", timeout=15)
    
    # Warm up - get some cookies
    await s.get("https://www.nseindia.com")
    await asyncio.sleep(2)
    await s.get("https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE")
    await asyncio.sleep(2)
    
    print(f"Cookies available: {list(s.cookies.keys())}")
    
    # Test various endpoints
    endpoints = [
        ("equity-meta-info", "https://www.nseindia.com/api/equity-meta-info?symbol=RELIANCE"),
        ("trade-info", "https://www.nseindia.com/api/quote-equity?symbol=RELIANCE&section=trade_info"),
        ("market-turnover", "https://www.nseindia.com/api/market-turnover"),
        ("chart-data", "https://www.nseindia.com/api/historical/cm/equity?symbol=RELIANCE&series=EQ&from=01-01-2025&to=10-01-2025&dataType=allEntries"),
        ("corp-info", "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=RELIANCE"),
    ]
    
    for name, url in endpoints:
        print(f"\n--- {name} ---")
        try:
            r = await s.get(
                url,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE",
                }
            )
            print(f"  Status: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    # Print top-level keys
                    print(f"  Keys: {list(data.keys())[:10]}")
                    # Look for issued/shares/capital keywords
                    def find_issued(obj, depth=0):
                        if depth > 4:
                            return
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                if any(kw in str(k).lower() for kw in ['issued', 'shares', 'capital', 'outstanding']):
                                    print(f"    Found: {k} = {v}")
                                find_issued(v, depth+1)
                    find_issued(data)
                elif isinstance(data, list) and len(data) > 0:
                    print(f"  List of {len(data)} items, first item keys: {list(data[0].keys())[:10] if isinstance(data[0], dict) else 'N/A'}")
            else:
                print(f"  Body: {r.text[:200]}")
        except Exception as e:
            print(f"  Error: {e}")
        await asyncio.sleep(2)
    
    await s.close()


asyncio.run(test())
