"""
Investigate EQUITY_L.csv PAID UP VALUE field.
Hypothesis: PAID UP VALUE = total paid-up capital in some unit
FACE VALUE = per share face value
Therefore: issued_shares = PAID UP VALUE / FACE VALUE * some_multiplier
"""
import urllib.request
import io
import pandas as pd

url = 'https://archives.nseindia.com/content/equities/EQUITY_L.csv'
with urllib.request.urlopen(url, timeout=20) as r:
    df = pd.read_csv(io.StringIO(r.read().decode('utf-8', errors='replace')))

df.columns = df.columns.str.strip()
print("Columns:", list(df.columns))
print()

# Known companies with known issued shares for validation
test_cases = {
    'RELIANCE': {'known_shares': 13_506_294_750, 'face_value': 10},  # ~1350 crore shares, FV=10
    'TCS':      {'known_shares': 3_664_919_514,  'face_value': 1},
    'INFY':     {'known_shares': 4_163_070_390,  'face_value': 5},
    'HDFCBANK': {'known_shares': 7_634_175_510,  'face_value': 1},
}

for symbol, expected in test_cases.items():
    row = df[df['SYMBOL'] == symbol]
    if not row.empty:
        r = row.iloc[0]
        paid_up = r.get('PAID UP VALUE', 'N/A')
        face_val = r.get('FACE VALUE', 'N/A')
        
        print(f"{symbol}:")
        print(f"  PAID UP VALUE: {paid_up}")
        print(f"  FACE VALUE:    {face_val}")
        
        # Try to derive shares
        if paid_up != 'N/A' and face_val != 'N/A' and float(face_val) > 0:
            # If PAID UP VALUE is in lakhs, crores, or rupees
            for unit_name, multiplier in [("rupees", 1), ("thousands", 1000), ("lakhs", 100000), ("crores", 10000000)]:
                derived = float(paid_up) * multiplier / float(face_val)
                actual = expected['known_shares']
                ratio = derived / actual if actual > 0 else 0
                if 0.9 < ratio < 1.1:
                    print(f"  --> MATCH with unit={unit_name}: derived={derived:,.0f}, actual={actual:,.0f}, ratio={ratio:.4f}")
                else:
                    print(f"  unit={unit_name}: derived={derived:,.0f} (ratio={ratio:.4f})")
        print()
