"""
Test NSE API with curl_cffi's http_version and akamai parameters.
"""
import asyncio
from curl_cffi.requests import AsyncSession
from curl_cffi import CurlHttpVersion


async def test():
    print("=== Test 1: With akamai fingerprint ===")
    s = AsyncSession(impersonate="chrome120", timeout=15)
    
    r1 = await s.get("https://www.nseindia.com")
    print(f"Homepage: {r1.status_code}, cookies: {list(s.cookies.keys())}")
    await asyncio.sleep(2)
    
    r2 = await s.get("https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE")
    print(f"Quotes page: {r2.status_code}, cookies: {list(s.cookies.keys())}")
    await asyncio.sleep(2)
    
    r3 = await s.get(
        "https://www.nseindia.com/api/quote-equity?symbol=RELIANCE",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE",
        }
    )
    print(f"API (default): {r3.status_code}")
    
    await asyncio.sleep(2)
    
    # Try with HTTP/1.1 explicitly
    r4 = await s.get(
        "https://www.nseindia.com/api/quote-equity?symbol=RELIANCE",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE",
        },
        http_version=CurlHttpVersion.V1_1
    )
    print(f"API (HTTP/1.1): {r4.status_code}")
    if r4.status_code == 200:
        data = r4.json()
        issued = data.get("securityInfo", {}).get("issuedSize", "N/A")
        print(f"  issuedSize: {issued}")
    
    await s.close()
    
    print("\n=== Test 2: Longer wait between page load and API ===")
    s2 = AsyncSession(impersonate="chrome131", timeout=15)
    
    await s2.get("https://www.nseindia.com")
    print(f"Homepage: {list(s2.cookies.keys())}")
    await asyncio.sleep(5)  # Longer wait
    
    await s2.get("https://www.nseindia.com/market-data/all-reports")
    print(f"After market-data page: {list(s2.cookies.keys())}")
    await asyncio.sleep(5)
    
    r = await s2.get(
        "https://www.nseindia.com/api/quote-equity?symbol=RELIANCE",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE",
        }
    )
    print(f"API (after longer session): {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"  issuedSize: {data.get('securityInfo', {}).get('issuedSize', 'N/A')}")
    
    await s2.close()


asyncio.run(test())
