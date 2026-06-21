"""
Use Playwright to open NSE in a real browser and fetch quotes via JavaScript
directly within the browser page (bypassing TLS fingerprint issues).
"""
import asyncio
import json
from playwright.async_api import async_playwright


async def test():
    print("Step 1: Launching Chromium...")
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    page = await context.new_page()
    
    print("Step 2: Navigating to NSE quotes page...")
    try:
        # Try with 'load' instead of 'networkidle' - more lenient
        await page.goto(
            "https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE",
            wait_until="load",
            timeout=30000
        )
        print(f"  Page loaded. Title: {await page.title()}")
    except Exception as e:
        print(f"  Page load error: {e}")
        # Even if it errors, try to continue — the page may have partially loaded
    
    await asyncio.sleep(5)  # Let JavaScript execute and Akamai resolve
    
    print("Step 3: Fetching quote via browser JavaScript...")
    try:
        result = await page.evaluate("""
            async () => {
                try {
                    const resp = await fetch('/api/quote-equity?symbol=RELIANCE', {
                        headers: {
                            'Accept': 'application/json, text/plain, */*',
                        }
                    });
                    if (!resp.ok) {
                        return { error: `HTTP ${resp.status}`, status: resp.status };
                    }
                    const data = await resp.json();
                    return {
                        status: 200,
                        issuedSize: data?.securityInfo?.issuedSize || null,
                        lastPrice: data?.priceInfo?.lastPrice || null,
                        totalMarketCap: data?.marketDeptOrderBook?.tradeInfo?.totalMarketCap || null,
                    };
                } catch (e) {
                    return { error: e.message };
                }
            }
        """)
        print(f"  Result: {json.dumps(result, indent=2)}")
    except Exception as e:
        print(f"  JavaScript execution error: {e}")
    
    await browser.close()
    await pw.stop()
    print("\nDone!")


asyncio.run(test())
