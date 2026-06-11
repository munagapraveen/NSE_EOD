# NSE EOD Data Management Application — Complete Implementation Specification

> **Purpose of this document**: This is a **self-contained, complete specification** designed so that any LLM or developer can implement the entire project from scratch without additional research. Every API endpoint, data format, business rule, and code pattern is documented here.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Technology Stack](#2-technology-stack)
3. [Project Structure](#3-project-structure)
4. [Environment & Setup](#4-environment--setup)
5. [Database Schema](#5-database-schema)
6. [NSE Data Sources — Complete Reference](#6-nse-data-sources--complete-reference)
7. [NSE HTTP Client Specification](#7-nse-http-client-specification)
8. [Data Ingestion Pipeline](#8-data-ingestion-pipeline)
9. [Corporate Actions Logic](#9-corporate-actions-logic)
10. [Price Adjustment Engine](#10-price-adjustment-engine)
11. [Market Cap Calculation](#11-market-cap-calculation)
12. [Technical Indicators Engine](#12-technical-indicators-engine)
13. [Symbol Change Handler](#13-symbol-change-handler)
14. [Sync Manager & Orchestration](#14-sync-manager--orchestration)
15. [UI Specification](#15-ui-specification)
16. [Error Handling & Resilience](#16-error-handling--resilience)
17. [Testing Strategy](#17-testing-strategy)
18. [Implementation Order](#18-implementation-order)
19. [Configuration Reference](#19-configuration-reference)
20. [Glossary](#20-glossary)

---

## 1. Project Overview

### What We're Building
A Python desktop application that:
1. **Downloads** daily EOD (End-of-Day) OHLCV data from NSE India for Stocks (EQ & BE series), ETFs, and Indexes
2. **Stores** data in an organized database (DuckDB for desktop, PostgreSQL for cloud)
3. **Calculates** Market Cap and Technical Indicators for each stock/date
4. **Fetches** Corporate Actions (splits & bonus) and adjusts historical stock prices accordingly
5. **Handles** Stock Symbol Changes automatically
6. **Provides** a rich desktop UI using NiceGUI (which can deploy as a web app later)

### Key Business Rules
- Market Cap is calculated ONCE from raw prices and NEVER adjusted for corporate actions
- Technical Indicators are calculated from ADJUSTED prices
- Raw price data is NEVER modified — adjusted prices are stored separately
- Historical data is loaded from a configurable start date (default: 01-01-2024)
- Subsequent runs only fetch data from last sync date to today
- **Historical Download is Bhavcopy-First**: During initial historical load, stocks are auto-discovered directly from bhavcopy data — NOT from the NSE master list. This ensures we capture delisted stocks, old symbols, and any securities that no longer appear in the current EQUITY_L.csv. The master list is used only to *enrich* discovered stocks with metadata (company name, industry, listing date, etc.) and for filtering during incremental daily updates.

### Future Scope (design for, don't implement yet)
- Stock Screener engine with custom filter queries
- Backtesting engine for trading strategies
- Advanced charting with drawing tools
- Cloud/web deployment (NiceGUI makes this trivial)

---

## 2. Technology Stack

### Exact Dependencies

```toml
# pyproject.toml
[project]
name = "nse-eod-manager"
version = "0.1.0"
description = "NSE EOD Data Management Desktop Application"
requires-python = ">=3.11"

dependencies = [
    # --- UI Framework ---
    "nicegui>=2.10",                # Desktop/Web UI (FastAPI + Quasar/Vue.js)

    # --- Database ---
    "sqlalchemy>=2.0.36",           # ORM & database abstraction
    "duckdb>=1.2",                  # Embedded columnar database (desktop)
    "duckdb-engine>=0.15",          # SQLAlchemy driver for DuckDB
    "alembic>=1.14",                # Database schema migrations

    # --- Data Processing ---
    "pandas>=2.2",                  # DataFrames for CSV parsing & indicator calc
    "pandas-ta>=0.3.14b1",         # 130+ technical indicators
    "numpy>=1.26",                  # Numerical operations

    # --- HTTP & Networking ---
    "httpx>=0.28",                  # Async HTTP client (better than requests)

    # --- External Data Sources ---
    "yfinance>=0.2.40",             # Yahoo Finance — historical shares outstanding for market cap

    # --- Configuration & Validation ---
    "pydantic>=2.10",               # Data validation & models
    "pydantic-settings>=2.7",       # Environment-based settings
    "python-dotenv>=1.0",           # .env file loading

    # --- Scheduling & Logging ---
    "apscheduler>=3.10",            # Background task scheduling
    "loguru>=0.7",                  # Structured logging

    # --- Utilities ---
    "python-dateutil>=2.9",         # Date parsing & trading calendar
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-cov>=6.0",
    "ruff>=0.8",                    # Linter + formatter
]

# For future cloud migration
cloud = [
    "asyncpg>=0.30",               # PostgreSQL async driver
    "psycopg2-binary>=2.9",        # PostgreSQL sync driver
]

[project.scripts]
nse-eod = "src.main:main"
```

### Why Each Technology

| Technology | Role | Why This One |
|-----------|------|--------------|
| **NiceGUI** | Desktop + Web UI | Python-native, uses Quasar/Vue.js for Material Design UI. `native=True` = desktop window. Remove it = instant web app. Built-in AG Grid, ECharts, Plotly. Built on FastAPI so REST API comes free. |
| **DuckDB** | Desktop database | Embedded (single file, zero config), columnar OLAP engine (10-100x faster than SQLite for analytical queries), excellent compression, native Pandas integration. |
| **SQLAlchemy 2.0** | ORM | Database abstraction — same models work on DuckDB and PostgreSQL. Just change connection string for cloud migration. |
| **httpx** | HTTP client | Async support, automatic connection pooling, timeout handling, better than `requests` for concurrent downloads. |
| **pandas-ta** | Technical indicators | 130+ indicators, pure Python (no C dependencies like TA-Lib), direct DataFrame integration. |
| **yfinance** | Historical shares data | Yahoo Finance provides quarterly shares outstanding for NSE stocks (`.NS` suffix). Used to validate/enhance reverse-engineered historical share counts for accurate market cap. |
| **Pydantic** | Data validation | Strict type checking critical for financial data integrity. Also powers settings management. |
| **APScheduler** | Task scheduling | Schedule daily auto-downloads, background processing tasks. |
| **Loguru** | Logging | Simpler API than stdlib logging, structured output, file rotation. |

---

## 3. Project Structure

```
d:\Praveen\gemini\nse_eod\
│
├── pyproject.toml                      # Project metadata & dependencies
├── README.md                           # Project documentation
├── .env                                # Environment config (gitignored)
├── .env.example                        # Template for .env
├── .gitignore                          # Git ignore rules
│
├── alembic/                            # Database migrations
│   ├── alembic.ini
│   ├── env.py                          # Migration environment config
│   └── versions/                       # Auto-generated migration files
│
├── config/
│   ├── __init__.py
│   ├── settings.py                     # Pydantic Settings class (reads .env)
│   ├── constants.py                    # NSE URLs, series types, index names
│   └── logging_config.py              # Loguru configuration
│
├── src/
│   ├── __init__.py
│   │
│   ├── main.py                         # App entry point (thin — just startup)
│   │
│   ├── models/                         # SQLAlchemy ORM models (DB tables)
│   │   ├── __init__.py                # Exports all models
│   │   ├── base.py                    # DeclarativeBase, common mixins (TimestampMixin)
│   │   ├── stock.py                   # Stock (master), StockPrice (raw OHLCV)
│   │   ├── adjusted_price.py          # AdjustedPrice (split/bonus adjusted)
│   │   ├── market_cap.py             # MarketCap
│   │   ├── index.py                   # Index (master), IndexPrice
│   │   ├── etf.py                     # ETF (master), ETFPrice
│   │   ├── indicator.py              # StockIndicator (technical indicators)
│   │   ├── corporate_action.py       # CorporateAction (splits, bonus)
│   │   ├── symbol_change.py          # SymbolChange
│   │   └── sync_log.py               # SyncLog (download tracking)
│   │
│   ├── db/                            # Database infrastructure
│   │   ├── __init__.py
│   │   ├── engine.py                  # create_engine, sessionmaker, get_session
│   │   └── repository.py             # Generic CRUD + bulk upsert helpers
│   │
│   ├── services/                      # Business logic layer
│   │   ├── __init__.py
│   │   ├── nse_client.py             # NSE HTTP client (session mgmt, rate limiting, retries)
│   │   ├── stock_downloader.py       # Download & parse equity bhavcopy (UDiFF format)
│   │   ├── index_downloader.py       # Download & parse index daily CSV
│   │   ├── etf_downloader.py         # Parse ETF data from bhavcopy
│   │   ├── master_data.py            # Download EQUITY_L.csv, eq_etfseclist.csv
│   │   ├── corporate_actions.py      # Fetch corporate actions from NSE API
│   │   ├── symbol_changes.py         # Fetch & apply symbol changes
│   │   ├── price_adjuster.py         # Calculate adjustment factors, generate adjusted prices
│   │   ├── market_cap.py             # Fetch issued shares, calculate market cap
│   │   ├── yahoo_client.py           # Yahoo Finance client (historical shares outstanding)
│   │   ├── indicators.py             # Calculate all technical indicators using pandas-ta
│   │   └── sync_manager.py           # Orchestrate full sync workflow (ties everything together)
│   │
│   ├── ui/                            # NiceGUI interface
│   │   ├── __init__.py
│   │   ├── app.py                     # NiceGUI app creation, theme setup, router
│   │   ├── theme.py                   # Color palette, dark mode, CSS variables
│   │   ├── layout.py                  # Sidebar nav, header, footer, shared layout
│   │   ├── pages/
│   │   │   ├── __init__.py
│   │   │   ├── dashboard.py          # Overview: sync status, market summary, quick stats
│   │   │   ├── stocks.py             # Stock master list with AG Grid
│   │   │   ├── stock_detail.py       # Individual stock: candlestick chart, indicators
│   │   │   ├── indexes.py            # Index list and charts
│   │   │   ├── etfs.py               # ETF list and details
│   │   │   ├── download.py           # Data download controls, progress, logs
│   │   │   ├── corporate_actions.py  # Corporate actions history table
│   │   │   ├── symbol_changes.py     # Symbol change history
│   │   │   └── settings_page.py      # App settings (DB, indicators, schedule)
│   │   └── components/
│   │       ├── __init__.py
│   │       ├── stock_table.py         # Reusable AG Grid component for stock data
│   │       ├── price_chart.py         # ECharts candlestick chart component
│   │       ├── indicator_chart.py     # ECharts indicator subplot component
│   │       ├── sync_progress.py       # Download progress bar + live log
│   │       ├── stat_card.py           # Dashboard stat card (icon + value + label)
│   │       └── search_bar.py          # Global stock search with autocomplete
│   │
│   └── utils/
│       ├── __init__.py
│       ├── date_utils.py              # Trading calendar, holiday list, is_trading_day()
│       ├── parsers.py                 # Parse corporate action description text
│       └── validators.py             # ISIN validator, price sanity checks
│
├── data/                               # Local data directory (gitignored)
│   ├── market.db                      # DuckDB database file
│   ├── downloads/                     # Raw downloaded CSVs (temporary cache)
│   └── logs/                          # Application logs
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                    # Shared fixtures (test DB, mock NSE client)
│   ├── test_nse_client.py
│   ├── test_stock_downloader.py
│   ├── test_corporate_actions.py
│   ├── test_price_adjuster.py
│   ├── test_indicators.py
│   ├── test_market_cap.py
│   └── test_sync_manager.py
│
└── scripts/
    ├── init_db.py                     # Create all tables, run initial migrations
    └── run.py                         # Alternative entry point
```

### File Responsibilities — Quick Reference

| File | What It Does | Depends On |
|------|-------------|------------|
| `main.py` | Creates NiceGUI app, starts server | `ui/app.py`, `db/engine.py` |
| `nse_client.py` | HTTP session management, cookie refresh, rate limiting | `httpx`, `config/constants.py` |
| `stock_downloader.py` | Downloads UDiFF bhavcopy ZIP, parses CSV, stores prices | `nse_client.py`, `models/stock.py` |
| `index_downloader.py` | Downloads index CSV, parses, stores index prices | `nse_client.py`, `models/index.py` |
| `master_data.py` | Downloads EQUITY_L.csv and ETF list, populates master tables | `nse_client.py`, `models/stock.py`, `models/etf.py` |
| `corporate_actions.py` | Fetches splits/bonus from NSE API, parses description text | `nse_client.py`, `models/corporate_action.py` |
| `price_adjuster.py` | Calculates cumulative adjustment factors, generates adjusted prices | `models/adjusted_price.py`, `models/corporate_action.py` |
| `market_cap.py` | Fetches issued shares from NSE quote API, reverse-engineers historical shares using adjustment factors, validates with Yahoo Finance, computes market cap | `nse_client.py`, `yahoo_client.py`, `models/market_cap.py`, `models/corporate_action.py` |
| `yahoo_client.py` | Yahoo Finance client — fetches quarterly shares outstanding via `yfinance`, provides fallback for delisted stocks | `yfinance` |
| `indicators.py` | Calculates SMA, EMA, RSI, MACD, BB, ATR using pandas-ta | `pandas-ta`, `models/indicator.py` |
| `symbol_changes.py` | Downloads symbolchange.csv, renames symbols across all tables | `nse_client.py`, all models |
| `sync_manager.py` | Orchestrates: master data → prices → corp actions → adjust → indicators | All services |

---

## 4. Environment & Setup

### `.env` File

```ini
# Database
DATABASE_URL=duckdb:///data/market.db
# For PostgreSQL (cloud): DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/nse_eod

# NSE Configuration
NSE_START_DATE=2024-01-01
NSE_REQUEST_DELAY_SECONDS=3
NSE_SESSION_REFRESH_MINUTES=5
NSE_MAX_RETRIES=3

# App Configuration
APP_TITLE=NSE Data Manager
APP_HOST=127.0.0.1
APP_PORT=8080
APP_NATIVE=true
APP_DARK_MODE=true

# Logging
LOG_LEVEL=INFO
LOG_FILE=data/logs/app.log

# Scheduling
AUTO_SYNC_ENABLED=false
AUTO_SYNC_TIME=18:30
```

### `config/settings.py` — Pydantic Settings

```python
from pydantic_settings import BaseSettings
from pydantic import Field
from datetime import date, time
from pathlib import Path


class Settings(BaseSettings):
    # Database
    database_url: str = "duckdb:///data/market.db"

    # NSE
    nse_start_date: date = date(2024, 1, 1)
    nse_request_delay_seconds: float = 3.0
    nse_session_refresh_minutes: int = 5
    nse_max_retries: int = 3

    # App
    app_title: str = "NSE Data Manager"
    app_host: str = "127.0.0.1"
    app_port: int = 8080
    app_native: bool = True
    app_dark_mode: bool = True

    # Logging
    log_level: str = "INFO"
    log_file: Path = Path("data/logs/app.log")

    # Scheduling
    auto_sync_enabled: bool = False
    auto_sync_time: time = time(18, 30)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
```

### `config/constants.py` — NSE URLs & Constants

```python
"""All NSE URLs, series types, and static configuration."""

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

# Name changes history
NAME_CHANGE_URL = (
    "https://nsearchives.nseindia.com/content/equities/namechange.csv"
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
    "SttlmDt": "settlement_date",
    "SctySrs": "series",       # EQ, BE, BZ, etc.
    "FinInstrmTp": "instrument_type",  # STK, ETF, etc.
}

# ============================================================
# Index List to Track
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
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

# ============================================================
# Technical Indicators Configuration
# ============================================================

INDICATOR_CONFIG = {
    "sma": [20, 50, 200],
    "ema": [12, 26],
    "rsi": [14],
    "macd": {"fast": 12, "slow": 26, "signal": 9},
    "bollinger": {"length": 20, "std": 2},
    "atr": [14],
    "obv": True,
    "vwap": True,
    "stochastic": {"k": 14, "d": 3, "smooth_k": 3},
}
```

---

## 5. Database Schema

### Complete SQL Schema

> [!NOTE]
> This SQL uses standard types. DuckDB and PostgreSQL both support these. SQLAlchemy ORM will generate the appropriate DDL for each database.

```sql
-- ============================================================
-- MASTER TABLES
-- ============================================================

CREATE TABLE stocks (
    id              INTEGER PRIMARY KEY,          -- Auto-increment
    symbol          VARCHAR(30) NOT NULL UNIQUE,   -- Current trading symbol (e.g., 'RELIANCE')
    company_name    VARCHAR(200),                  -- NULL when auto-discovered from bhavcopy (enriched later from master list)
    series          VARCHAR(5) NOT NULL,           -- 'EQ' or 'BE'
    isin            VARCHAR(12) UNIQUE,            -- From bhavcopy ISIN column; UNIQUE but nullable for edge cases
    face_value      DECIMAL(10, 2),                -- e.g., 10.00, 2.00, 1.00 (enriched from master list)
    listing_date    DATE,                          -- Enriched from master list
    issued_shares   BIGINT,                        -- Total shares outstanding (for market cap)
    industry        VARCHAR(100),                  -- Enriched from quote API
    is_active       BOOLEAN DEFAULT TRUE,          -- TRUE if in current master list
    is_delisted     BOOLEAN DEFAULT FALSE,         -- TRUE if found in bhavcopy but NOT in current master list
    data_source     VARCHAR(30) DEFAULT 'BHAVCOPY_DISCOVERED',  -- 'BHAVCOPY_DISCOVERED' or 'MASTER_LIST'
    first_seen_date DATE,                          -- Earliest trade_date found in bhavcopy
    last_seen_date  DATE,                          -- Latest trade_date found in bhavcopy
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE indexes (
    id              INTEGER PRIMARY KEY,
    index_name      VARCHAR(100) NOT NULL UNIQUE,   -- e.g., 'NIFTY 50'
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE etfs (
    id              INTEGER PRIMARY KEY,
    symbol          VARCHAR(30) NOT NULL UNIQUE,
    etf_name        VARCHAR(200),
    isin            VARCHAR(12) UNIQUE,
    underlying_index VARCHAR(100),                  -- What index this ETF tracks
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================
-- PRICE TABLES (Raw — never modified after insertion)
-- ============================================================

CREATE TABLE stock_prices (
    id              INTEGER PRIMARY KEY,
    stock_id        INTEGER NOT NULL REFERENCES stocks(id),
    trade_date      DATE NOT NULL,
    open            DECIMAL(12, 2) NOT NULL,
    high            DECIMAL(12, 2) NOT NULL,
    low             DECIMAL(12, 2) NOT NULL,
    close           DECIMAL(12, 2) NOT NULL,
    last_price      DECIMAL(12, 2),
    prev_close      DECIMAL(12, 2),
    volume          BIGINT NOT NULL,
    turnover        DECIMAL(18, 2),                -- In lakhs or crores (as NSE reports)
    total_trades    BIGINT,
    UNIQUE(stock_id, trade_date)
);

CREATE TABLE index_prices (
    id              INTEGER PRIMARY KEY,
    index_id        INTEGER NOT NULL REFERENCES indexes(id),
    trade_date      DATE NOT NULL,
    open            DECIMAL(12, 2) NOT NULL,
    high            DECIMAL(12, 2) NOT NULL,
    low             DECIMAL(12, 2) NOT NULL,
    close           DECIMAL(12, 2) NOT NULL,
    volume          BIGINT,
    turnover        DECIMAL(18, 2),
    UNIQUE(index_id, trade_date)
);

CREATE TABLE etf_prices (
    id              INTEGER PRIMARY KEY,
    etf_id          INTEGER NOT NULL REFERENCES etfs(id),
    trade_date      DATE NOT NULL,
    open            DECIMAL(12, 2) NOT NULL,
    high            DECIMAL(12, 2) NOT NULL,
    low             DECIMAL(12, 2) NOT NULL,
    close           DECIMAL(12, 2) NOT NULL,
    volume          BIGINT NOT NULL,
    turnover        DECIMAL(18, 2),
    UNIQUE(etf_id, trade_date)
);


-- ============================================================
-- ADJUSTED PRICES (Recalculated when corporate actions change)
-- ============================================================

CREATE TABLE adjusted_prices (
    id                  INTEGER PRIMARY KEY,
    stock_id            INTEGER NOT NULL REFERENCES stocks(id),
    trade_date          DATE NOT NULL,
    adj_open            DECIMAL(12, 4) NOT NULL,
    adj_high            DECIMAL(12, 4) NOT NULL,
    adj_low             DECIMAL(12, 4) NOT NULL,
    adj_close           DECIMAL(12, 4) NOT NULL,
    adj_volume          BIGINT NOT NULL,
    adjustment_factor   DECIMAL(12, 6) NOT NULL DEFAULT 1.0,  -- Cumulative factor applied
    UNIQUE(stock_id, trade_date)
);


-- ============================================================
-- MARKET CAP (Calculated from RAW prices — NEVER adjusted)
-- ============================================================

CREATE TABLE market_cap (
    id              INTEGER PRIMARY KEY,
    stock_id        INTEGER NOT NULL REFERENCES stocks(id),
    trade_date      DATE NOT NULL,
    close_price     DECIMAL(12, 2) NOT NULL,       -- Raw close price used
    issued_shares   BIGINT NOT NULL,                -- Shares outstanding AS OF this date (historical)
    market_cap      DECIMAL(18, 2) NOT NULL,        -- close_price × issued_shares
    shares_source   VARCHAR(30) DEFAULT 'REVERSE_ENGINEERED',
                                                    -- 'REVERSE_ENGINEERED': from current shares / adjustment factor
                                                    -- 'YAHOO_FINANCE': from yfinance quarterly data
                                                    -- 'NSE_QUOTE': directly from NSE quote API (incremental updates)
    UNIQUE(stock_id, trade_date)
);


-- ============================================================
-- TECHNICAL INDICATORS (Calculated from ADJUSTED prices)
-- ============================================================

CREATE TABLE stock_indicators (
    id              INTEGER PRIMARY KEY,
    stock_id        INTEGER NOT NULL REFERENCES stocks(id),
    trade_date      DATE NOT NULL,

    -- Simple Moving Averages
    sma_20          DECIMAL(12, 4),
    sma_50          DECIMAL(12, 4),
    sma_200         DECIMAL(12, 4),

    -- Exponential Moving Averages
    ema_12          DECIMAL(12, 4),
    ema_26          DECIMAL(12, 4),

    -- RSI
    rsi_14          DECIMAL(8, 4),

    -- MACD
    macd_line       DECIMAL(12, 4),          -- MACD line (EMA12 - EMA26)
    macd_signal     DECIMAL(12, 4),          -- Signal line (EMA9 of MACD)
    macd_histogram  DECIMAL(12, 4),          -- MACD - Signal

    -- Bollinger Bands
    bb_upper        DECIMAL(12, 4),
    bb_middle       DECIMAL(12, 4),          -- SMA(20)
    bb_lower        DECIMAL(12, 4),

    -- ATR
    atr_14          DECIMAL(12, 4),

    -- Volume Indicators
    obv             DECIMAL(18, 2),          -- On-Balance Volume
    vwap            DECIMAL(12, 4),          -- Volume Weighted Average Price

    -- Stochastic
    stoch_k         DECIMAL(8, 4),
    stoch_d         DECIMAL(8, 4),

    UNIQUE(stock_id, trade_date)
);


-- ============================================================
-- CORPORATE ACTIONS
-- ============================================================

CREATE TABLE corporate_actions (
    id                      INTEGER PRIMARY KEY,
    stock_id                INTEGER NOT NULL REFERENCES stocks(id),
    action_type             VARCHAR(20) NOT NULL,       -- 'SPLIT' or 'BONUS'
    ex_date                 DATE NOT NULL,
    record_date             DATE,
    description             TEXT NOT NULL,               -- Original NSE text
    -- For SPLIT: old_face_value → new_face_value
    old_face_value          DECIMAL(10, 2),
    new_face_value          DECIMAL(10, 2),
    -- For BONUS: ratio = bonus_new : bonus_existing (e.g., 1:1 means bonus_new=1, bonus_existing=1)
    bonus_ratio_new         INTEGER,                     -- New shares issued
    bonus_ratio_existing    INTEGER,                     -- Per existing shares
    -- Calculated
    adjustment_factor       DECIMAL(12, 6) NOT NULL,     -- Multiplier for this single action
    is_processed            BOOLEAN DEFAULT FALSE,
    processed_at            TIMESTAMP,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_id, ex_date, action_type)
);


-- ============================================================
-- SYMBOL CHANGES
-- ============================================================

CREATE TABLE symbol_changes (
    id              INTEGER PRIMARY KEY,
    stock_id        INTEGER REFERENCES stocks(id), -- NULL if stock not yet in DB
    old_symbol      VARCHAR(30) NOT NULL,
    new_symbol      VARCHAR(30) NOT NULL,
    effective_date  DATE,
    is_applied      BOOLEAN DEFAULT FALSE,
    applied_at      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(old_symbol, new_symbol)
);


-- ============================================================
-- SYNC LOG (Download tracking)
-- ============================================================

CREATE TABLE sync_log (
    id                  INTEGER PRIMARY KEY,
    sync_type           VARCHAR(30) NOT NULL,   -- 'BHAVCOPY', 'INDEX', 'CORPORATE_ACTIONS',
                                                 -- 'MARKET_CAP', 'INDICATORS', 'SYMBOL_CHANGES',
                                                 -- 'MASTER_DATA', 'FULL_SYNC'
    sync_date           DATE,                    -- The trading date being synced (NULL for non-date syncs)
    status              VARCHAR(20) NOT NULL,    -- 'STARTED', 'SUCCESS', 'FAILED', 'PARTIAL'
    records_processed   INTEGER DEFAULT 0,
    error_message       TEXT,
    started_at          TIMESTAMP NOT NULL,
    completed_at        TIMESTAMP,
    UNIQUE(sync_type, sync_date)
);
```

### SQLAlchemy ORM Model Example (`models/stock.py`)

```python
from sqlalchemy import (
    Column, Integer, String, Date, Numeric, BigInteger,
    Boolean, DateTime, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .base import Base


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(30), nullable=False, unique=True, index=True)
    company_name = Column(String(200), nullable=True)       # NULL when bhavcopy-discovered
    series = Column(String(5), nullable=False)
    isin = Column(String(12), nullable=True, unique=True, index=True)  # From bhavcopy ISIN col
    face_value = Column(Numeric(10, 2))
    listing_date = Column(Date)
    issued_shares = Column(BigInteger)
    industry = Column(String(100))
    is_active = Column(Boolean, default=True)
    is_delisted = Column(Boolean, default=False)            # In bhavcopy but not in master list
    data_source = Column(String(30), default="BHAVCOPY_DISCOVERED")  # or 'MASTER_LIST'
    first_seen_date = Column(Date)                           # Earliest bhavcopy appearance
    last_seen_date = Column(Date)                            # Latest bhavcopy appearance
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    prices = relationship("StockPrice", back_populates="stock", lazy="dynamic")
    adjusted_prices = relationship("AdjustedPrice", back_populates="stock", lazy="dynamic")
    indicators = relationship("StockIndicator", back_populates="stock", lazy="dynamic")
    corporate_actions = relationship("CorporateAction", back_populates="stock", lazy="dynamic")
    market_caps = relationship("MarketCap", back_populates="stock", lazy="dynamic")

    def __repr__(self):
        return f"<Stock(symbol={self.symbol}, name={self.company_name}, delisted={self.is_delisted})>"


class StockPrice(Base):
    __tablename__ = "stock_prices"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", name="uq_stock_price_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    open = Column(Numeric(12, 2), nullable=False)
    high = Column(Numeric(12, 2), nullable=False)
    low = Column(Numeric(12, 2), nullable=False)
    close = Column(Numeric(12, 2), nullable=False)
    last_price = Column(Numeric(12, 2))
    prev_close = Column(Numeric(12, 2))
    volume = Column(BigInteger, nullable=False)
    turnover = Column(Numeric(18, 2))
    total_trades = Column(BigInteger)

    # Relationships
    stock = relationship("Stock", back_populates="prices")
```

### `models/base.py`

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

---

## 6. NSE Data Sources — Complete Reference

### 6.1 Daily Bhavcopy (UDiFF Format) — PRIMARY DATA SOURCE

**URL Pattern:**
```
https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip
```

**Date format in URL:** `YYYYMMDD` (e.g., `20250605` for June 5, 2025)

**Example URL:**
```
https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20250605_F_0000.csv.zip
```

**What's inside:** A ZIP file containing a single CSV file. The CSV has ALL equity trades for that day — all series (EQ, BE, BZ) and all instruments including ETFs.

**CSV columns (UDiFF format — introduced July 2024):**
```
TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,
FnlPric,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,
SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,
SsnId,NewBrdLtQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4
```

**Key columns for our use:**
| Column | Use | Example |
|--------|-----|---------|
| `TradDt` | Trade date | `2025-06-05` |
| `FinInstrmId` | Symbol | `RELIANCE` |
| `ISIN` | ISIN code | `INE002A01018` |
| `SctySrs` | Series | `EQ`, `BE`, `BZ` |
| `OpnPric` | Open price | `1285.50` |
| `HghPric` | High price | `1292.00` |
| `LwPric` | Low price | `1275.10` |
| `ClsPric` | Close price | `1288.35` |
| `LastPric` | Last traded price | `1288.00` |
| `PrvsClsgPric` | Previous close | `1280.00` |
| `TtlTradgVol` | Volume | `12345678` |
| `TtlTrfVal` | Turnover (₹) | `159123456.78` |
| `TtlNbOfTxsExctd` | Total trades | `98765` |

**How to filter:**
- **Stocks (EQ series):** `SctySrs == "EQ"`
- **Stocks (BE series):** `SctySrs == "BE"` (trade-to-trade, no intraday)
- **ETFs:** `SctySrs == "EQ"` AND symbol is in ETF master list
- Ignore: `BZ`, `SM`, `ST`, `SG` and other series

**Protection level:** LOW — just needs a proper `User-Agent` header.

**Error cases:**
- Returns HTTP 404 if the date is a holiday/weekend
- Returns HTTP 403 if no `User-Agent` or IP is blocked

---

### 6.2 Index Daily Close CSV

**URL Pattern:**
```
https://nsearchives.nseindia.com/content/indices/ind_close_all_{DDMMYYYY}.csv
```

**Date format in URL:** `DDMMYYYY` (e.g., `05062025` for June 5, 2025)

**Example URL:**
```
https://nsearchives.nseindia.com/content/indices/ind_close_all_05062025.csv
```

**CSV columns:**
```
Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,
Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),
P/E,P/B,Div Yield
```

**Key columns:**
| Column | Use | Example |
|--------|-----|---------|
| `Index Name` | Name | `Nifty 50` |
| `Index Date` | Date | `05-06-2025` |
| `Open Index Value` | Open | `24500.00` |
| `High Index Value` | High | `24650.00` |
| `Low Index Value` | Low | `24400.00` |
| `Closing Index Value` | Close | `24580.50` |
| `Volume` | Volume | `123456789` |
| `Turnover (Rs. Cr.)` | Turnover | `12345.67` |

**Protection level:** LOW

---

### 6.3 Stock Master List (EQUITY_L.csv)

**URL:**
```
https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv
```

**CSV columns:**
```
SYMBOL, NAME OF COMPANY,  SERIES,  DATE OF LISTING,  PAID UP VALUE,  MARKET LOT,  ISIN NUMBER,  FACE VALUE
```

> [!WARNING]
> The column names have leading/trailing spaces. Use `.strip()` on column names after reading.

**Example row:**
```
RELIANCE, RELIANCE INDUSTRIES LTD,  EQ,  29-NOV-1995,  10,  1,  INE002A01018,  10
```

**Protection level:** LOW

---

### 6.4 ETF Master List

**URL:**
```
https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv
```

**Protection level:** LOW

---

### 6.5 Corporate Actions API

**URL:**
```
https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date={DD-MM-YYYY}&to_date={DD-MM-YYYY}
```

**Query Parameters:**
| Param | Format | Example | Required |
|-------|--------|---------|----------|
| `index` | String | `equities` | Yes |
| `from_date` | DD-MM-YYYY | `01-01-2024` | Optional |
| `to_date` | DD-MM-YYYY | `31-12-2024` | Optional |
| `symbol` | String | `RELIANCE` | Optional |

**Response (JSON array):**
```json
[
  {
    "symbol": "RELIANCE",
    "company": "Reliance Industries Limited",
    "industry": "REFINERIES",
    "purpose": "Bonus",
    "subject": "Bonus Issue 1:1",
    "exDate": "29-Oct-2024",
    "recDate": "29-Oct-2024",
    "bcStartDate": "25-Oct-2024",
    "bcEndDate": "28-Oct-2024",
    "ndStartDate": "-",
    "ndEndDate": "-"
  },
  {
    "symbol": "TCS",
    "company": "Tata Consultancy Services Limited",
    "industry": "COMPUTERS - SOFTWARE",
    "purpose": "Face Value Split",
    "subject": "Sub-Division/Stock Split From Rs.10/- Per Share To Re.1/- Per Share",
    "exDate": "10-Jun-2024",
    "recDate": "10-Jun-2024",
    ...
  }
]
```

**Protection level:** HIGH — requires session cookies.

**How to parse `subject` field for splits:**
```python
import re

def parse_split(subject: str) -> tuple[float, float] | None:
    """Parse split subject text. Returns (old_fv, new_fv) or None."""
    # Pattern: "From Rs.10/- Per Share To Re.1/- Per Share"
    # or: "From Rs. 10/- to Rs. 2/-"
    # or: "Face Value Split from Rs.10 to Rs.2"
    pattern = r'[Ff]rom\s+(?:Rs\.?\s*)?(\d+(?:\.\d+)?)\s*/?-?\s*(?:Per\s+Share\s+)?[Tt]o\s+(?:Rs?e?\.?\s*)?(\d+(?:\.\d+)?)'
    match = re.search(pattern, subject)
    if match:
        old_fv = float(match.group(1))
        new_fv = float(match.group(2))
        return old_fv, new_fv
    return None


def parse_bonus(subject: str) -> tuple[int, int] | None:
    """Parse bonus subject text. Returns (new_shares, per_existing_shares) or None."""
    # Pattern: "Bonus Issue 1:1" or "Bonus 2:1" or "Bonus issue of 1:2"
    pattern = r'[Bb]onus\s+(?:[Ii]ssue\s+)?(?:of\s+)?(\d+)\s*:\s*(\d+)'
    match = re.search(pattern, subject)
    if match:
        new_shares = int(match.group(1))
        per_existing = int(match.group(2))
        return new_shares, per_existing
    return None
```

---

### 6.6 Stock Quote API (for Shares Outstanding)

**URL:**
```
https://www.nseindia.com/api/quote-equity?symbol={SYMBOL}
```

**Response (JSON, key sections):**
```json
{
  "info": {
    "symbol": "RELIANCE",
    "companyName": "Reliance Industries Limited",
    "industry": "REFINERIES",
    "isin": "INE002A01018"
  },
  "securityInfo": {
    "issuedSize": 6765813926,     // ← TOTAL SHARES OUTSTANDING
    "issuedCap": 67658139260,     // issued capital = face_value × issued_size
    "faceValue": 10.0
  },
  "priceInfo": {
    "close": 1288.35,
    "open": 1285.50,
    "previousClose": 1280.00
  }
}
```

**The critical field:** `securityInfo.issuedSize` = total shares outstanding.

**Protection level:** HIGH — requires session cookies.

**Rate limit concern:** With ~2000+ active stocks, fetching each stock's quote at 3s delay = ~100 minutes. Strategy: fetch once during initial load, then update only when corporate actions occur.

---

### 6.7 Symbol Changes CSV

**URL:**
```
https://nsearchives.nseindia.com/content/equities/symbolchange.csv
```

**CSV columns:**
```
Company Name, Old Symbol, New Symbol, Date of Change
```

**Example:**
```
XYZ Industries Ltd, XYZOLD, XYZNEW, 15-Mar-2024
```

**Protection level:** LOW

---

## 7. NSE HTTP Client Specification

### `services/nse_client.py` — Complete Implementation Pattern

```python
"""
NSE HTTP Client with session management, cookie refresh, and rate limiting.

NSE uses two tiers of protection:
1. nsearchives.nseindia.com — Low protection (User-Agent header only)
2. www.nseindia.com/api/* — High protection (session cookies required)

This client handles both seamlessly.
"""

import asyncio
import time
import io
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd
from loguru import logger

from config.constants import NSE_HEADERS, NSE_BASE_URL
from config.settings import settings


class NSEClient:
    """HTTP client for NSE India with automatic session management."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._last_cookie_refresh: Optional[datetime] = None
        self._last_request_time: float = 0

    async def _ensure_client(self):
        """Create HTTP client if not exists."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=NSE_HEADERS,
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
                verify=True,
            )

    async def _refresh_cookies(self, force: bool = False):
        """
        Visit NSE homepage to establish session cookies.
        Required before making API calls to www.nseindia.com/api/*.
        Cookies expire every ~5-10 minutes.
        """
        await self._ensure_client()

        now = datetime.now()
        needs_refresh = (
            force
            or self._last_cookie_refresh is None
            or (now - self._last_cookie_refresh) > timedelta(minutes=settings.nse_session_refresh_minutes)
        )

        if needs_refresh:
            logger.debug("Refreshing NSE session cookies...")
            try:
                response = await self._client.get(NSE_BASE_URL)
                response.raise_for_status()
                self._last_cookie_refresh = now
                logger.debug("NSE cookies refreshed successfully")
            except httpx.HTTPError as e:
                logger.error(f"Failed to refresh NSE cookies: {e}")
                raise

    async def _rate_limit(self):
        """Enforce minimum delay between requests."""
        elapsed = time.monotonic() - self._last_request_time
        delay = settings.nse_request_delay_seconds
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request_time = time.monotonic()

    async def _get(self, url: str, params: dict = None,
                   requires_cookies: bool = False,
                   retries: int = None) -> httpx.Response:
        """
        Make a GET request with rate limiting and retries.

        Args:
            url: Full URL to fetch
            params: Query parameters
            requires_cookies: If True, refresh session cookies first
            retries: Number of retries (default: settings.nse_max_retries)
        """
        if retries is None:
            retries = settings.nse_max_retries

        if requires_cookies:
            await self._refresh_cookies()

        await self._rate_limit()
        await self._ensure_client()

        for attempt in range(retries + 1):
            try:
                response = await self._client.get(url, params=params)

                if response.status_code == 401 or response.status_code == 403:
                    logger.warning(f"Got {response.status_code}, refreshing cookies (attempt {attempt + 1})")
                    await self._refresh_cookies(force=True)
                    await self._rate_limit()
                    continue

                response.raise_for_status()
                return response

            except httpx.HTTPError as e:
                if attempt < retries:
                    wait = (attempt + 1) * settings.nse_request_delay_seconds
                    logger.warning(f"Request failed ({e}), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Request failed after {retries + 1} attempts: {url}")
                    raise

    # ========== HIGH-LEVEL METHODS ==========

    async def download_bhavcopy_csv(self, trade_date: str) -> pd.DataFrame:
        """
        Download and parse UDiFF bhavcopy for a given date.

        Args:
            trade_date: Date string in YYYYMMDD format (e.g., '20250605')

        Returns:
            DataFrame with all equity trades for that day

        Raises:
            httpx.HTTPStatusError: If date is a holiday (404) or access denied (403)
        """
        from config.constants import BHAVCOPY_URL
        url = BHAVCOPY_URL.format(date=trade_date)
        response = await self._get(url, requires_cookies=False)

        # Unzip in memory
        zip_buffer = io.BytesIO(response.content)
        with zipfile.ZipFile(zip_buffer) as zf:
            csv_filename = zf.namelist()[0]  # There's only one CSV inside
            with zf.open(csv_filename) as csv_file:
                df = pd.read_csv(csv_file)

        return df

    async def download_index_csv(self, trade_date: str) -> pd.DataFrame:
        """
        Download and parse index daily close CSV.

        Args:
            trade_date: Date string in DDMMYYYY format (e.g., '05062025')
        """
        from config.constants import INDEX_CLOSE_URL
        url = INDEX_CLOSE_URL.format(date=trade_date)
        response = await self._get(url, requires_cookies=False)

        df = pd.read_csv(io.StringIO(response.text))
        return df

    async def download_equity_list(self) -> pd.DataFrame:
        """Download EQUITY_L.csv (stock master list)."""
        from config.constants import EQUITY_LIST_URL
        response = await self._get(EQUITY_LIST_URL, requires_cookies=False)
        df = pd.read_csv(io.StringIO(response.text))
        # Strip whitespace from column names (NSE has leading spaces)
        df.columns = df.columns.str.strip()
        return df

    async def download_etf_list(self) -> pd.DataFrame:
        """Download eq_etfseclist.csv (ETF master list)."""
        from config.constants import ETF_LIST_URL
        response = await self._get(ETF_LIST_URL, requires_cookies=False)
        df = pd.read_csv(io.StringIO(response.text))
        df.columns = df.columns.str.strip()
        return df

    async def download_symbol_changes(self) -> pd.DataFrame:
        """Download symbolchange.csv."""
        from config.constants import SYMBOL_CHANGE_URL
        response = await self._get(SYMBOL_CHANGE_URL, requires_cookies=False)
        df = pd.read_csv(io.StringIO(response.text))
        df.columns = df.columns.str.strip()
        return df

    async def fetch_corporate_actions(
        self, from_date: str, to_date: str, symbol: str = None
    ) -> list[dict]:
        """
        Fetch corporate actions from NSE API.

        Args:
            from_date: DD-MM-YYYY format
            to_date: DD-MM-YYYY format
            symbol: Optional stock symbol to filter

        Returns:
            List of corporate action dictionaries
        """
        from config.constants import CORPORATE_ACTIONS_URL
        params = {
            "index": "equities",
            "from_date": from_date,
            "to_date": to_date,
        }
        if symbol:
            params["symbol"] = symbol

        response = await self._get(
            CORPORATE_ACTIONS_URL, params=params, requires_cookies=True
        )
        return response.json()

    async def fetch_stock_quote(self, symbol: str) -> dict:
        """
        Fetch stock quote (contains issuedSize for market cap).

        Args:
            symbol: Stock symbol (e.g., 'RELIANCE')

        Returns:
            Full quote dictionary
        """
        from config.constants import STOCK_QUOTE_URL
        response = await self._get(
            STOCK_QUOTE_URL, params={"symbol": symbol}, requires_cookies=True
        )
        return response.json()

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
```

---

## 8. Data Ingestion Pipeline

> [!IMPORTANT]
> **TWO MODES OF OPERATION**: The stock downloader operates differently depending on whether we are doing a historical load or an incremental update. This is the most important architectural decision in the data pipeline.

### 8.1 Stock Downloader (`services/stock_downloader.py`)

**Responsibility:** Download bhavcopy for a date, parse it, separate stocks from ETFs, and store in DB. Has two modes: `HISTORICAL` and `INCREMENTAL`.

#### Mode 1: HISTORICAL (Initial Load — Bhavcopy-First Discovery)

**Used when:** Downloading historical data from START_DATE to today for the first time.

**Key principle:** Every security found in the bhavcopy is stored — including delisted stocks, old symbols, and securities not in the current EQUITY_L.csv master list. The bhavcopy IS the source of truth for what traded on that date.

**Algorithm:**
```
1. Format trade_date as YYYYMMDD
2. Call nse_client.download_bhavcopy_csv(date)
3. If 404 → skip (holiday), log and return
4. Filter DataFrame: SctySrs in ['EQ', 'BE']
   (take ALL stocks — do NOT cross-reference against master list)
5. For each stock row:
   a. Look up stock in DB by ISIN first, then by symbol as fallback:
      - Query: SELECT * FROM stocks WHERE isin = row.ISIN
      - Fallback: SELECT * FROM stocks WHERE symbol = row.symbol AND isin IS NULL
   b. If stock NOT found in DB → AUTO-CREATE a new stock record:
      INSERT INTO stocks (
          symbol      = row.FinInstrmId,
          series      = row.SctySrs,
          isin        = row.ISIN,
          company_name = NULL,              -- Unknown, will be enriched later
          face_value   = NULL,              -- Unknown, will be enriched later
          data_source  = 'BHAVCOPY_DISCOVERED',
          is_active    = TRUE,
          is_delisted  = FALSE,             -- Will be determined later during enrichment
          first_seen_date = trade_date,
          last_seen_date  = trade_date
      )
   c. If stock found → update last_seen_date if trade_date is newer
   d. Get stock_id (from existing or newly created record)
   e. Insert into stock_prices (skip if already exists — UNIQUE constraint)
6. Handle ETFs separately (see 8.4 below)
7. Log sync result to sync_log
```

**Why ISIN-first lookup?** A stock may have changed symbols between when this bhavcopy was generated and today. ISIN never changes, so it's the stable identifier. Example:
- Bhavcopy from 2024-01-15 has symbol `XYZOLD` with ISIN `INE123A01010`
- Bhavcopy from 2024-06-20 has symbol `XYZNEW` with ISIN `INE123A01010`
- Both should map to the SAME stock record (matched by ISIN)

#### Mode 2: INCREMENTAL (Daily Updates — Master List Aware)

**Used when:** Downloading daily data after initial historical load is complete.

**Key principle:** Only process stocks that exist in our DB (either from master list or from historical bhavcopy discovery). Log warnings for unknown symbols but don't auto-create.

**Algorithm:**
```
1. Format trade_date as YYYYMMDD
2. Call nse_client.download_bhavcopy_csv(date)
3. If 404 → skip (holiday), log and return
4. Filter DataFrame: SctySrs in ['EQ', 'BE']
5. For each stock row:
   a. Look up stock_id from stocks table (by ISIN first, then symbol)
   b. If found:
      - Insert into stock_prices
      - Update last_seen_date
   c. If NOT found:
      - This is a newly listed stock not yet in our DB
      - AUTO-CREATE with data_source = 'BHAVCOPY_DISCOVERED'
      - Log info: "New stock discovered: {symbol} ({isin})"
      - Insert price data
6. Handle ETFs separately
7. Log sync result to sync_log
```

> [!NOTE]
> Even in incremental mode, we auto-create newly discovered stocks. The difference from historical mode is mainly conceptual — in incremental mode, these are genuinely new listings. In historical mode, they might be old/delisted stocks.

#### Bulk Insert Pattern

```python
async def upsert_stock_prices(session, records: list[dict]):
    """Bulk upsert stock prices (skip duplicates)."""
    if not records:
        return 0

    # Pre-filter: check which (stock_id, trade_date) pairs already exist
    existing_keys = set()
    stock_ids = list({r["stock_id"] for r in records})
    trade_dates = list({r["trade_date"] for r in records})

    existing = session.execute(
        select(StockPrice.stock_id, StockPrice.trade_date)
        .where(StockPrice.stock_id.in_(stock_ids))
        .where(StockPrice.trade_date.in_(trade_dates))
    ).all()
    existing_keys = {(row.stock_id, row.trade_date) for row in existing}

    new_records = [
        r for r in records
        if (r["stock_id"], r["trade_date"]) not in existing_keys
    ]

    if new_records:
        session.bulk_insert_mappings(StockPrice, new_records)
        session.commit()

    return len(new_records)
```

#### Stock Auto-Discovery Helper

```python
async def find_or_create_stock(
    session, symbol: str, series: str, isin: str, trade_date: date
) -> int:
    """
    Find existing stock by ISIN (preferred) or symbol.
    If not found, auto-create a minimal stock record.
    Returns the stock_id.
    """
    # Step 1: Try ISIN lookup (most reliable — ISIN never changes)
    if isin:
        stock = session.execute(
            select(Stock).where(Stock.isin == isin)
        ).scalar_one_or_none()
        if stock:
            # Update last_seen_date and symbol if changed
            if stock.last_seen_date is None or trade_date > stock.last_seen_date:
                stock.last_seen_date = trade_date
            if stock.symbol != symbol:
                # Symbol has changed! Log this and update.
                logger.info(f"Symbol change detected via bhavcopy: {stock.symbol} → {symbol} (ISIN: {isin})")
                stock.symbol = symbol
            return stock.id

    # Step 2: Try symbol lookup (fallback for rows without ISIN)
    stock = session.execute(
        select(Stock).where(Stock.symbol == symbol)
    ).scalar_one_or_none()
    if stock:
        if stock.last_seen_date is None or trade_date > stock.last_seen_date:
            stock.last_seen_date = trade_date
        if isin and stock.isin is None:
            stock.isin = isin  # Backfill ISIN if we didn't have it
        return stock.id

    # Step 3: Auto-create new stock record
    new_stock = Stock(
        symbol=symbol,
        series=series,
        isin=isin if isin else None,
        company_name=None,                  # Will be enriched from master list later
        data_source="BHAVCOPY_DISCOVERED",
        is_active=True,
        is_delisted=False,
        first_seen_date=trade_date,
        last_seen_date=trade_date,
    )
    session.add(new_stock)
    session.flush()  # Get the auto-generated ID
    logger.info(f"Auto-discovered stock: {symbol} (ISIN: {isin}, series: {series})")
    return new_stock.id
```

---

### 8.2 Index Downloader (`services/index_downloader.py`)

**Algorithm:**
```
1. Format trade_date as DDMMYYYY
2. Call nse_client.download_index_csv(date)
3. Filter rows where "Index Name" is in TRACKED_INDEXES
4. For each row:
   a. Look up index_id from indexes table (auto-create if missing)
   b. Parse: Open, High, Low, Close, Volume, Turnover
   c. Insert into index_prices
5. Log to sync_log
```

---

### 8.3 Master Data Enrichment (`services/master_data.py`)

> [!IMPORTANT]
> **For historical loads, master data is used to ENRICH stocks that were already discovered from bhavcopies — not to seed the stocks table.** For incremental updates, it also identifies newly listed stocks.

**Algorithm — Enrich Mode (after historical bhavcopy load):**
```
1. Download EQUITY_L.csv → current master list
2. Build a lookup dict: { ISIN → master_row, symbol → master_row }
3. For each stock in our DB (all bhavcopy-discovered stocks):
   a. Match by ISIN first, then by symbol
   b. If matched in master list:
      - UPDATE stocks SET
          company_name = master.NAME_OF_COMPANY,
          face_value   = master.FACE_VALUE,
          listing_date = master.DATE_OF_LISTING,
          data_source  = 'MASTER_LIST',        -- Upgrade source
          is_active    = TRUE,
          is_delisted  = FALSE
   c. If NOT matched in master list:
      - This stock was in historical bhavcopies but is NOT currently listed
      - UPDATE stocks SET
          is_delisted = TRUE,
          is_active   = FALSE
      - Log: "Stock {symbol} (ISIN: {isin}) not in current master list — marked as delisted"
4. Check for stocks in master list that are NOT in our DB:
   - These are recently listed stocks that didn't appear in our historical date range
   - INSERT into stocks with data_source = 'MASTER_LIST'
5. Download eq_etfseclist.csv
6. For each ETF row:
   a. Check if exists (by symbol or ISIN)
   b. INSERT or UPDATE etfs table
7. Ensure all TRACKED_INDEXES exist in indexes table
```

**Algorithm — Incremental Refresh Mode (daily):**
```
1. Download EQUITY_L.csv
2. For each stock in master list:
   a. If exists in DB → UPDATE metadata (face_value, company_name, etc.)
   b. If NOT in DB → INSERT (new listing)
3. For stocks in DB where is_active=TRUE but NOT in latest master list:
   - If last_seen_date is > 30 days ago → mark is_delisted=TRUE, is_active=FALSE
   - (Grace period handles temporary suspensions)
```

### 8.4 ETF Downloader (`services/etf_downloader.py`)

**How to separate ETFs from stocks in bhavcopy:**
```
ETFs trade in the same bhavcopy as stocks, with series = 'EQ'.
To distinguish them:
1. Maintain a set of known ETF symbols (from eq_etfseclist.csv)
2. During historical load:
   - If symbol is in ETF set → treat as ETF, store in etf_prices
   - If symbol is NOT in ETF set AND NOT in stock set → it's likely an ETF
     that was delisted; store as stock (safer default)
3. During incremental load:
   - Cross-reference against current ETF list

Practical approach: Download ETF list FIRST (before bhavcopy processing),
use it as a filter. ETF list is small and rarely changes dramatically.
```

---

## 9. Corporate Actions Logic

### 9.1 Fetching Corporate Actions (`services/corporate_actions.py`)

**Algorithm:**
```
1. Call nse_client.fetch_corporate_actions(from_date, to_date)
2. For each action in response:
   a. Check action.purpose and action.subject:
      - If contains "split" or "sub-division" or "face value" → action_type = 'SPLIT'
      - If contains "bonus" → action_type = 'BONUS'
      - Otherwise → SKIP (we only care about splits and bonus)
   b. Parse the subject text:
      - For SPLIT: extract old_face_value and new_face_value
      - For BONUS: extract bonus ratio (new:existing)
   c. Calculate adjustment_factor for this single action:
      - SPLIT: factor = old_face_value / new_face_value
        Example: 10→2 split → factor = 10/2 = 5
      - BONUS: factor = (existing + new) / existing
        Example: 1:1 bonus → factor = (1+1)/1 = 2
   d. Look up stock_id by symbol
   e. Parse ex_date from action.exDate
   f. INSERT into corporate_actions (skip if already exists)
```

### 9.2 Subject Text Parsing — All Known Patterns

NSE corporate action `subject` field comes in many formats. Here are ALL known patterns:

**Split patterns:**
```
"Sub-Division/Stock Split From Rs.10/- Per Share To Re.1/- Per Share"
"Face Value Split from Rs. 10 to Rs. 2"
"Sub-Division from Rs. 10/- to Rs. 5/-"
"Stock Split from Rs.10 per share to Rs.2 per share"
"Sub-Division/Split Of Face Value From Rs. 10/- Per Share To Rs. 2/- Per Share"
"FV Split Rs. 10 to Rs. 5"
```

**Bonus patterns:**
```
"Bonus Issue 1:1"
"Bonus 2:1"
"Bonus issue of 1:2"
"Bonus Issue In Ratio Of 1:1"
"Bonus issue in the ratio of 3:1"
```

**Comprehensive regex patterns:**
```python
# SPLIT — match any "from X to Y" pattern with Rs/Re prefix
SPLIT_PATTERN = re.compile(
    r'(?:from|of)\s+(?:Rs?e?\.?\s*)?(\d+(?:\.\d+)?)\s*/?-?\s*'
    r'(?:per\s+share\s+)?'
    r'(?:to|into)\s+(?:Rs?e?\.?\s*)?(\d+(?:\.\d+)?)',
    re.IGNORECASE
)

# BONUS — match "N:M" ratio
BONUS_PATTERN = re.compile(
    r'bonus\s+(?:issue\s+)?(?:(?:in\s+)?(?:the\s+)?ratio\s+of\s+)?'
    r'(\d+)\s*:\s*(\d+)',
    re.IGNORECASE
)
```

---

## 10. Price Adjustment Engine

### 10.1 Core Concept

**Goal:** Create a separate `adjusted_prices` table that reflects what prices "would have been" if all future corporate actions had already happened. This makes historical analysis and indicator calculation accurate.

**Key rule:** We adjust prices **BEFORE** the ex-date. Prices on and after the ex-date remain unchanged.

### 10.2 Adjustment Factor Calculation

For a single corporate action:

```
SPLIT (old_fv → new_fv):
  single_factor = old_fv / new_fv
  Example: Rs.10 → Rs.2 means 5:1 split → factor = 5.0
  Pre-ex-date prices are DIVIDED by 5
  Pre-ex-date volumes are MULTIPLIED by 5

BONUS (new : existing):
  single_factor = (existing + new) / existing
  Example: 1:1 bonus → factor = (1+1)/1 = 2.0
  Pre-ex-date prices are DIVIDED by 2
  Pre-ex-date volumes are MULTIPLIED by 2
```

### 10.3 Cumulative Adjustment (Multiple Actions)

When a stock has multiple corporate actions, factors compound. We process them in **chronological order** (earliest first):

```
Example: Stock XYZ
  - 2024-03-15: Bonus 1:1 (factor = 2.0)
  - 2024-09-10: Split 10→2 (factor = 5.0)

For dates BEFORE 2024-03-15:
  cumulative_factor = 2.0 × 5.0 = 10.0
  adj_close = raw_close / 10.0

For dates between 2024-03-15 and 2024-09-09:
  cumulative_factor = 5.0  (only the split factor, since bonus already happened)
  adj_close = raw_close / 5.0

For dates on/after 2024-09-10:
  cumulative_factor = 1.0  (no adjustments needed)
  adj_close = raw_close (unchanged)
```

### 10.4 Complete Algorithm (`services/price_adjuster.py`)

```python
"""
Price Adjustment Engine

Generates adjusted prices for all stocks based on corporate actions.
Adjusts prices BEFORE each ex-date. Prices on/after ex-date are unchanged.
"""

async def calculate_adjusted_prices(session, stock_id: int):
    """
    Calculate and store adjusted prices for a stock.

    Algorithm:
    1. Get all corporate actions for this stock, ordered by ex_date ASC
    2. Get all raw prices for this stock, ordered by trade_date ASC
    3. Calculate cumulative adjustment factor for each date
    4. Apply factor to generate adjusted prices
    5. Upsert into adjusted_prices table
    """

    # Step 1: Get corporate actions sorted by ex_date (earliest first)
    actions = session.execute(
        select(CorporateAction)
        .where(CorporateAction.stock_id == stock_id)
        .where(CorporateAction.action_type.in_(["SPLIT", "BONUS"]))
        .order_by(CorporateAction.ex_date.asc())
    ).scalars().all()

    # Step 2: Get all raw prices
    prices = session.execute(
        select(StockPrice)
        .where(StockPrice.stock_id == stock_id)
        .order_by(StockPrice.trade_date.asc())
    ).scalars().all()

    if not prices:
        return

    # Step 3: Build list of (ex_date, factor) tuples
    action_factors = []
    for action in actions:
        action_factors.append((action.ex_date, action.adjustment_factor))

    # Step 4: For each price, calculate cumulative factor
    adjusted_records = []
    for price in prices:
        # Cumulative factor = product of all action factors where ex_date > price.trade_date
        # (i.e., actions that happen AFTER this date — these affect historical prices)
        cumulative_factor = 1.0
        for ex_date, factor in action_factors:
            if price.trade_date < ex_date:
                cumulative_factor *= factor

        adjusted_records.append({
            "stock_id": stock_id,
            "trade_date": price.trade_date,
            "adj_open": round(float(price.open) / cumulative_factor, 4),
            "adj_high": round(float(price.high) / cumulative_factor, 4),
            "adj_low": round(float(price.low) / cumulative_factor, 4),
            "adj_close": round(float(price.close) / cumulative_factor, 4),
            "adj_volume": round(float(price.volume) * cumulative_factor),
            "adjustment_factor": round(cumulative_factor, 6),
        })

    # Step 5: Delete existing adjusted prices and re-insert
    # (simpler than upsert when recalculating everything)
    session.execute(
        delete(AdjustedPrice).where(AdjustedPrice.stock_id == stock_id)
    )
    session.bulk_insert_mappings(AdjustedPrice, adjusted_records)
    await session.commit()
```

### 10.5 When to Recalculate

Adjusted prices must be recalculated when:
1. **New corporate action is discovered** → recalculate affected stock from the beginning
2. **New raw prices are added** → calculate adjusted prices for just the new dates
3. **Corporate action is corrected/deleted** → full recalculation for that stock

---

## 11. Market Cap Calculation — Historical Accuracy

> [!IMPORTANT]
> **Historical market cap requires knowing the shares outstanding on EACH historical date**, not just today's count. Share counts change due to splits, bonus, rights issues, buybacks, QIPs, etc. We use a hybrid approach:
> - **Method A** (Primary): Reverse-engineer historical shares from current `issuedSize` + corporate actions
> - **Method B** (Supplementary): Yahoo Finance quarterly shares data for validation and gap-filling
> - **Method C** (Daily updates): Direct from NSE quote API

### 11.1 Core Principle

```
Historical Market Cap = Historical Shares Outstanding × Raw Close Price

Where:
  - Historical Shares Outstanding varies per date (NOT a constant)
  - Raw Close Price = close from stock_prices (NEVER adjusted_prices)
  - Market Cap is NEVER retroactively modified after initial calculation
```

### 11.2 Method A: Reverse-Engineer from Corporate Actions (PRIMARY)

**Key insight:** The same `cumulative_adjustment_factor` we calculate for price adjustment can be used to derive historical share counts. If a split/bonus multiplied the share count by factor X, then before that event, the shares were `current_shares / X`.

**Formula:**
```
historical_shares[date] = current_issued_shares / cumulative_factor[date]
```

**Worked Example — Reliance Industries:**
```
Current issued shares (Jan 2025): 13,531,627,852
Corporate action: 1:1 Bonus on 28-Oct-2024 (adjustment_factor = 2)

For 01-Jan-2024 (BEFORE bonus):
  cumulative_factor = 2.0 (bonus ex_date 28-Oct-2024 > 01-Jan-2024)
  historical_shares = 13,531,627,852 / 2 = 6,765,813,926 ✓
  Close price on 01-Jan-2024: ₹2,493.60
  Market Cap = 6,765,813,926 × 2,493.60 = ₹16,86,335 Cr ✓

For 01-Nov-2024 (AFTER bonus):
  cumulative_factor = 1.0 (no future actions)
  historical_shares = 13,531,627,852 / 1 = 13,531,627,852
  Close price on 01-Nov-2024: ₹1,267.55
  Market Cap = 13,531,627,852 × 1,267.55 = ₹17,15,376 Cr ✓
  (Market cap roughly same — bonus didn't change company value)
```

**Accuracy: ~90-95%** — Covers splits and bonus perfectly. Misses rights issues, buybacks, QIPs (typically 1-10% impact).

**Code:**
```python
async def calculate_historical_market_cap_method_a(
    session, stock_id: int, current_issued_shares: int
):
    """
    Calculate historical market cap by reverse-engineering shares outstanding
    using corporate action adjustment factors.
    """
    # Get corporate actions for this stock
    actions = session.execute(
        select(CorporateAction)
        .where(CorporateAction.stock_id == stock_id)
        .where(CorporateAction.action_type.in_(["SPLIT", "BONUS"]))
        .order_by(CorporateAction.ex_date.asc())
    ).scalars().all()

    action_factors = [(a.ex_date, a.adjustment_factor) for a in actions]

    # Get all raw prices
    prices = session.execute(
        select(StockPrice)
        .where(StockPrice.stock_id == stock_id)
        .order_by(StockPrice.trade_date.asc())
    ).scalars().all()

    records = []
    for price in prices:
        # Same cumulative factor logic as price adjustment
        cumulative_factor = 1.0
        for ex_date, factor in action_factors:
            if price.trade_date < ex_date:
                cumulative_factor *= factor

        historical_shares = int(current_issued_shares / cumulative_factor)
        market_cap_value = historical_shares * float(price.close)

        records.append({
            "stock_id": stock_id,
            "trade_date": price.trade_date,
            "close_price": float(price.close),
            "issued_shares": historical_shares,
            "market_cap": round(market_cap_value, 2),
            "shares_source": "REVERSE_ENGINEERED",
        })

    # Bulk insert
    session.execute(delete(MarketCap).where(MarketCap.stock_id == stock_id))
    session.bulk_insert_mappings(MarketCap, records)
    session.commit()
```

### 11.3 Method B: Yahoo Finance Quarterly Data (SUPPLEMENTARY)

**Purpose:** Validate Method A results and catch share count changes from events we don't track (rights issues, buybacks, QIPs).

**Yahoo Finance provides quarterly shares outstanding** via `ticker.get_shares_full()`. This data reflects ALL share count changes, not just splits/bonus.

**Add to services: `services/yahoo_client.py`**
```python
"""
Yahoo Finance client for supplementary historical data.
Used primarily for historical shares outstanding (quarterly resolution).
"""

import yfinance as yf
import pandas as pd
from loguru import logger
from datetime import date


def get_historical_shares_outstanding(
    symbol: str, start_date: date, end_date: date
) -> pd.Series | None:
    """
    Fetch historical shares outstanding from Yahoo Finance.

    Args:
        symbol: NSE symbol (e.g., 'RELIANCE'). Will append '.NS' for Yahoo.
        start_date: Start date for historical data
        end_date: End date for historical data

    Returns:
        pandas Series with dates as index and shares outstanding as values.
        Quarterly resolution, forward-filled to daily.
        Returns None if data unavailable.
    """
    yahoo_symbol = f"{symbol}.NS"

    try:
        ticker = yf.Ticker(yahoo_symbol)
        shares = ticker.get_shares_full(
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d")
        )

        if shares is None or shares.empty:
            logger.debug(f"No Yahoo shares data for {symbol}")
            return None

        # Forward-fill quarterly data to get daily values
        # Create a daily date range and reindex
        daily_range = pd.date_range(start=start_date, end=end_date, freq='B')  # Business days
        shares_daily = shares.reindex(daily_range, method='ffill')
        shares_daily = shares_daily.dropna()

        logger.debug(f"Yahoo shares data for {symbol}: {len(shares)} quarterly → {len(shares_daily)} daily")
        return shares_daily

    except Exception as e:
        logger.warning(f"Failed to fetch Yahoo shares for {symbol}: {e}")
        return None


def get_current_shares_outstanding(symbol: str) -> int | None:
    """
    Get current shares outstanding from Yahoo Finance.
    Fallback when NSE quote API is unavailable (e.g., for delisted stocks).
    """
    yahoo_symbol = f"{symbol}.NS"
    try:
        ticker = yf.Ticker(yahoo_symbol)
        shares = ticker.info.get('sharesOutstanding')
        return int(shares) if shares else None
    except Exception as e:
        logger.warning(f"Failed to fetch Yahoo current shares for {symbol}: {e}")
        return None
```

**How Method B enhances Method A:**
```python
async def calculate_historical_market_cap_with_yahoo_validation(
    session, stock_id: int, symbol: str,
    current_issued_shares: int, start_date: date, end_date: date
):
    """
    Calculate historical market cap using Method A (reverse-engineering),
    then validate/override with Method B (Yahoo Finance) where available.
    """
    # Step 1: Calculate using Method A (reverse-engineering)
    await calculate_historical_market_cap_method_a(
        session, stock_id, current_issued_shares
    )

    # Step 2: Try Yahoo Finance for validation
    yahoo_shares = get_historical_shares_outstanding(symbol, start_date, end_date)

    if yahoo_shares is None or yahoo_shares.empty:
        logger.info(f"{symbol}: Using reverse-engineered shares only (Yahoo unavailable)")
        return

    # Step 3: Compare and override where Yahoo data differs significantly
    market_caps = session.execute(
        select(MarketCap)
        .where(MarketCap.stock_id == stock_id)
        .order_by(MarketCap.trade_date.asc())
    ).scalars().all()

    updates = []
    for mc in market_caps:
        trade_dt = pd.Timestamp(mc.trade_date)
        if trade_dt in yahoo_shares.index:
            yahoo_count = int(yahoo_shares[trade_dt])
            reverse_count = mc.issued_shares

            # If Yahoo differs by > 5%, prefer Yahoo (likely a rights/buyback event)
            if abs(yahoo_count - reverse_count) / reverse_count > 0.05:
                new_mcap = yahoo_count * float(mc.close_price)
                updates.append({
                    "id": mc.id,
                    "issued_shares": yahoo_count,
                    "market_cap": round(new_mcap, 2),
                    "shares_source": "YAHOO_FINANCE",
                })

    if updates:
        for u in updates:
            session.execute(
                update(MarketCap)
                .where(MarketCap.id == u["id"])
                .values(
                    issued_shares=u["issued_shares"],
                    market_cap=u["market_cap"],
                    shares_source=u["shares_source"]
                )
            )
        session.commit()
        logger.info(
            f"{symbol}: Updated {len(updates)} market cap records from Yahoo Finance "
            f"(detected non-split/bonus share changes)"
        )
```

### 11.4 Method C: NSE Quote API (INCREMENTAL — Daily Updates)

**For daily updates**, we don't need reverse-engineering. We fetch the latest `issuedSize` directly from NSE:

```
INCREMENTAL MARKET CAP (for each new trading date):
1. For each active stock with a new price record:
   a. Use stocks.issued_shares (last known value)
   b. market_cap = issued_shares × today's close
   c. INSERT into market_cap with shares_source = 'NSE_QUOTE'
2. Periodically (weekly), re-fetch issuedSize from NSE quote API:
   a. If changed → update stocks.issued_shares
   b. Recalculate recent market cap entries
```

### 11.5 Handling Delisted Stocks

```
Delisted stocks pose a challenge:
- Cannot fetch current issuedSize from NSE (stock doesn't exist)
- Cannot reverse-engineer without a starting point

Strategy for delisted stocks:
1. Try Yahoo Finance: get_current_shares_outstanding(symbol)
   → Yahoo sometimes retains data for delisted stocks
2. If Yahoo has data → use it as the "current" base, reverse-engineer from there
3. If Yahoo has NO data → market_cap = NULL for this stock
   (log warning, mark stock as market_cap_unavailable)
4. Historical market cap for delisted stocks is best-effort, not guaranteed
```

### 11.6 Accuracy Summary

| Scenario | Method | Accuracy | Notes |
|----------|--------|----------|-------|
| Stock with only splits/bonus | Method A | **99%+** | Reverse-engineering is exact for these events |
| Stock with rights issues or buybacks | Method A + B | **95-99%** | Yahoo quarterly data catches most |
| Recently listed stock | Method C | **100%** | Direct from NSE, no history to worry about |
| Delisted stock (Yahoo has data) | Method B | **90-95%** | Quarterly resolution, forward-filled |
| Delisted stock (no data anywhere) | — | **N/A** | Market cap marked as NULL |

### 11.7 Important Business Rules (Unchanged)

> [!IMPORTANT]
> - Market Cap is calculated from **RAW** prices (never adjusted prices)
> - Market Cap is **NEVER retroactively modified** after initial calculation
> - Historical shares outstanding reflects the **approximate** count on that date
> - The `shares_source` column tracks provenance for auditability

---

## 12. Technical Indicators Engine

### 12.1 Indicator List & Parameters

| Indicator | Parameters | Source | Column Names |
|-----------|-----------|--------|--------------|
| SMA | length=[20, 50, 200] | adj_close | `sma_20`, `sma_50`, `sma_200` |
| EMA | length=[12, 26] | adj_close | `ema_12`, `ema_26` |
| RSI | length=14 | adj_close | `rsi_14` |
| MACD | fast=12, slow=26, signal=9 | adj_close | `macd_line`, `macd_signal`, `macd_histogram` |
| Bollinger Bands | length=20, std=2 | adj_close | `bb_upper`, `bb_middle`, `bb_lower` |
| ATR | length=14 | adj_high, adj_low, adj_close | `atr_14` |
| OBV | — | adj_close, adj_volume | `obv` |
| VWAP | — | adj_high, adj_low, adj_close, adj_volume | `vwap` |
| Stochastic | k=14, d=3, smooth_k=3 | adj_high, adj_low, adj_close | `stoch_k`, `stoch_d` |

### 12.2 Algorithm (`services/indicators.py`)

```python
"""
Technical Indicators Engine

Calculates all indicators from ADJUSTED prices and stores in stock_indicators table.
Uses pandas-ta library for calculation.
"""

import pandas as pd
import pandas_ta as ta


async def calculate_indicators_for_stock(session, stock_id: int):
    """Calculate all technical indicators for a stock using adjusted prices."""

    # Step 1: Load adjusted prices as DataFrame
    result = session.execute(
        select(AdjustedPrice)
        .where(AdjustedPrice.stock_id == stock_id)
        .order_by(AdjustedPrice.trade_date.asc())
    )
    rows = result.scalars().all()

    if len(rows) < 200:  # Need enough data for SMA(200)
        # Still calculate what we can, but some indicators will be NaN
        pass

    df = pd.DataFrame([{
        "trade_date": r.trade_date,
        "open": float(r.adj_open),
        "high": float(r.adj_high),
        "low": float(r.adj_low),
        "close": float(r.adj_close),
        "volume": int(r.adj_volume),
    } for r in rows])

    if df.empty:
        return

    # Step 2: Calculate indicators
    # SMA
    df["sma_20"] = ta.sma(df["close"], length=20)
    df["sma_50"] = ta.sma(df["close"], length=50)
    df["sma_200"] = ta.sma(df["close"], length=200)

    # EMA
    df["ema_12"] = ta.ema(df["close"], length=12)
    df["ema_26"] = ta.ema(df["close"], length=26)

    # RSI
    df["rsi_14"] = ta.rsi(df["close"], length=14)

    # MACD
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd_df is not None:
        df["macd_line"] = macd_df.iloc[:, 0]      # MACD_12_26_9
        df["macd_signal"] = macd_df.iloc[:, 2]     # MACDs_12_26_9
        df["macd_histogram"] = macd_df.iloc[:, 1]  # MACDh_12_26_9

    # Bollinger Bands
    bb_df = ta.bbands(df["close"], length=20, std=2)
    if bb_df is not None:
        df["bb_lower"] = bb_df.iloc[:, 0]    # BBL_20_2.0
        df["bb_middle"] = bb_df.iloc[:, 1]   # BBM_20_2.0
        df["bb_upper"] = bb_df.iloc[:, 2]    # BBU_20_2.0

    # ATR
    df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    # OBV
    df["obv"] = ta.obv(df["close"], df["volume"])

    # VWAP (simplified — true VWAP needs intraday data; using hlc3 × vol / cumvol)
    df["vwap"] = ta.vwap(df["high"], df["low"], df["close"], df["volume"])

    # Stochastic
    stoch_df = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3, smooth_k=3)
    if stoch_df is not None:
        df["stoch_k"] = stoch_df.iloc[:, 0]
        df["stoch_d"] = stoch_df.iloc[:, 1]

    # Step 3: Prepare records for DB insertion
    indicator_records = []
    for _, row in df.iterrows():
        record = {
            "stock_id": stock_id,
            "trade_date": row["trade_date"],
        }
        for col in ["sma_20", "sma_50", "sma_200", "ema_12", "ema_26",
                     "rsi_14", "macd_line", "macd_signal", "macd_histogram",
                     "bb_upper", "bb_middle", "bb_lower", "atr_14",
                     "obv", "vwap", "stoch_k", "stoch_d"]:
            val = row.get(col)
            record[col] = round(float(val), 4) if pd.notna(val) else None
        indicator_records.append(record)

    # Step 4: Delete existing and re-insert (simpler than upsert)
    session.execute(
        delete(StockIndicator).where(StockIndicator.stock_id == stock_id)
    )
    session.bulk_insert_mappings(StockIndicator, indicator_records)
    await session.commit()
```

### 12.3 When to Recalculate

| Event | Action |
|-------|--------|
| New daily prices added | Calculate indicators for the new date(s) only (append) |
| Corporate action applied (adjusted prices change) | Full recalculation for affected stock |
| User changes indicator parameters | Full recalculation for all stocks |

---

## 13. Symbol Change Handler

### 13.1 Algorithm (`services/symbol_changes.py`)

```
1. Download symbolchange.csv from NSE
2. Parse into list of (old_symbol, new_symbol, effective_date)
3. For each change:
   a. Check if old_symbol exists in stocks table
   b. If yes AND new_symbol doesn't exist:
      - UPDATE stocks SET symbol = new_symbol WHERE symbol = old_symbol
      - INSERT into symbol_changes table with is_applied = TRUE
   c. If old_symbol doesn't exist → might have already been applied
      - Check symbol_changes table to see if we've processed this
      - If not → INSERT with is_applied = FALSE (stock may not be in our DB)
   d. If both exist → conflict, log warning for manual review
```

> [!NOTE]
> Symbol changes only affect the `stocks.symbol` column. All other tables reference `stock_id` (integer FK), so they don't need any updates. This is why we use integer IDs as foreign keys, not the symbol string.

---

## 14. Sync Manager & Orchestration

### 14.1 Full Sync Workflow (`services/sync_manager.py`)

```
INITIAL LOAD (when database is empty — BHAVCOPY-FIRST strategy):
════════════════════════════════════════════════════════════════

>>> KEY PRINCIPLE: Bhavcopies are downloaded FIRST. Stocks are auto-discovered
>>> from the bhavcopy data itself. Master list is used ONLY to enrich metadata
>>> afterwards. This captures delisted stocks, old symbols, and all historical
>>> securities that may not appear in the current EQUITY_L.csv.

Step 0: Pre-download ETF List (needed to separate ETFs from stocks in bhavcopy)
  → Download eq_etfseclist.csv → populate etfs table
  → Build a set of known ETF symbols for filtering
  → Ensure TRACKED_INDEXES exist in indexes table

Step 1: ★ Historical Bhavcopy — STOCK DISCOVERY (date by date from START_DATE to today)
  → This is the PRIMARY data ingestion step.
  → For each trading day:
     a. Download bhavcopy ZIP
     b. Parse CSV, filter SctySrs in ['EQ', 'BE']
     c. For each row:
        - Call find_or_create_stock(symbol, series, isin, trade_date)
          → Looks up by ISIN first, then symbol
          → If not found → AUTO-CREATES a new stock record with:
             company_name=NULL, face_value=NULL,
             data_source='BHAVCOPY_DISCOVERED',
             first_seen_date=trade_date
          → If found → updates last_seen_date
        - Insert price into stock_prices
     d. Separate ETFs (symbol in ETF set) → store in etf_prices
     e. Download index CSV, store index prices
     f. Log to sync_log
     g. Sleep (rate limit)
  → Progress: report % complete to UI
  → At end of this step, stocks table contains ALL securities ever traded
     in our date range — including delisted stocks and old symbols.

Step 2: Master Data ENRICHMENT (fills in metadata for discovered stocks)
  → Download EQUITY_L.csv (current master list)
  → For each stock in DB:
     a. Match against master list by ISIN, then symbol
     b. If matched → UPDATE: company_name, face_value, listing_date,
        data_source='MASTER_LIST', is_active=TRUE
     c. If NOT matched → mark is_delisted=TRUE, is_active=FALSE
        (stock existed historically but is no longer listed)
  → For master list entries NOT in DB → INSERT (recently listed stocks
     outside our historical date range)
  → Log: "Enriched X stocks from master list, Y marked as delisted"

Step 3: Symbol Changes (apply BEFORE shares outstanding fetch)
  → Download symbolchange.csv
  → Apply symbol renames to stocks table
  → This ensures we fetch quotes using current symbols in Step 4

Step 4: Shares Outstanding (for market cap — only for ACTIVE stocks)
  → For each stock where is_active=TRUE AND is_delisted=FALSE:
     a. Fetch quote API for issuedSize (current shares outstanding)
     b. Update stocks.issued_shares
     c. Sleep (rate limit — ~2000 stocks × 3s = ~100 min)
  → For delisted stocks: try Yahoo Finance as fallback
     (get_current_shares_outstanding via yfinance)

Step 5: Corporate Actions ★ (BEFORE Market Cap — needed for reverse-engineering)
  → Fetch corporate actions from START_DATE to today
  → Parse splits and bonus
  → Store in corporate_actions table
  → NOTE: Must run before Market Cap because historical shares are
    reverse-engineered using corporate action adjustment factors

Step 6: Price Adjustment
  → For each stock with corporate actions:
     Calculate cumulative adjustment factors
     Generate adjusted_prices

Step 7: Adjusted Prices for stocks WITHOUT corporate actions
  → Copy raw prices as-is (factor = 1.0) into adjusted_prices

Step 8: ★ Historical Market Cap (uses Method A + Method B from Section 11)
  → For each stock with issued_shares:
     a. Reverse-engineer historical shares outstanding:
        historical_shares[date] = current_issued_shares / cumulative_factor[date]
        (reuses same cumulative_factor from Step 6)
     b. market_cap = historical_shares × raw_close_price
     c. INSERT into market_cap with shares_source = 'REVERSE_ENGINEERED'
  → Then validate with Yahoo Finance (Method B):
     a. For each stock, fetch yfinance get_shares_full()
     b. Compare quarterly Yahoo data with reverse-engineered values
     c. If discrepancy > 5%, override with Yahoo data
        (indicates rights issue, buyback, or QIP we didn't capture)
     d. Update shares_source = 'YAHOO_FINANCE' for overridden records
  → For delisted stocks with Yahoo data: calculate from Yahoo shares
  → For delisted stocks without ANY shares data: market_cap = NULL
  → Progress: report % complete to UI (this step is slow due to Yahoo API)

Step 9: Technical Indicators
  → For each stock:
     Calculate all indicators from adjusted prices
     Store in stock_indicators

Step 10: Final
  → Log full sync completion to sync_log
  → Summary: "Discovered X stocks (Y active, Z delisted), loaded N price records,
              market cap calculated for M stocks (R reverse-engineered, Q Yahoo-validated)"


INCREMENTAL UPDATE (daily, after initial load):
═══════════════════════════════════════════════
Step 1: Refresh Master Data
  → Download EQUITY_L.csv, update/enrich stocks table
  → Any new listings get added with data_source='MASTER_LIST'

Step 2: Determine date range
  → Query sync_log for last successful BHAVCOPY sync date
  → Generate list of trading days from (last_date + 1) to today

Step 3: Download missing bhavcopies
  → For each missing date, process bhavcopy:
     - Stocks already in DB → insert prices, update last_seen_date
     - Unknown stocks → auto-create (new listing discovered in bhavcopy)

Step 4: Check for new corporate actions
  → Fetch from last check date to today
  → If new actions found:
     a. Store in corporate_actions
     b. Recalculate adjusted prices for affected stocks
     c. Re-fetch issuedSize for affected stocks (if bonus/split)

Step 5: Market Cap for new dates (Method C — direct from NSE)
  → For each active stock with a new price record:
     market_cap = stocks.issued_shares × today's raw close
     INSERT into market_cap with shares_source = 'NSE_QUOTE'
  → Periodically (weekly), re-fetch issuedSize from NSE quote API
     to catch any share count changes we missed

Step 6: Indicators for updated stocks
  → Recalculate indicators for stocks with new data or adjusted prices changes

Step 7: Symbol changes
  → Re-download and apply any new changes
```

### 14.2 Progress Reporting

The sync manager should emit progress events that the UI can listen to:

```python
from dataclasses import dataclass
from enum import Enum


class SyncStage(Enum):
    MASTER_DATA = "Master Data"
    BHAVCOPY = "Bhavcopy Download"
    INDEX_DATA = "Index Data"
    SHARES_OUTSTANDING = "Shares Outstanding"
    MARKET_CAP = "Market Cap"
    CORPORATE_ACTIONS = "Corporate Actions"
    PRICE_ADJUSTMENT = "Price Adjustment"
    INDICATORS = "Indicators"
    SYMBOL_CHANGES = "Symbol Changes"


@dataclass
class SyncProgress:
    stage: SyncStage
    current: int
    total: int
    message: str

    @property
    def percentage(self) -> float:
        return (self.current / self.total * 100) if self.total > 0 else 0
```

---

## 15. UI Specification

### 15.1 App Entry Point (`ui/app.py`)

```python
from nicegui import ui, app
from .theme import apply_theme
from .layout import create_layout
from .pages import dashboard, stocks, stock_detail, indexes, etfs, download, \
    corporate_actions, symbol_changes, settings_page


def create_app():
    """Create and configure the NiceGUI application."""

    apply_theme()

    # Define pages with routes
    @ui.page("/")
    def page_dashboard():
        create_layout(active="dashboard")
        dashboard.render()

    @ui.page("/stocks")
    def page_stocks():
        create_layout(active="stocks")
        stocks.render()

    @ui.page("/stocks/{symbol}")
    def page_stock_detail(symbol: str):
        create_layout(active="stocks")
        stock_detail.render(symbol)

    @ui.page("/indexes")
    def page_indexes():
        create_layout(active="indexes")
        indexes.render()

    @ui.page("/etfs")
    def page_etfs():
        create_layout(active="etfs")
        etfs.render()

    @ui.page("/download")
    def page_download():
        create_layout(active="download")
        download.render()

    @ui.page("/corporate-actions")
    def page_corporate_actions():
        create_layout(active="corporate_actions")
        corporate_actions.render()

    @ui.page("/symbol-changes")
    def page_symbol_changes():
        create_layout(active="symbol_changes")
        symbol_changes.render()

    @ui.page("/settings")
    def page_settings():
        create_layout(active="settings")
        settings_page.render()


def main():
    """Application entry point."""
    from config.settings import settings
    create_app()
    ui.run(
        title=settings.app_title,
        host=settings.app_host,
        port=settings.app_port,
        native=settings.app_native,  # True = desktop window, False = web browser
        reload=False,
        dark=settings.app_dark_mode,
        window_size=(1400, 900),
    )
```

### 15.2 Theme (`ui/theme.py`)

```python
from nicegui import ui


def apply_theme():
    """Apply custom dark theme with premium aesthetics."""

    ui.add_head_html('''
    <style>
        :root {
            --primary: #6366f1;        /* Indigo */
            --primary-light: #818cf8;
            --primary-dark: #4f46e5;
            --accent: #22d3ee;         /* Cyan */
            --success: #10b981;        /* Emerald */
            --warning: #f59e0b;        /* Amber */
            --danger: #ef4444;         /* Red */
            --surface: #1e1e2e;        /* Dark surface */
            --surface-light: #2a2a3e;
            --text-primary: #e2e8f0;
            --text-secondary: #94a3b8;
            --gain: #10b981;           /* Green for positive */
            --loss: #ef4444;           /* Red for negative */
        }

        body {
            font-family: 'Inter', 'Segoe UI', sans-serif;
            background: var(--surface);
        }

        /* Glassmorphism card effect */
        .glass-card {
            background: rgba(30, 30, 46, 0.8);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 12px;
            padding: 20px;
        }

        /* Stat card styling */
        .stat-card {
            background: linear-gradient(135deg, var(--surface-light), var(--surface));
            border-radius: 16px;
            padding: 24px;
            border: 1px solid rgba(255, 255, 255, 0.06);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .stat-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
        }

        /* Sidebar styling */
        .sidebar-item {
            border-radius: 8px;
            transition: background 0.2s;
        }
        .sidebar-item:hover {
            background: rgba(99, 102, 241, 0.15);
        }
        .sidebar-item.active {
            background: rgba(99, 102, 241, 0.25);
            border-left: 3px solid var(--primary);
        }
    </style>

    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    ''')
```

### 15.3 Layout (`ui/layout.py`)

```python
from nicegui import ui

NAV_ITEMS = [
    {"id": "dashboard",        "label": "Dashboard",          "icon": "dashboard",      "route": "/"},
    {"id": "stocks",           "label": "Stocks",             "icon": "trending_up",    "route": "/stocks"},
    {"id": "indexes",          "label": "Indexes",            "icon": "show_chart",     "route": "/indexes"},
    {"id": "etfs",             "label": "ETFs",               "icon": "account_balance","route": "/etfs"},
    {"id": "download",         "label": "Download",           "icon": "cloud_download", "route": "/download"},
    {"id": "corporate_actions","label": "Corporate Actions",  "icon": "swap_horiz",     "route": "/corporate-actions"},
    {"id": "symbol_changes",   "label": "Symbol Changes",     "icon": "find_replace",   "route": "/symbol-changes"},
    {"id": "settings",         "label": "Settings",           "icon": "settings",       "route": "/settings"},
]


def create_layout(active: str = "dashboard"):
    """Create the shared layout with sidebar navigation."""

    with ui.header().classes("bg-[#1a1a2e] border-b border-white/10"):
        with ui.row().classes("w-full items-center justify-between px-4"):
            ui.label("🏛️ NSE Data Manager").classes("text-xl font-bold text-white")

            # Global stock search
            ui.input(placeholder="Search stocks...") \
                .props('outlined dense dark') \
                .classes("w-64")

            ui.icon("dark_mode").classes("text-white cursor-pointer text-2xl")

    with ui.left_drawer().classes("bg-[#1a1a2e] border-r border-white/10").props("width=240"):
        ui.label("NAVIGATION").classes("text-xs text-gray-500 font-semibold px-4 py-2 mt-2")

        for item in NAV_ITEMS:
            is_active = item["id"] == active
            with ui.row().classes(
                f"items-center gap-3 px-4 py-3 cursor-pointer sidebar-item "
                f"{'active' if is_active else ''}"
            ).on("click", lambda _, r=item["route"]: ui.navigate.to(r)):
                ui.icon(item["icon"]).classes(
                    f"text-xl {'text-indigo-400' if is_active else 'text-gray-400'}"
                )
                ui.label(item["label"]).classes(
                    f"text-sm {'text-white font-semibold' if is_active else 'text-gray-400'}"
                )

    # Footer status bar
    with ui.footer().classes("bg-[#1a1a2e] border-t border-white/10 py-1"):
        with ui.row().classes("w-full items-center justify-between px-4"):
            ui.label("Last Sync: --").classes("text-xs text-gray-500")
            ui.label("Stocks: -- | DB: --").classes("text-xs text-gray-500")
```

### 15.4 Key Page Specifications

#### Dashboard Page
- **4 stat cards** in a row: Total Stocks, Total ETFs, Last Sync Date, DB Size
- **Market Summary section**: Nifty 50 mini line chart (ECharts), today's value + change%
- **Top Gainers / Top Losers**: Two side-by-side AG Grid tables (5 rows each)
- **Recent Sync Activity**: List of last 10 sync log entries
- **Quick Action Buttons**: "Sync Now", "Full History Download"

#### Stocks Page
- **AG Grid** with columns: Symbol, Company Name, Series, Close, Change%, Volume, Market Cap, RSI(14), SMA Status
- **Column sorting** on all columns
- **Text filter** on symbol/company name
- **Row click** navigates to `/stocks/{symbol}`
- **Export** button (CSV download)

#### Stock Detail Page
- **Header**: Symbol, Company Name, ISIN, Close Price, Change (₹ and %), Market Cap
- **Candlestick Chart** (ECharts): Main chart showing OHLCV with volume bars below
- **Indicator toggles**: Checkboxes to overlay SMA(20/50/200), EMA, Bollinger Bands on chart
- **Indicator subplots**: RSI panel, MACD panel below main chart
- **Corporate Actions markers**: Vertical lines on chart at ex-dates with labels
- **Data Table** (AG Grid): Date, Open, High, Low, Close, Volume, SMA20, RSI14, MACD

#### Download Page
- **Status banner**: "Last sync: {date}" or "Never synced"
- **Date range selector**: From date, To date (with calendar pickers)
- **Checkboxes**: Stocks, ETFs, Indexes, Corporate Actions, Market Cap, Indicators
- **"Start Download" button**: Triggers sync_manager
- **Progress section**: Per-stage progress bars (Master Data ▓▓▓░░ 60%)
- **Live log**: Scrollable text area showing real-time log messages
- **"Cancel" button**: Stops the current sync

### 15.5 ECharts Candlestick Chart Component (`components/price_chart.py`)

```python
from nicegui import ui


def create_candlestick_chart(dates: list, ohlc: list, volumes: list,
                              indicators: dict = None,
                              corporate_actions: list = None):
    """
    Create an ECharts candlestick chart with volume bars.

    Args:
        dates: List of date strings ['2024-01-01', '2024-01-02', ...]
        ohlc: List of [open, close, low, high] for each date
        volumes: List of volume values
        indicators: Dict of indicator overlays, e.g. {"SMA 20": [values...]}
        corporate_actions: List of {"date": "2024-03-15", "type": "SPLIT", "label": "5:1 Split"}
    """

    # Build series list
    series = [
        {
            "name": "Price",
            "type": "candlestick",
            "data": ohlc,  # [open, close, low, high] format for ECharts
            "itemStyle": {
                "color": "#10b981",       # Bullish (green)
                "color0": "#ef4444",      # Bearish (red)
                "borderColor": "#10b981",
                "borderColor0": "#ef4444",
            },
        }
    ]

    # Add indicator overlays
    if indicators:
        colors = ["#f59e0b", "#6366f1", "#22d3ee", "#f472b6", "#a78bfa"]
        for i, (name, values) in enumerate(indicators.items()):
            series.append({
                "name": name,
                "type": "line",
                "data": values,
                "smooth": True,
                "lineStyle": {"width": 1.5},
                "itemStyle": {"color": colors[i % len(colors)]},
                "symbol": "none",
            })

    # Volume bars (in separate grid below)
    volume_series = {
        "name": "Volume",
        "type": "bar",
        "data": volumes,
        "xAxisIndex": 1,
        "yAxisIndex": 1,
        "itemStyle": {"color": "rgba(99, 102, 241, 0.4)"},
    }
    series.append(volume_series)

    # Corporate action markers
    mark_lines = []
    if corporate_actions:
        for ca in corporate_actions:
            mark_lines.append({
                "xAxis": ca["date"],
                "label": {"formatter": ca["label"], "fontSize": 10},
                "lineStyle": {"type": "dashed", "color": "#f59e0b"},
            })

    if mark_lines:
        series[0]["markLine"] = {
            "data": mark_lines,
            "symbol": "none",
        }

    chart_options = {
        "animation": True,
        "backgroundColor": "transparent",
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "cross"},
        },
        "legend": {
            "data": [s["name"] for s in series if s["name"] != "Volume"],
            "textStyle": {"color": "#94a3b8"},
            "top": 10,
        },
        "grid": [
            {"left": "8%", "right": "4%", "top": "15%", "height": "55%"},   # Price grid
            {"left": "8%", "right": "4%", "top": "75%", "height": "15%"},   # Volume grid
        ],
        "xAxis": [
            {"type": "category", "data": dates, "gridIndex": 0,
             "axisLabel": {"color": "#94a3b8"}},
            {"type": "category", "data": dates, "gridIndex": 1,
             "axisLabel": {"show": False}},
        ],
        "yAxis": [
            {"type": "value", "gridIndex": 0, "splitLine": {"lineStyle": {"color": "rgba(255,255,255,0.05)"}},
             "axisLabel": {"color": "#94a3b8"}},
            {"type": "value", "gridIndex": 1, "splitLine": {"show": False},
             "axisLabel": {"show": False}},
        ],
        "dataZoom": [
            {"type": "inside", "xAxisIndex": [0, 1], "start": 70, "end": 100},
            {"type": "slider", "xAxisIndex": [0, 1], "start": 70, "end": 100,
             "bottom": 5, "height": 20},
        ],
        "series": series,
    }

    ui.echart(chart_options).classes("w-full h-[500px]")
```

### 15.6 AG Grid Stock Table Component (`components/stock_table.py`)

```python
from nicegui import ui


def create_stock_table(stocks_data: list[dict]):
    """
    Create an AG Grid table for stock listing.

    Args:
        stocks_data: List of dicts with keys: symbol, company_name, series,
                     close, change_pct, volume, market_cap, rsi_14
    """

    columns = [
        {"headerName": "Symbol", "field": "symbol", "width": 100,
         "filter": "agTextColumnFilter", "pinned": "left",
         "cellStyle": {"fontWeight": "bold", "color": "#818cf8"}},
        {"headerName": "Company", "field": "company_name", "width": 200,
         "filter": "agTextColumnFilter"},
        {"headerName": "Series", "field": "series", "width": 70},
        {"headerName": "Close", "field": "close", "width": 100,
         "type": "numericColumn",
         "valueFormatter": "x => x ? '₹' + Number(x).toFixed(2) : '-'"},
        {"headerName": "Change %", "field": "change_pct", "width": 100,
         "type": "numericColumn",
         "valueFormatter": "x => x ? Number(x).toFixed(2) + '%' : '-'",
         "cellStyle": """params => ({
             color: params.value > 0 ? '#10b981' : params.value < 0 ? '#ef4444' : '#94a3b8'
         })"""},
        {"headerName": "Volume", "field": "volume", "width": 120,
         "type": "numericColumn",
         "valueFormatter": "x => x ? Number(x).toLocaleString('en-IN') : '-'"},
        {"headerName": "Market Cap (Cr)", "field": "market_cap_cr", "width": 130,
         "type": "numericColumn",
         "valueFormatter": "x => x ? '₹' + Number(x).toLocaleString('en-IN') : '-'"},
        {"headerName": "RSI(14)", "field": "rsi_14", "width": 90,
         "type": "numericColumn",
         "valueFormatter": "x => x ? Number(x).toFixed(1) : '-'",
         "cellStyle": """params => ({
             color: params.value > 70 ? '#ef4444' : params.value < 30 ? '#10b981' : '#94a3b8'
         })"""},
    ]

    grid = ui.aggrid({
        "columnDefs": columns,
        "rowData": stocks_data,
        "defaultColDef": {
            "sortable": True,
            "filter": True,
            "resizable": True,
        },
        "pagination": True,
        "paginationPageSize": 50,
        "domLayout": "autoHeight",
        "rowSelection": "single",
        "animateRows": True,
    }).classes("w-full").style("height: 600px")

    # Handle row click → navigate to stock detail
    grid.on("cellClicked", lambda e: ui.navigate.to(f"/stocks/{e.args['data']['symbol']}"))

    return grid
```

---

## 16. Error Handling & Resilience

### 16.1 NSE Request Errors

| HTTP Status | Meaning | Action |
|-------------|---------|--------|
| **200** | Success | Process response |
| **401/403** | Session expired or blocked | Refresh cookies, retry (max 3 times) |
| **404** | Holiday/weekend (no data for this date) | Skip date, log as holiday, no error |
| **429** | Too many requests | Back off exponentially (5s, 10s, 20s) |
| **5xx** | NSE server error | Retry after 10s (max 3 times) |
| **Timeout** | Connection timeout | Retry after 5s |

### 16.2 Data Validation Rules

```python
def validate_price_row(row: dict) -> bool:
    """Validate a single price row from bhavcopy."""
    # Prices must be positive
    for field in ["open", "high", "low", "close"]:
        if row.get(field, 0) <= 0:
            return False

    # High >= Low always
    if row["high"] < row["low"]:
        return False

    # High >= Open and High >= Close
    if row["high"] < row["open"] or row["high"] < row["close"]:
        return False

    # Low <= Open and Low <= Close
    if row["low"] > row["open"] or row["low"] > row["close"]:
        return False

    # Volume must be non-negative
    if row.get("volume", 0) < 0:
        return False

    return True
```

### 16.3 Sync Failure Recovery

- Each sync step logs to `sync_log` with status `STARTED` → `SUCCESS` or `FAILED`
- On restart after failure, check `sync_log` for `STARTED` entries (incomplete)
- Resume from the last successful step/date
- Failed individual dates are skipped and retried on next run

---

## 17. Testing Strategy

### 17.1 Test Fixtures (`tests/conftest.py`)

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.base import Base

@pytest.fixture
def test_engine():
    """Create an in-memory DuckDB for testing."""
    engine = create_engine("duckdb:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()

@pytest.fixture
def test_session(test_engine):
    """Create a test database session."""
    Session = sessionmaker(bind=test_engine)
    session = Session()
    yield session
    session.close()

@pytest.fixture
def sample_bhavcopy_df():
    """Sample bhavcopy DataFrame for testing."""
    import pandas as pd
    return pd.DataFrame({
        "TradDt": ["2024-06-05"] * 3,
        "FinInstrmId": ["RELIANCE", "TCS", "INFY"],
        "ISIN": ["INE002A01018", "INE467B01029", "INE009A01021"],
        "SctySrs": ["EQ", "EQ", "EQ"],
        "OpnPric": [1285.50, 3800.00, 1450.00],
        "HghPric": [1292.00, 3820.00, 1460.00],
        "LwPric": [1275.10, 3780.00, 1440.00],
        "ClsPric": [1288.35, 3810.50, 1455.25],
        "TtlTradgVol": [12345678, 5678901, 9876543],
        "TtlTrfVal": [159123456.78, 21612345.90, 14321098.67],
    })
```

### 17.2 Key Test Cases

```python
# test_price_adjuster.py

def test_split_adjustment():
    """Test that a 5:1 split correctly adjusts pre-split prices."""
    # Stock at Rs.1000, then 10→2 split (factor = 5)
    # Pre-split adj_close should be 1000/5 = 200
    # Post-split price of Rs.200 should remain 200

def test_bonus_adjustment():
    """Test that a 1:1 bonus correctly adjusts pre-bonus prices."""
    # Stock at Rs.500, then 1:1 bonus (factor = 2)
    # Pre-bonus adj_close should be 500/2 = 250
    # Post-bonus price of Rs.250 should remain 250

def test_multiple_actions_compound():
    """Test that multiple actions compound correctly."""
    # Bonus 1:1 on 2024-03-15 (factor 2)
    # Split 10→2 on 2024-09-10 (factor 5)
    # Price of Rs.1000 on 2024-01-01:
    #   adj_close = 1000 / (2 × 5) = 100

def test_market_cap_not_adjusted():
    """Test that market cap uses raw prices, not adjusted."""
    # Market cap on 2024-01-01 should use raw close of Rs.1000
    # NOT the adjusted close of Rs.100

def test_indicators_use_adjusted_prices():
    """Test that SMA/RSI/MACD use adjusted prices."""
    # SMA(20) should be calculated from adj_close column

def test_symbol_change_preserves_data():
    """Test that symbol change updates symbol without losing price data."""
    # Rename XYZOLD → XYZNEW
    # All price records should still be accessible under new symbol
```

---

## 18. Implementation Order

> [!IMPORTANT]
> Follow this exact order. Each phase depends on the previous one.

### Phase 1: Project Skeleton & Database (Do First)
```
1. Create pyproject.toml with all dependencies
2. Create .env and config/settings.py
3. Create config/constants.py with all NSE URLs
4. Create models/base.py
5. Create ALL model files (stock.py, index.py, etf.py, etc.)
6. Create db/engine.py (create_engine, sessionmaker)
7. Set up Alembic and run initial migration
8. Create scripts/init_db.py to verify tables create correctly
9. TEST: Run init_db.py, verify all tables exist in market.db
```

### Phase 2: NSE Client & Bhavcopy Download (Stock Discovery)
```
1. Create services/nse_client.py (complete HTTP client)
2. Create utils/date_utils.py (trading day helpers)
3. Create services/stock_downloader.py with find_or_create_stock() helper
4. Create services/index_downloader.py
5. Create services/etf_downloader.py (download ETF list for filtering)
6. Create db/repository.py with bulk insert helpers
7. TEST: Download one day's bhavcopy, verify:
   - Stocks are auto-discovered (created in stocks table from bhavcopy data)
   - Price data is stored correctly
   - ETFs are separated and stored in etf_prices
8. Implement historical loop (START_DATE to today)
9. TEST: After full historical download, verify:
   - stocks table has MORE entries than EQUITY_L.csv (includes delisted)
   - Delisted/old stocks have company_name=NULL, data_source='BHAVCOPY_DISCOVERED'
```

### Phase 3: Master Data Enrichment
```
1. Create services/master_data.py with enrich mode
2. Download EQUITY_L.csv, match against bhavcopy-discovered stocks
3. TEST: After enrichment, verify:
   - Active stocks have company_name, face_value, listing_date populated
   - Stocks NOT in master list are marked is_delisted=TRUE
   - data_source upgraded from 'BHAVCOPY_DISCOVERED' to 'MASTER_LIST' for matched stocks
4. TEST: Count active vs delisted stocks, verify numbers are reasonable
```

### Phase 4: Corporate Actions & Adjustments
```
1. Create utils/parsers.py (split/bonus text parsing)
2. Create services/corporate_actions.py
3. TEST: Fetch corporate actions, verify parsing of subject text
4. Create services/price_adjuster.py
5. TEST: Verify split adjustment math
6. TEST: Verify bonus adjustment math
7. TEST: Verify compound adjustments
```

### Phase 5: Market Cap
```
1. Create services/market_cap.py
2. TEST: Fetch issuedSize for a few stocks
3. Implement bulk market cap calculation
4. Verify market cap uses RAW prices (not adjusted)
```

### Phase 6: Technical Indicators
```
1. Create services/indicators.py
2. TEST: Calculate indicators for one stock, compare with TradingView
3. Implement batch calculation for all stocks
4. Verify indicators use ADJUSTED prices
```

### Phase 7: Symbol Changes
```
1. Create services/symbol_changes.py
2. TEST: Parse symbolchange.csv
3. Implement apply logic
```

### Phase 8: Sync Manager
```
1. Create services/sync_manager.py (orchestrates all steps)
2. Implement initial load workflow
3. Implement incremental update workflow
4. Add progress reporting
```

### Phase 9: Basic UI Shell
```
1. Create ui/theme.py (dark theme, colors, fonts)
2. Create ui/layout.py (sidebar, header, footer)
3. Create ui/app.py (routes, entry point)
4. Create src/main.py
5. TEST: Run app, verify sidebar navigation works
```

### Phase 10: UI Pages
```
1. Dashboard page with stat cards
2. Download page with progress bars
3. Stocks page with AG Grid
4. Stock Detail page with candlestick chart
5. Indexes page
6. ETFs page
7. Corporate Actions page
8. Symbol Changes page
9. Settings page
```

### Phase 11: Polish
```
1. Add comprehensive error handling
2. Add loading spinners and empty states
3. Write unit tests
4. Performance optimize (batch inserts, indexing)
5. Add auto-sync scheduling (APScheduler)
```

---

## 19. Configuration Reference

### NSE Trading Calendar Notes

- NSE is open Monday–Friday (except public holidays)
- Trading hours: 9:15 AM – 3:30 PM IST
- EOD data typically available by 5:00 PM IST
- No trading on: Republic Day (Jan 26), Holi, Good Friday, Independence Day (Aug 15), Diwali (Laxmi Pujan + Balipratipada), Christmas (Dec 25), and ~10-12 other holidays per year
- The list changes yearly; download from NSE or hardcode known holidays

### Date Format Cheat Sheet

| Context | Format | Example |
|---------|--------|---------|
| Bhavcopy URL | `YYYYMMDD` | `20250605` |
| Index CSV URL | `DDMMYYYY` | `05062025` |
| Corporate Actions API | `DD-MM-YYYY` | `05-06-2025` |
| Database storage | `YYYY-MM-DD` | `2025-06-05` |
| Bhavcopy CSV `TradDt` column | `YYYY-MM-DD` | `2025-06-05` |
| EQUITY_L.csv `DATE OF LISTING` | `DD-MMM-YYYY` | `29-NOV-1995` |

### DuckDB Connection String Patterns

```python
# File-based (desktop)
"duckdb:///data/market.db"           # Relative path
"duckdb:///D:/data/market.db"        # Absolute path (Windows)

# In-memory (testing)
"duckdb:///:memory:"

# PostgreSQL (cloud migration)
"postgresql+psycopg2://user:pass@localhost:5432/nse_eod"
"postgresql+asyncpg://user:pass@host:5432/nse_eod"  # Async
```

---

## 20. Glossary

| Term | Definition |
|------|-----------|
| **Bhavcopy** | Daily security-wise price data file published by NSE after market close |
| **UDiFF** | Unified Distilled File Format — NSE's new standardized CSV format (since July 2024) |
| **EQ Series** | Regular equity shares — normal trading with intraday allowed |
| **BE Series** | Trade-to-Trade segment — must take delivery, no intraday (Books Entry only) |
| **OHLCV** | Open, High, Low, Close, Volume — the 5 core price/volume data points |
| **Ex-Date** | The date from which a corporate action takes effect. Buyer on this date does NOT get the benefit |
| **Adjustment Factor** | Multiplier used to retroactively adjust historical prices for splits/bonus |
| **Issued Size** | Total number of shares issued by a company (used for market cap calculation) |
| **Face Value** | Nominal value of a share as stated in the company's charter (e.g., ₹10, ₹2, ₹1) |
| **ISIN** | International Securities Identification Number — 12-character unique identifier for a security |
| **SMA** | Simple Moving Average — arithmetic mean of last N closing prices |
| **EMA** | Exponential Moving Average — weighted average giving more weight to recent prices |
| **RSI** | Relative Strength Index — momentum oscillator (0-100), >70 overbought, <30 oversold |
| **MACD** | Moving Average Convergence Divergence — trend-following momentum indicator |
| **Bollinger Bands** | Volatility bands at ±2 standard deviations from SMA(20) |
| **ATR** | Average True Range — volatility indicator based on high-low range |
| **OBV** | On-Balance Volume — cumulative volume indicator (up days add, down days subtract) |
| **VWAP** | Volume Weighted Average Price — average price weighted by volume |

---

> [!TIP]
> **For any LLM implementing this:** Start with Phase 1 (skeleton + DB) and work through each phase sequentially. Each phase is self-contained and testable. The NSE client (Phase 2) is the most critical piece — get session management right first, then everything else is data plumbing.

> [!TIP]
> **Quick validation checkpoints:**
> 1. After Phase 2: Can you successfully download EQUITY_L.csv and parse it?
> 2. After Phase 3: Can you download one day's bhavcopy and see ~2000 stock rows?
> 3. After Phase 4: Does Reliance's adjusted price history look correct?
> 4. After Phase 5: Does Reliance's market cap match NSE website (approximately)?
> 5. After Phase 6: Does RSI(14) for NIFTY 50 stocks match TradingView?
> 6. After Phase 9: Does the app open as a native desktop window with a sidebar?
