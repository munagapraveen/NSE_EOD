"""All NSE URLs, series types, headers, and indicator configurations."""

from datetime import date

# ============================================================
# NSE ARCHIVE URLs (Low protection — User-Agent header only)
# ============================================================

# Daily bhavcopy in UDiFF format (contains ALL equity series + ETFs)
# Format: BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip
BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{date}_F_0000.csv.zip"
)

# Index daily close values (all indexes in one CSV)
# Format: ind_close_all_{DDMMYYYY}.csv
INDEX_CLOSE_URL = (
    "https://nsearchives.nseindia.com/content/indices/"
    "ind_close_all_{date}.csv"
)

# Stock master list
EQUITY_LIST_URL = (
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
)

# ETF master list
ETF_LIST_URL = (
    "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv"
)

# Symbol changes history
SYMBOL_CHANGE_URL = (
    "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"
)


# ============================================================
# NSE API URLs (High protection — requires session cookies)
# ============================================================

NSE_BASE_URL = "https://www.nseindia.com"

# Corporate actions (splits, bonus, dividends)
# Query params: index=equities, from_date=DD-MM-YYYY, to_date=DD-MM-YYYY, symbol=SYMBOL
CORPORATE_ACTIONS_URL = (
    "https://www.nseindia.com/api/corporates-corporateActions"
)

# Stock quote (contains issuedSize for market cap)
# Query params: symbol=SYMBOL
STOCK_QUOTE_URL = (
    "https://www.nseindia.com/api/quote-equity"
)

# NextApi GetQuoteApi — richer payload per symbol
# Returns issuedSize, totalMarketCap, ffmc, faceValue, sector, industryInfo, indexList
# Query params: functionName=getSymbolData, marketType=N, series=EQ, symbol=SYMBOL
GET_QUOTE_API_URL = (
    "https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi"
)

# Historical index data (alternative to daily CSV)
INDEX_HISTORY_URL = (
    "https://www.nseindia.com/api/historical/indicesHistory"
)

# ============================================================
# Series Types
# ============================================================

# EQ = Regular equity, BE = Trade-to-Trade (no intraday), BZ = same as BE
EQUITY_SERIES = ["EQ", "BE"]

# ============================================================
# UDiFF Bhavcopy Column Mapping
# ============================================================

UDIFF_COLUMNS = {
    "TradDt": "trade_date",
    "FinInstrmId": "symbol",
    "ISIN": "isin",
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close",
    "LastPric": "last_price",
    "PrvsClsgPric": "prev_close",
    "TtlTradgVol": "volume",
    "TtlTrfVal": "turnover",
    "TtlNbOfTxsExctd": "total_trades",
    "SctySrs": "series",       # EQ, BE, BZ, etc.
    "FinInstrmTp": "instrument_type",  # STK, ETF, etc.
}

# ============================================================
# HTTP Headers (required by NSE)
# ============================================================

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

# Accept header override for JSON API calls (quote-equity, corporate actions, etc.)
# NSE's API endpoints reject requests that don't advertise JSON acceptance.
NSE_API_ACCEPT = "application/json, text/plain, */*"

# ============================================================
# BSE Shareholding Pattern Quarter Anchor Configuration
# ============================================================
BSE_ANCHOR_DATE = date(2026, 3, 31)
BSE_ANCHOR_QTRID = 129

# ============================================================
# Tracked Indexes configuration (whitelist)
# ============================================================
TRACKED_INDEXES = [
    "NIFTY 50",
    "NIFTY NEXT 50",
    "NIFTY 100",
    "NIFTY 200",
    "NIFTY 500",
    "NIFTY MIDCAP 50",
    "NIFTY MIDCAP 100",
    "NIFTY SMALLCAP 50",
    "NIFTY SMALLCAP 100",
    "NIFTY BANK",
    "NIFTY IT",
    "NIFTY PHARMA",
    "NIFTY AUTO",
    "NIFTY FMCG",
    "NIFTY METAL",
    "NIFTY ENERGY",
    "NIFTY INFRA",
    "NIFTY REALTY",
    "NIFTY FIN SERVICE",
    "NIFTY MEDIA",
    "NIFTY PSE",
    "NIFTY CPSE",
    "INDIA VIX",
    "NIFTY COMMODITIES",
    "NIFTY CONSUMPTION",
    "NIFTY PSU BANK",
    "NIFTY PRIVATE BANK",
]


# ============================================================
# Financial Constants
# ============================================================
CRORE = 10_000_000.0

