import httpx
import re
import asyncio
from bs4 import BeautifulSoup
from loguru import logger
from typing import Optional

from datetime import date
from config.constants import BSE_ANCHOR_DATE, BSE_ANCHOR_QTRID

def _parse_quarter_date(text: str) -> Optional[date]:
    """
    Parses a quarter ended/ending date string from BSE HTML into a Python date.
    Examples: 'Quarter ending :March 2026', 'Quarter ended June 30, 2025'
    """
    if not text:
        return None
    text = re.sub(r'\s+', ' ', text)
    # Match month, optional day, optional comma, and 4-digit year
    match = re.search(r'(March|June|September|December|Dec|Mar|Jun|Sep|Sept)\s*(?:\d{1,2})?\s*,?\s*(20\d{2})', text, re.IGNORECASE)
    if not match:
        return None
    
    month_str = match.group(1).lower()
    year = int(match.group(2))
    
    if "mar" in month_str:
        return date(year, 3, 31)
    elif "jun" in month_str:
        return date(year, 6, 30)
    elif "sep" in month_str:
        return date(year, 9, 30)
    elif "dec" in month_str:
        return date(year, 12, 31)
    return None


class BSEClient:
    """Async client for fetching outstanding shares and metadata from BSE India website."""

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.bseindia.com/",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.bseindia.com"
        }
        self.client = httpx.AsyncClient(headers=self.headers, follow_redirects=True, timeout=15.0)
        self._semaphore = asyncio.Semaphore(3)

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def _get_with_retry(
        self,
        url: str,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        backoff_factor: float = 2.0
    ) -> httpx.Response:
        """
        Helper method to perform GET requests with exponential backoff retries for transient errors.
        """
        import random
        for attempt in range(max_retries):
            try:
                async with self._semaphore:
                    # Respect semaphore and small rate-limiting delay
                    await asyncio.sleep(0.2)
                    r = await self.client.get(url)
                r.raise_for_status()
                return r
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                is_transient = True
                if isinstance(e, httpx.HTTPStatusError):
                    status_code = e.response.status_code
                    # Do not retry on client errors that aren't rate limits (429)
                    if status_code in [400, 401, 403, 404]:
                        is_transient = False
                
                if not is_transient or attempt == max_retries - 1:
                    raise e
                
                # Exponential backoff with a bit of jitter
                delay = (initial_delay * (backoff_factor ** attempt)) + random.uniform(0, 0.5)
                logger.warning(
                    f"Transient HTTP error fetching URL {url}: {e}. "
                    f"Retrying in {delay:.2f}s (attempt {attempt + 1}/{max_retries})..."
                )
                await asyncio.sleep(delay)


    async def lookup_scripcode_by_isin(self, isin: str) -> Optional[str]:
        """
        Lookup BSE scripcode using ISIN code.
        Returns 6-digit scripcode if found, else None.
        """
        if not isin or not isinstance(isin, str) or len(isin.strip()) != 12:
            return None
        
        isin = isin.strip()
        url = f"https://api.bseindia.com/BseIndiaAPI/api/PeerSmartSearch/w?Type=SS&text={isin}"
        
        try:
            r = await self._get_with_retry(url)
            
            # Extract 6-digit scripcode using regex
            match = re.search(r'(\d{6})</a>', r.text)
            if match:
                return match.group(1)
            
            match2 = re.search(r"liclick\('(\d{6})'", r.text)
            if match2:
                return match2.group(1)
                
            logger.warning(f"No scripcode matched in BSE search response for ISIN {isin}")
            return None
        except Exception as e:
            logger.error(f"Failed to lookup BSE scripcode for ISIN {isin}: {e}")
            return None

    async def fetch_outstanding_shares(self, scrip_code: str, qtrid: str = "") -> tuple[Optional[int], Optional[date]]:
        """
        Fetch outstanding shares from the latest BSE shareholding pattern summary.
        Returns a tuple of (shares, quarter_date).
        """
        if not scrip_code:
            return None, None
            
        url = f"https://api.bseindia.com/BseIndiaAPI/api/shpSecSummery_New/w?qtrid={qtrid}&scripcode={scrip_code}"
        
        try:
            r = await self._get_with_retry(url)
            
            data = r.json()
            html = data.get("Data", "")
            
            # If default empty qtrid returns empty HTML, try fallback quarter IDs
            if not html and not qtrid:
                from datetime import date as dt
                today = dt.today()
                
                months_elapsed = (today.year - BSE_ANCHOR_DATE.year) * 12 + (today.month - BSE_ANCHOR_DATE.month)
                quarters_elapsed = months_elapsed // 3
                current_qtrid = BSE_ANCHOR_QTRID + quarters_elapsed
                
                fallback_qtrids = [str(current_qtrid - i) for i in range(4)]
                logger.info(f"Default shareholding pattern for scrip {scrip_code} is empty. Trying fallbacks {fallback_qtrids}...")
                for f_qtrid in fallback_qtrids:
                    fallback_url = f"https://api.bseindia.com/BseIndiaAPI/api/shpSecSummery_New/w?qtrid={f_qtrid}&scripcode={scrip_code}"
                    try:
                        fr = await self._get_with_retry(fallback_url)
                        fdata = fr.json()
                        fhtml = fdata.get("Data", "")
                        if fhtml:
                            html = fhtml
                            logger.info(f"Successfully retrieved shareholding pattern for scrip {scrip_code} using qtrid={f_qtrid}")
                            break
                    except Exception as fe:
                        logger.debug(f"Failed fallback fetch for scrip {scrip_code} (qtrid={f_qtrid}): {fe}")
                        
            if not html:
                logger.warning(f"BSE shareholding pattern response for scrip {scrip_code} remains empty after fallbacks")
                return None, None
                
            soup = BeautifulSoup(html, "html.parser")
            qtr_date = _parse_quarter_date(soup.get_text())
            rows = soup.find_all("tr")
            
            # 1. Find the header row containing "total no. shares held" to get column index dynamically
            header_cells = None
            for row in rows:
                if row.find("table") is not None:
                    continue
                cells = [td.get_text(strip=True).lower() for td in row.find_all(["td", "th"])]
                if "total no. shares held" in cells:
                    header_cells = cells
                    break
                    
            if not header_cells:
                logger.warning(f"Header containing 'Total no. shares held' not found for scrip {scrip_code}")
                return None, qtr_date
                
            col_idx = header_cells.index("total no. shares held")
            
            # 2. Find the td containing exactly "Grand Total"
            grand_total_td = soup.find(lambda tag: tag.name == 'td' and tag.get_text(strip=True).lower() == 'grand total')
            if not grand_total_td:
                logger.warning(f"Grand Total cell not found in shareholding pattern for scrip {scrip_code}")
                return None, qtr_date
                
            # Get sibling td elements in the same row
            siblings = grand_total_td.find_next_siblings("td")
            sibling_vals = [td.get_text(strip=True) for td in siblings]
            
            # The first column "Category of shareholder" is grand_total_td, so sibling index is col_idx - 1
            target_sibling_idx = col_idx - 1
            if target_sibling_idx >= len(sibling_vals):
                logger.warning(f"Target column index {target_sibling_idx} exceeds sibling count {len(sibling_vals)} for scrip {scrip_code}")
                return None, qtr_date
                
            total_shares_str = sibling_vals[target_sibling_idx]
            
            # Remove commas and convert to integer
            shares = int(re.sub(r'[\s,]', '', total_shares_str))
            if shares <= 0:
                logger.warning(f"Parsed invalid non-positive outstanding shares ({shares}) for scrip {scrip_code}")
                return None, qtr_date
                
            return shares, qtr_date
        except Exception as e:
            logger.error(f"Failed to fetch/parse outstanding shares for scrip {scrip_code}: {e}")
            return None, None
