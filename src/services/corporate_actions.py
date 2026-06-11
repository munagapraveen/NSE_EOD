import re
import math
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session
from loguru import logger

from src.models import Security, CorporateAction
from src.services.nse_client import NSEClient

# SPLIT regex
# Match: "Sub-Division/Stock Split From Rs.10/- Per Share To Re.1/- Per Share", "Rs. 10 to Rs. 2", "FV Split Rs.10 to Rs.5"
# Match format: (digits) followed by (to/into) followed by (digits)
SPLIT_PATTERN = re.compile(
    r'(?:from|of|split)?\s*(?:Rs?e?\.?\s*)?(\d+(?:\.\d+)?)\s*/?-?\s*'
    r'(?:per\s+share\s+)?'
    r'(?:to|into)\s*(?:Rs?e?\.?\s*)?(\d+(?:\.\d+)?)',
    re.IGNORECASE
)

# BONUS regex
# Match: "Bonus 1:1", "Bonus Issue In Ratio Of 1:1", "Bonus issue of 1:2"
BONUS_PATTERN = re.compile(
    r'bonus\s*(?:issue\s*)?(?:(?:in\s*)?(?:the\s*)?ratio\s*of\s*)?'
    r'(\d+)\s*:\s*(\d+)',
    re.IGNORECASE
)


def parse_action_date(date_str: str) -> date:
    """Helper to parse dates in YYYY-MM-DD or DD-MMM-YYYY format."""
    if not date_str or not isinstance(date_str, str):
        raise ValueError("Invalid date string")
    
    date_str_clean = date_str.strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str_clean, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Could not parse date: {date_str}")


def parse_corporate_action_text(purpose: str, subject: str = "") -> dict:
    """
    Parses purpose and subject strings to identify splits or bonuses
    and extract ratios / face values.
    
    Returns:
        A dict with parsed details or None if it's not a split or bonus.
    """
    combined_text = f"{purpose or ''} {subject or ''}".strip()
    combined_text_lower = combined_text.lower()
    
    # 1. Check for Stock Split / Sub-Division
    if "split" in combined_text_lower or "sub-division" in combined_text_lower or "sub division" in combined_text_lower:
        match = SPLIT_PATTERN.search(combined_text)
        if match:
            try:
                old_fv = float(match.group(1))
                new_fv = float(match.group(2))
                if old_fv > 0 and new_fv > 0:
                    factor = old_fv / new_fv
                    return {
                        "action_type": "SPLIT",
                        "old_face_value": old_fv,
                        "new_face_value": new_fv,
                        "bonus_ratio_new": None,
                        "bonus_ratio_existing": None,
                        "adjustment_factor": factor
                    }
            except Exception as e:
                logger.warning(f"Failed parsing split details from text '{combined_text}': {e}")
                
    # 2. Check for Bonus Issue
    if "bonus" in combined_text_lower:
        match = BONUS_PATTERN.search(combined_text)
        if match:
            try:
                new_ratio = int(match.group(1))
                existing_ratio = int(match.group(2))
                if new_ratio > 0 and existing_ratio > 0:
                    factor = (existing_ratio + new_ratio) / existing_ratio
                    return {
                        "action_type": "BONUS",
                        "old_face_value": None,
                        "new_face_value": None,
                        "bonus_ratio_new": new_ratio,
                        "bonus_ratio_existing": existing_ratio,
                        "adjustment_factor": factor
                    }
            except Exception as e:
                logger.warning(f"Failed parsing bonus details from text '{combined_text}': {e}")

    return None


class CorporateActionsService:
    """Service to fetch, parse, and store Corporate Actions from NSE."""

    def __init__(self, client: NSEClient):
        self.client = client

    async def sync_corporate_actions(
        self, session: Session, from_date: date, to_date: date, symbol: str = None
    ) -> int:
        """
        Fetch corporate actions from NSE for a date range, parse splits/bonuses,
        and store them in the database.
        
        Args:
            session: SQLAlchemy DB Session
            from_date: Start date of search range
            to_date: End date of search range
            symbol: Optional ticker symbol to restrict search
            
        Returns:
            Number of new corporate action records created.
        """
        from_str = from_date.strftime("%d-%m-%Y")
        to_str = to_date.strftime("%d-%m-%Y")
        
        logger.info(f"Fetching corporate actions from {from_str} to {to_str}...")
        try:
            raw_actions = await self.client.fetch_corporate_actions(from_str, to_str, symbol)
        except Exception as e:
            logger.error(f"Failed to fetch corporate actions: {e}")
            raise e

        if not raw_actions or not isinstance(raw_actions, list):
            logger.info("No corporate actions found in response.")
            return 0

        logger.info(f"Retrieved {len(raw_actions)} raw corporate actions from NSE API.")
        
        new_records_count = 0
        
        # Pre-load security ID mappings by symbol (global lookup to prevent duplicate violations)
        unique_symbols = {action.get("symbol").strip() for action in raw_actions if action.get("symbol")}
        if not unique_symbols:
            return 0
            
        securities = session.execute(
            select(Security).where(Security.symbol.in_(list(unique_symbols)))
        ).scalars().all()
        symbol_to_id = {sec.symbol: sec.id for sec in securities}

        for item in raw_actions:
            sym = item.get("symbol", "").strip()
            purpose = item.get("purpose", "")
            subject = item.get("subject", "")
            ex_date_str = item.get("exDate")
            record_date_str = item.get("recDate")
            desc = item.get("desc", purpose or subject)

            if not sym or not ex_date_str:
                continue

            # Lookup security ID
            sec_id = symbol_to_id.get(sym)
            if not sec_id:
                # Security is not in our database, skip it (we only store actions for securities we track)
                continue

            # Parse ex-date
            try:
                ex_date = parse_action_date(ex_date_str)
            except Exception as e:
                logger.warning(f"Skipping action for {sym} due to unparseable exDate '{ex_date_str}': {e}")
                continue

            # Parse record date
            record_date = None
            if record_date_str:
                try:
                    record_date = parse_action_date(record_date_str)
                except:
                    pass

            # Parse split/bonus values
            parsed_details = parse_corporate_action_text(purpose, subject)
            if not parsed_details:
                continue

            # Check if this action already exists in the database
            existing = session.execute(
                select(CorporateAction)
                .where(CorporateAction.security_id == sec_id)
                .where(CorporateAction.ex_date == ex_date)
                .where(CorporateAction.action_type == parsed_details["action_type"])
            ).scalar_one_or_none()

            if existing:
                continue

            # Create new record
            action_record = CorporateAction(
                security_id=sec_id,
                action_type=parsed_details["action_type"],
                ex_date=ex_date,
                record_date=record_date,
                description=desc,
                old_face_value=parsed_details["old_face_value"],
                new_face_value=parsed_details["new_face_value"],
                bonus_ratio_new=parsed_details["bonus_ratio_new"],
                bonus_ratio_existing=parsed_details["bonus_ratio_existing"],
                adjustment_factor=parsed_details["adjustment_factor"],
                is_processed=False
            )
            session.add(action_record)
            new_records_count += 1

        if new_records_count > 0:
            session.commit()
            logger.info(f"Successfully sync'd and saved {new_records_count} new corporate actions.")
        else:
            logger.info("No new corporate actions to save.")

        return new_records_count
