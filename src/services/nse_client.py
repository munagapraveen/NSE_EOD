import asyncio
import io
import zipfile
from datetime import datetime
from typing import Optional

import pandas as pd
from loguru import logger
from curl_cffi.requests import AsyncSession

from config.constants import NSE_HEADERS, NSE_API_ACCEPT
from config.settings import settings


class NSEClient:
    """HTTP client for NSE India.

    Architecture:
    - **Cookie acquisition**: Real Chrome browser (via Selenium) visits NSE homepage and
      solves Akamai's JavaScript challenge, setting valid session cookies.
    - **API calls**: curl_cffi with Chrome TLS fingerprint impersonation uses those cookies
      to make lightweight, fast API requests.

    This two-step approach is necessary because NSE's Akamai Bot Manager uses
    JavaScript-level challenges that pure HTTP libraries cannot solve.
    """

    IMPERSONATE_BROWSER = "chrome120"

    def __init__(self):
        self._session: Optional[AsyncSession] = None
        self._session_lock = asyncio.Lock()
        self._last_session_refresh: Optional[datetime] = None

    # ──────────────────────────────────────────────────────────────────────────
    #  Session & Cookie Management
    # ──────────────────────────────────────────────────────────────────────────

    async def _ensure_session(self, force_cookie_refresh: bool = False):
        """
        Ensure curl_cffi session exists with fresh cookies.
        Creates or re-creates the session and visits NSE homepage to establish cookies.
        """
        async with self._session_lock:
            # Prevent thundering herd of cookie refreshes if updated within 15 seconds
            now = datetime.now()
            is_recent_refresh = (
                self._last_session_refresh is not None 
                and (now - self._last_session_refresh).total_seconds() < 15.0
            )
            
            if force_cookie_refresh and is_recent_refresh:
                logger.info("NSEClient: Ignoring redundant cookie refresh request (refreshed recently).")
                force_cookie_refresh = False

            has_no_cookies = False
            if self._session is not None:
                try:
                    if not self._session.cookies or len(self._session.cookies) == 0:
                        has_no_cookies = True
                except Exception:
                    has_no_cookies = True
    
            if self._session is None or force_cookie_refresh or has_no_cookies:
                # Close old session if refreshing
                if self._session is not None:
                    await self._session.close()
    
                self._session = AsyncSession(
                    impersonate=self.IMPERSONATE_BROWSER,
                    timeout=30,
                    allow_redirects=True,
                    verify=True,
                )
                # Visit homepage to establish cookies
                await self._session.get("https://www.nseindia.com/")
                self._last_session_refresh = datetime.now()
                logger.debug(
                    f"NSEClient: Session {'refreshed' if force_cookie_refresh else 'created'} "
                    f"by visiting homepage directly."
                )

    async def _rate_limit(self):
        """Minimum delay between sequential API calls."""
        delay = settings.nse_request_delay_seconds
        await asyncio.sleep(delay)

    # ──────────────────────────────────────────────────────────────────────────
    #  CRITICAL ARCHITECTURAL WARNING - DO NOT MODIFY OR REVERT THE FOLLOWING
    # ──────────────────────────────────────────────────────────────────────────
    # FINDINGS & ALERTS (2026-06-29):
    # 1. DO NOT REVERT TO A SINGLE PERSISTENT SESSION FOR PARALLEL QUOTES:
    #    Reusing a single AsyncSession for concurrent requests (e.g., in fetch_all_quotes_parallel)
    #    forces curl_cffi to multiplex requests over a single TCP connection using HTTP/2.
    #    NSE India's Akamai Bot Manager detects this and immediately terminates the streams with
    #    "curl: (92) HTTP/2 stream was not closed cleanly: INTERNAL_ERROR (err 2)".
    #    To avoid this, GetQuoteApi requests MUST use `use_new_session=True` to spawn a new session
    #    (and thus a new TCP connection/TLS handshake) per request, just like the working Codex script.
    #
    # 2. DO NOT REMOVE `NSE_HEADERS` FROM DEFAULT REQUESTS:
    #    Archive and CSV downloads (e.g. bhavcopy, index close) do not specify custom headers.
    #    If NSE_HEADERS are not merged by default in `_get`, these requests are sent without
    #    a browser User-Agent and are blocked instantly by Akamai (403 Forbidden / Connection reset).
    # ──────────────────────────────────────────────────────────────────────────

    async def _get(
        self,
        url: str,
        params: dict = None,
        headers: dict = None,
        requires_cookies: bool = False,
        retries: int = None,
        accept_json: bool = False,
        bypass_rate_limit: bool = False,
        use_new_session: bool = False,
    ):
        """
        Make a GET request with rate limiting and 403 retry via cookie refresh.

        Args:
            url: Full URL to fetch
            params: Query parameters
            headers: Custom HTTP headers to merge
            requires_cookies: If True, use Chrome-acquired NSE session cookies
            retries: Number of retries (default: settings.nse_max_retries)
            accept_json: If True, override Accept header for API JSON calls
            bypass_rate_limit: If True, bypass the rate limit delay
            use_new_session: If True, use a new AsyncSession for this request to avoid HTTP/2 multiplexing

        Returns:
            curl_cffi Response object
        """
        if retries is None:
            retries = settings.nse_max_retries

        # Ensure we always use the proper NSE headers by default (CRITICAL: Fixes broken downloads)
        request_headers = NSE_HEADERS.copy()
        if accept_json:
            request_headers["Accept"] = NSE_API_ACCEPT
        if headers:
            request_headers.update(headers)

        if requires_cookies:
            await self._ensure_session()
        elif not use_new_session:
            # For non-cookie requests (archive downloads), a plain session is fine
            if self._session is None:
                self._session = AsyncSession(
                    impersonate=self.IMPERSONATE_BROWSER,
                    timeout=30,
                )

        for attempt in range(retries + 1):
            try:
                if not bypass_rate_limit:
                    await self._rate_limit()

                if use_new_session:
                    # CRITICAL: Creating a new AsyncSession per request prevents HTTP/2 stream resets
                    async with AsyncSession(impersonate=self.IMPERSONATE_BROWSER, timeout=30) as session:
                        response = await session.get(url, params=params, headers=request_headers)
                else:
                    response = await self._session.get(url, params=params, headers=request_headers)

                if response.status_code in (401, 403) and requires_cookies and attempt < retries:
                    logger.warning(
                        f"Got {response.status_code} on attempt {attempt + 1}/{retries + 1} "
                        f"— refreshing Chrome cookies..."
                    )
                    backoff = (attempt + 1) * 5.0
                    await asyncio.sleep(backoff)
                    await self._ensure_session(force_cookie_refresh=True)
                    continue

                if response.status_code == 404:
                    raise HttpNotFoundError(url)

                if response.status_code >= 400:
                    raise HttpStatusError(response.status_code, url)

                return response

            except (HttpNotFoundError, HttpStatusError):
                raise  # Never retry 404; propagate HTTP errors after exhausting retries

            except Exception as e:
                if attempt < retries:
                    wait = (attempt + 1) * settings.nse_request_delay_seconds
                    logger.warning(f"Request failed ({e}), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Request failed after {retries + 1} attempts: {url}")
                    raise

    # ──────────────────────────────────────────────────────────────────────────
    #  High-Level Download Methods
    # ──────────────────────────────────────────────────────────────────────────

    async def download_bhavcopy_csv(self, trade_date: str) -> pd.DataFrame:
        """
        Download and parse CM UDiFF bhavcopy for a given date.

        Args:
            trade_date: Date string in YYYYMMDD format (e.g., '20250605')

        Returns:
            DataFrame with all equity trades for that day
        """
        from config.constants import BHAVCOPY_URL
        url = BHAVCOPY_URL.format(date=trade_date)
        response = await self._get(url, requires_cookies=False)

        zip_buffer = io.BytesIO(response.content)
        if not zipfile.is_zipfile(zip_buffer):
            raise ValueError(f"Downloaded content for {trade_date} is not a valid zip file. Length: {len(response.content)} bytes")

        with zipfile.ZipFile(zip_buffer) as zf:
            names = zf.namelist()
            if not names:
                raise ValueError(f"Zip file for {trade_date} contains no files.")
            csv_filename = names[0]
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
        return pd.read_csv(io.StringIO(response.text))

    async def download_equity_list(self) -> pd.DataFrame:
        """Download EQUITY_L.csv (stock master list)."""
        from config.constants import EQUITY_LIST_URL
        response = await self._get(EQUITY_LIST_URL, requires_cookies=False)
        df = pd.read_csv(io.StringIO(response.text))
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
        df = pd.read_csv(
            io.StringIO(response.text),
            header=None,
            names=["company_name", "old_symbol", "new_symbol", "effective_date"]
        )
        for col in df.columns:
            df[col] = df[col].astype(str).str.strip()
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
            CORPORATE_ACTIONS_URL, params=params,
            headers={"Referer": "https://www.nseindia.com/market-data/corporate-actions"},
            requires_cookies=True, accept_json=True
        )
        return response.json()

    async def fetch_stock_quote(self, symbol: str, retries: int = None) -> dict:
        """
        Fetch stock quote (requires valid NSE session cookies).

        The quote-equity API returns issuedSize (outstanding shares), last price,
        total market cap, etc.

        Args:
            symbol: Stock symbol (e.g., 'RELIANCE')
            retries: Number of request retries

        Returns:
            Full quote dict from NSE API
        """
        from config.constants import STOCK_QUOTE_URL
        response = await self._get(
            STOCK_QUOTE_URL,
            params={"symbol": symbol},
            requires_cookies=True,
            retries=retries,
            accept_json=True
        )
        return response.json()

    async def fetch_get_quote_api(self, symbol: str, series: str = "EQ", bypass_rate_limit: bool = False) -> dict:
        """
        Fetch stock data from NSE NextApi GetQuoteApi for a single symbol.

        Returns equityResponse[0] — a dict with keys:
            metaData   → symbol, companyName, isinCode, series, closePrice
            tradeInfo  → issuedSize, totalMarketCap, ffmc, faceValue, lastPrice
            secInfo    → sector, industryInfo, macro, index, indexList, listingDate
            orderBook  → lastPrice (post-market)
            lastUpdateTime

        Does not require cookies when using impersonated browser session and correct Referer.

        Args:
            symbol: NSE stock symbol (e.g., 'RELIANCE')
            series: NSE series code (default 'EQ')
            bypass_rate_limit: If True, bypass the rate limit delay

        Returns:
            equityResponse[0] dict, or raises RuntimeError if response is empty.
        """
        from config.constants import GET_QUOTE_API_URL
        response = await self._get(
            GET_QUOTE_API_URL,
            params={
                "functionName": "getSymbolData",
                "marketType": "N",
                "series": series,
                "symbol": symbol,
            },
            requires_cookies=False,
            accept_json=True,
            headers={"Referer": f"https://www.nseindia.com/get-quote/equity/{symbol}"},
            bypass_rate_limit=bypass_rate_limit,
            use_new_session=True,
        )
        data = response.json()
        equity_response = data.get("equityResponse") or []
        if not equity_response:
            raise RuntimeError(f"Empty equityResponse for {symbol}/{series}")
            
        res_data = equity_response[0]
        # Raise error if metadata is missing (indicating stock is not active in this series)
        if res_data.get("metaData") is None:
            raise RuntimeError(f"Empty metadata for {symbol}/{series}")
                
        return res_data

    async def fetch_all_quotes_parallel(
        self,
        stocks: list[tuple[str, str]],
        workers: int = 8,
        bypass_rate_limit: bool = True,
    ) -> dict[str, dict | None]:
        """
        Fetch GetQuoteApi for multiple (symbol, series) pairs concurrently.

        Uses a two-pass approach:
        1. Fetches all stocks using their requested series (typically EQ).
        2. Collects any stocks that failed (metadata is None) and retries them with BE series in batch mode.

        Args:
            stocks:  List of (symbol, series) tuples, e.g. [('RELIANCE', 'EQ'), ...]
            workers: Max concurrent requests (default 8)
            bypass_rate_limit: If True, bypass the rate limit delay (recommended for speed)

        Returns:
            Dict keyed by symbol → equityResponse[0] dict, or None on failure.
        """
        semaphore = asyncio.Semaphore(workers)
        results: dict[str, dict | None] = {}

        async def _fetch_one(symbol: str, series: str):
            async with semaphore:
                try:
                    return symbol, await self.fetch_get_quote_api(symbol, series, bypass_rate_limit=bypass_rate_limit)
                except Exception as exc:
                    logger.warning(f"[GetQuoteApi] Failed for {symbol}/{series}: {exc}")
                    return symbol, None

        # Pass 1: Fetch all stocks with their requested series
        tasks = [_fetch_one(sym, ser) for sym, ser in stocks]
        for coro in asyncio.as_completed(tasks):
            sym, data = await coro
            results[sym] = data

        # Pass 2: Retry failed EQ stocks using the BE series in batch mode
        failed_eq_stocks = [
            (sym, "BE") for sym, ser in stocks 
            if results.get(sym) is None and ser == "EQ"
        ]
        
        if failed_eq_stocks:
            logger.info(f"Retrying {len(failed_eq_stocks)} failed EQ stocks in BE series (batch mode)...")
            be_results = await self.fetch_all_quotes_parallel(
                failed_eq_stocks, 
                workers=workers, 
                bypass_rate_limit=bypass_rate_limit
            )
            results.update(be_results)

        return results

    async def close(self):
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None


# ──────────────────────────────────────────────────────────────────────────────
#  Custom Exception Classes
# ──────────────────────────────────────────────────────────────────────────────

class HttpNotFoundError(Exception):
    """Raised when a 404 Not Found response is received."""
    def __init__(self, url: str):
        self.url = url
        self.status_code = 404
        super().__init__(f"404 Not Found: {url}")


class HttpStatusError(Exception):
    """Raised when an HTTP error status (>= 400) is received."""
    def __init__(self, status_code: int, url: str):
        self.status_code = status_code
        self.url = url
        super().__init__(f"HTTP {status_code} for {url}")
