import asyncio
import time
import io
import zipfile
from datetime import datetime, timedelta
from typing import Optional

import httpx
import pandas as pd
from loguru import logger

from config.constants import NSE_HEADERS, NSE_BASE_URL
from config.settings import settings


class NSEClient:
    """HTTP client for NSE India with automatic session management and rate limiting."""

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

    async def _get(
        self,
        url: str,
        params: dict = None,
        requires_cookies: bool = False,
        retries: int = None,
    ) -> httpx.Response:
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

        await self._ensure_client()

        for attempt in range(retries + 1):
            try:
                if requires_cookies:
                    await self._refresh_cookies()

                await self._rate_limit()
                response = await self._client.get(url, params=params)

                # If session is expired or blocked, refresh cookies and retry
                if response.status_code in (401, 403) and requires_cookies:
                    logger.warning(f"Got {response.status_code}, refreshing cookies (attempt {attempt + 1})")
                    await self._refresh_cookies(force=True)
                    continue

                response.raise_for_status()
                return response

            except httpx.HTTPStatusError as e:
                # If we get a 404, do not retry (indicates holiday or non-existent page/bhavcopy)
                if e.response.status_code == 404:
                    logger.warning(f"404 Not Found: {url}")
                    raise
                
                if attempt < retries:
                    wait = (attempt + 1) * settings.nse_request_delay_seconds
                    logger.warning(f"Request failed status error ({e.response.status_code}), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Request failed after {retries + 1} attempts: {url}")
                    raise
            except httpx.HTTPError as e:
                if attempt < retries:
                    wait = (attempt + 1) * settings.nse_request_delay_seconds
                    logger.warning(f"Request failed connection error ({e}), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Request failed after {retries + 1} attempts: {url}")
                    raise

        raise httpx.HTTPError("Request failed after max retries")

    # ========== HIGH-LEVEL METHODS ==========

    async def download_bhavcopy_csv(self, trade_date: str) -> pd.DataFrame:
        """
        Download and parse UDiFF bhavcopy for a given date.

        Args:
            trade_date: Date string in YYYYMMDD format (e.g., '20250605')

        Returns:
            DataFrame with all equity trades for that day
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
        # symbolchange.csv has no header row
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
            CORPORATE_ACTIONS_URL, params=params, requires_cookies=True
        )
        return response.json()

    async def fetch_stock_quote(self, symbol: str, retries: int = 0) -> dict:
        """
        Fetch stock quote.

        Args:
            symbol: Stock symbol (e.g., 'RELIANCE')
            retries: Number of request retries (default: 0)
        """
        from config.constants import STOCK_QUOTE_URL
        response = await self._get(
            STOCK_QUOTE_URL, params={"symbol": symbol}, requires_cookies=True, retries=retries
        )
        return response.json()

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
