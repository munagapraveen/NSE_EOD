"""
Test NSE FO (Futures & Options) bhavcopy for issued capital data.
NSE's FO bhavcopy ZIP contains data for F&O eligible stocks including lot size
and possibly issued cap.

URL pattern: https://archives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip
"""
import asyncio
import zipfile
import io
import pandas as pd
from datetime import date, timedelta
from curl_cffi.requests import AsyncSession


async def test():
    s = AsyncSession(impersonate="chrome120", timeout=30)
    
    today = date.today()
    
    print("=== Testing NSE FO Bhavcopy ===\n")
    
    for days_back in range(1, 8):
        d = today - timedelta(days=days_back)
        date_str = d.strftime("%Y%m%d")
        
        # FO bhavcopy URL format
        url = f"https://archives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{date_str}_F_0000.csv.zip"
        
        try:
            r = await s.get(url, headers={"Accept": "*/*"})
            if r.status_code == 200:
                print(f"SUCCESS! FO Bhavcopy for {d}: {url}")
                with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                    fname = zf.namelist()[0]
                    print(f"  File inside ZIP: {fname}")
                    with zf.open(fname) as f:
                        df = pd.read_csv(f)
                df.columns = df.columns.str.strip()
                print(f"  Columns ({len(df.columns)}): {list(df.columns)}")
                print(f"  Rows: {len(df)}")
                print(f"  First row: {df.iloc[0].to_dict()}")
                print(f"  TckrSymb unique types: {df['FinInstrmTp'].unique() if 'FinInstrmTp' in df.columns else 'N/A'}")
                
                # Look for shares/issued/capital columns
                share_cols = [c for c in df.columns if any(kw in c.lower() for kw in ['issued', 'shares', 'cap', 'outstanding'])]
                if share_cols:
                    print(f"  SHARES COLUMNS FOUND: {share_cols}")
                    rel = df[df['TckrSymb'] == 'RELIANCE'] if 'TckrSymb' in df.columns else pd.DataFrame()
                    if not rel.empty:
                        print(f"  RELIANCE: {rel[share_cols].iloc[0].to_dict()}")
                else:
                    print("  No issued/shares/cap columns found in FO bhavcopy")
                break
            else:
                print(f"  {d}: {r.status_code}")
        except Exception as e:
            print(f"  {d}: ERROR - {e}")
        await asyncio.sleep(0.5)
    
    print("\n=== Testing NSE Securities in F&O List ===")
    url2 = "https://archives.nseindia.com/content/fo/fo_underlyinglist.csv"
    r2 = await s.get(url2, headers={"Accept": "*/*"})
    print(f"fo_underlyinglist: {r2.status_code}")
    if r2.status_code == 200:
        df2 = pd.read_csv(io.StringIO(r2.text))
        df2.columns = df2.columns.str.strip()
        print(f"  Columns: {list(df2.columns)}")
        print(f"  Rows: {len(df2)}")
        if not df2.empty:
            print(f"  First 3:\n{df2.head(3).to_string()}")
    
    await s.close()


asyncio.run(test())
