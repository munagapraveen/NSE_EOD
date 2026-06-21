import asyncio
from src.services.nse_client import NSEClient

async def test_client():
    print("Initializing NSEClient (Selenium-free)...")
    client = NSEClient()
    
    try:
        from_date = "10-06-2026"
        to_date = "13-06-2026"
        print(f"Fetching corporate actions from {from_date} to {to_date}...")
        actions = await client.fetch_corporate_actions(from_date, to_date)
        
        print("\nAPI Response received successfully!")
        print(f"Total actions retrieved: {len(actions)}")
        print("-" * 50)
        for act in actions[:5]:
            print(f"Symbol:  {act.get('symbol')}")
            print(f"Subject: {act.get('subject')}")
            print(f"Ex-Date: {act.get('exDate')}")
            print("-" * 30)
        print("-" * 50)
        
        if not actions or not isinstance(actions, list):
            print("FAILED: No actions parsed or unexpected data format.")
            return False
            
        print("SUCCESS: Corporate actions fetched and parsed successfully without Selenium!")
        return True
        
    except Exception as e:
        print(f"ERROR: Client fetching failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        print("Closing NSEClient...")
        await client.close()

if __name__ == "__main__":
    asyncio.run(test_client())
