import asyncio
import sys
from loguru import logger

# Configure loguru to write to stdout
logger.remove()
logger.add(sys.stdout, level="INFO")

async def test_cookies():
    from src.services.nse_cookie_provider import NSECookieProvider
    
    print("Initializing NSECookieProvider...")
    provider = NSECookieProvider()
    try:
        print("Fetching cookies via Selenium (force_refresh=True)...")
        cookies = await provider.get_cookies(force_refresh=True)
        print("\nCookies fetched successfully!")
        print("-" * 50)
        for k, v in cookies.items():
            # Truncate long cookie values for readability
            val_display = v if len(v) <= 40 else v[:37] + "..."
            print(f"  {k}: {val_display}")
        print("-" * 50)
        
        # Verify some typical NSE cookies
        important_cookies = ["_abck", "bm_sv", "nsit"]
        found_important = [c for c in important_cookies if c in cookies]
        print(f"Found important cookies: {found_important}")
        if not cookies:
            print("FAILED: Cookie dictionary is empty!")
            return False
            
        print("SUCCESS: Cookie extraction flow works perfectly!")
        return True
    except Exception as e:
        print(f"ERROR: Failed cookie flow: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        print("Closing cookie provider browser session...")
        provider.close()

if __name__ == "__main__":
    # Run async test
    asyncio.run(test_cookies())
