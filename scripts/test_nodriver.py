"""
Test using nodriver (real Chrome) to get valid NSE cookies that can bypass Akamai.

Strategy:
1. Launch a real Chrome browser via nodriver
2. Visit NSE homepage — Chrome solves the Akamai challenge (JavaScript sensor data)
3. Extract cookies from the browser session
4. Use those cookies with curl_cffi to make API calls
"""
import asyncio
import nodriver as uc
from curl_cffi.requests import AsyncSession


async def test():
    print("Step 1: Launching Chrome via nodriver...")
    browser = await uc.start(headless=True)
    
    print("Step 2: Visiting NSE homepage (Chrome will solve Akamai challenge)...")
    page = await browser.get("https://www.nseindia.com")
    
    # Wait for the page to fully load and Akamai to set proper cookies
    await asyncio.sleep(5)
    
    print("Step 3: Extracting cookies from browser...")
    # Get cookies from the browser
    browser_cookies = await browser.cookies.get_all()
    print(f"  Got {len(browser_cookies)} cookies from browser")
    
    # Convert to dict for curl_cffi
    cookie_dict = {}
    for cookie in browser_cookies:
        name = cookie.name if hasattr(cookie, 'name') else cookie.get('name', '')
        value = cookie.value if hasattr(cookie, 'value') else cookie.get('value', '')
        if name:
            cookie_dict[name] = value
            print(f"  {name}: {value[:50]}...")
    
    print("\nStep 4: Using cookies with curl_cffi for API call...")
    session = AsyncSession(impersonate="chrome120", timeout=15, cookies=cookie_dict)
    
    r = await session.get(
        "https://www.nseindia.com/api/quote-equity?symbol=RELIANCE",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE",
        },
    )
    print(f"  API status: {r.status_code}")
    
    if r.status_code == 200:
        data = r.json()
        issued = data.get("securityInfo", {}).get("issuedSize", "N/A")
        price = data.get("priceInfo", {}).get("lastPrice", "N/A")
        print(f"  [OK] issuedSize: {issued}")
        print(f"  [OK] lastPrice: {price}")
    else:
        print(f"  [FAIL] Still getting {r.status_code}")
        print(f"  Response body (first 500 chars): {r.text[:500]}")
    
    await session.close()
    browser.stop()
    print("\nDone!")


asyncio.run(test())
