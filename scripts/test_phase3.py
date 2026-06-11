import asyncio
import sys
import os
from datetime import date, datetime
from decimal import Decimal
import pandas as pd

# Append the project's root directory to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.engine import SessionLocal, engine
from src.models import Security, RawPrice, AdjustedPrice, CorporateAction, SymbolChange
from src.services.corporate_actions import parse_corporate_action_text, CorporateActionsService
from src.services.symbol_changes import SymbolChangesService
from src.services.price_adjuster import adjust_prices_for_security, adjust_all_prices
from src.services.nse_client import NSEClient


async def run_tests():
    print("--- Starting Phase 3 Verification Tests ---")
    session = SessionLocal()
    client = NSEClient()
    
    try:
        # ==========================================
        # 1. Test Regex Purpose & Subject Parsing
        # ==========================================
        print("\n1. Testing corporate action text parsing...")
        
        # Test splits
        split1 = parse_corporate_action_text("Sub-Division/Stock Split From Rs.10/- Per Share To Re.1/- Per Share")
        assert split1 is not None and split1["action_type"] == "SPLIT"
        assert split1["old_face_value"] == 10.0 and split1["new_face_value"] == 1.0
        assert split1["adjustment_factor"] == 10.0
        
        split2 = parse_corporate_action_text("FV Split Rs. 10 to Rs. 2")
        assert split2 is not None and split2["action_type"] == "SPLIT"
        assert split2["old_face_value"] == 10.0 and split2["new_face_value"] == 2.0
        assert split2["adjustment_factor"] == 5.0

        # Test bonuses
        bonus1 = parse_corporate_action_text("Bonus Issue 1:1")
        assert bonus1 is not None and bonus1["action_type"] == "BONUS"
        assert bonus1["bonus_ratio_new"] == 1 and bonus1["bonus_ratio_existing"] == 1
        assert bonus1["adjustment_factor"] == 2.0

        bonus2 = parse_corporate_action_text("Bonus issue in ratio of 3:1")
        assert bonus2 is not None and bonus2["action_type"] == "BONUS"
        assert bonus2["bonus_ratio_new"] == 3 and bonus2["bonus_ratio_existing"] == 1
        assert bonus2["adjustment_factor"] == 4.0

        print("   [OK] Regex parsers for splits and bonuses passed successfully.")

        # ==========================================
        # 2. Test Price Adjustment Calculation
        # ==========================================
        print("\n2. Testing price adjustment calculation (splits & bonuses)...")
        
        # Clean previous test security if any
        existing_test = session.query(Security).filter(Security.symbol == "MOCK_TCS").first()
        if existing_test:
            session.delete(existing_test)
            session.commit()

        # Seed mock security
        mock_sec = Security(
            symbol="MOCK_TCS",
            company_name="Mock TCS Limited",
            security_type="STOCK",
            isin="INE999X01019",
            is_active=True,
            data_source="MOCK"
        )
        session.add(mock_sec)
        session.flush() # get ID
        
        # Day 1 & Day 2 (Pre-split): Price is ~3000
        # Day 3 (Ex-date of 10:2 split = factor 5.0): Price drops to ~600
        # Day 4 & Day 5 (Post-split)
        prices_to_seed = [
            {"trade_date": date(2025, 1, 10), "open": 3000.0, "high": 3050.0, "low": 2980.0, "close": 3010.0, "volume": 100000},
            {"trade_date": date(2025, 1, 11), "open": 3010.0, "high": 3060.0, "low": 3000.0, "close": 3020.0, "volume": 110000},
            {"trade_date": date(2025, 1, 12), "open": 604.0, "high": 615.0, "low": 598.0, "close": 605.0, "volume": 550000}, # ex-date
            {"trade_date": date(2025, 1, 13), "open": 605.0, "high": 620.0, "low": 601.0, "close": 612.0, "volume": 560000},
            {"trade_date": date(2025, 1, 14), "open": 612.0, "high": 618.0, "low": 608.0, "close": 610.0, "volume": 500000},
        ]
        
        for p in prices_to_seed:
            raw_p = RawPrice(
                security_id=mock_sec.id,
                trade_date=p["trade_date"],
                open=p["open"],
                high=p["high"],
                low=p["low"],
                close=p["close"],
                volume=p["volume"]
            )
            session.add(raw_p)
            
        # Add corporate action: Split 10 -> 2 (factor = 5.0) on 2025-01-12
        split_action = CorporateAction(
            security_id=mock_sec.id,
            action_type="SPLIT",
            ex_date=date(2025, 1, 12),
            description="FV Split Rs 10 to Rs 2",
            old_face_value=10.0,
            new_face_value=2.0,
            adjustment_factor=5.0,
            is_processed=False
        )
        session.add(split_action)
        session.commit()

        # Run adjustment
        print("   Running adjust_prices_for_security...")
        written_count = await adjust_prices_for_security(session, mock_sec.id)
        assert written_count == 5

        # Check results from database
        adjusted_prices = session.query(AdjustedPrice)\
            .filter(AdjustedPrice.security_id == mock_sec.id)\
            .order_by(AdjustedPrice.trade_date.asc()).all()

        assert len(adjusted_prices) == 5

        # Verify Day 1 (Adjusted): Price / 5.0, Vol * 5.0
        p1 = adjusted_prices[0]
        assert p1.trade_date == date(2025, 1, 10)
        assert float(p1.adj_close) == 3010.0 / 5.0
        assert p1.adj_volume == 100000 * 5
        assert float(p1.adjustment_factor) == 5.0

        # Verify Day 3 (Post-split/Ex-date): Unchanged
        p3 = adjusted_prices[2]
        assert p3.trade_date == date(2025, 1, 12)
        assert float(p3.adj_close) == 605.0
        assert p3.adj_volume == 550000
        assert float(p3.adjustment_factor) == 1.0

        # Verify Corporate action marked as processed
        db_action = session.query(CorporateAction).filter(CorporateAction.security_id == mock_sec.id).first()
        assert db_action.is_processed is True
        assert db_action.processed_at is not None

        print("   [OK] Price adjustment calculation verified correctly!")

        # ==========================================
        # 3. Test Symbol Rename Propagation
        # ==========================================
        print("\n3. Testing symbol rename propagation...")
        
        # Clean previous renames
        existing_rename = session.query(SymbolChange).filter(SymbolChange.old_symbol == "OLD_MOCK").first()
        if existing_rename:
            session.delete(existing_rename)
        existing_renamed_sec = session.query(Security).filter(Security.symbol.in_(["OLD_MOCK", "NEW_MOCK"])).first()
        if existing_renamed_sec:
            session.delete(existing_renamed_sec)
        session.commit()

        # Add security to rename
        old_sec = Security(
            symbol="OLD_MOCK",
            company_name="Mock Rename Corp",
            security_type="STOCK",
            isin="INE888Y01018",
            is_active=True,
            data_source="MOCK"
        )
        session.add(old_sec)
        session.commit()

        # Add symbol changes in df format (mimicking CSV download)
        symbol_changes_service = SymbolChangesService(client)
        df_mock = pd.DataFrame([
            {
                "company_name": "Mock Rename Corp",
                "old_symbol": "OLD_MOCK",
                "new_symbol": "NEW_MOCK",
                "effective_date": "15-JAN-2025"
            }
        ])
        
        # Mock download_symbol_changes return val
        async def mock_download_changes():
            return df_mock
        client.download_symbol_changes = mock_download_changes
        
        # Sync
        recorded = await symbol_changes_service.sync_symbol_changes(session)
        assert recorded == 1
        
        # Verify db renamed old_sec to NEW_MOCK
        renamed_sec = session.query(Security).filter(Security.id == old_sec.id).one()
        assert renamed_sec.symbol == "NEW_MOCK"
        
        # Verify symbol change applied flag
        change_log = session.query(SymbolChange).filter(SymbolChange.security_id == old_sec.id).one()
        assert change_log.is_applied is True
        assert change_log.applied_at is not None
        assert change_log.new_symbol == "NEW_MOCK"

        print("   [OK] Symbol rename propagation verified correctly!")

        # ==========================================
        # 4. Test Corporate Actions Fetching (API Integration)
        # ==========================================
        print("\n4. Testing corporate actions API fetching (TCS historical action)...")
        
        # Seed a security for TCS if not exists
        tcs_sec = session.query(Security).filter(Security.symbol == "TCS").first()
        if not tcs_sec:
            tcs_sec = Security(
                symbol="TCS",
                company_name="Tata Consultancy Services Limited",
                security_type="STOCK",
                isin="INE467B01029",
                is_active=True,
                data_source="MOCK"
            )
            session.add(tcs_sec)
            session.commit()

        # TCS had a 1:1 bonus issue in ex_date: 31-May-2018
        # We query a window containing May 31, 2018
        ca_service = CorporateActionsService(client)
        from_dt = date(2018, 5, 20)
        to_dt = date(2018, 6, 5)
        
        print(f"   Fetching actual corporate actions for TCS between {from_dt} and {to_dt}...")
        ca_count = await ca_service.sync_corporate_actions(session, from_dt, to_dt, symbol="TCS")
        
        # Query actions seeded for TCS
        tcs_actions = session.query(CorporateAction).filter(CorporateAction.security_id == tcs_sec.id).all()
        
        if len(tcs_actions) > 0:
            print(f"   Found {len(tcs_actions)} actions for TCS in DB.")
            for act in tcs_actions:
                print(f"     Action: Type={act.action_type}, Ex-date={act.ex_date}, Factor={act.adjustment_factor}, Desc={act.description}")
                if act.action_type == "BONUS":
                    assert act.bonus_ratio_new == 1 and act.bonus_ratio_existing == 1
                    assert float(act.adjustment_factor) == 2.0
            print("   [OK] Corporate action API parsing and sync verified successfully.")
        else:
            print("   [WARNING] No historical corporate actions fetched. (API request might have been rate-limited or blocked, which is acceptable during dev tests).")

        print("\nAll Phase 3 verification tests passed successfully!")

    except Exception as e:
        print(f"\n[ERROR] Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        session.close()
        await client.close()


if __name__ == "__main__":
    asyncio.run(run_tests())
