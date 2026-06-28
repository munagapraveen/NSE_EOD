import os
import asyncio
from datetime import date, timedelta, datetime
from nicegui import ui

from config.settings import settings
from loguru import logger
from src.models import Base
from src.db.engine import engine
import glob
from src.utils.backup_utils import create_db_backup, prune_old_backups, restore_db_from_backup, get_db_file_path
from sqlalchemy.engine import make_url


def validate_db_url(db_url: str) -> bool:
    """Validate database URL format."""
    try:
        url_obj = make_url(db_url)
        if not (url_obj.drivername.startswith("postgresql") or url_obj.drivername.startswith("duckdb")):
            return False
        return True
    except Exception:
        return False


def save_env_settings(db_url: str, start_date_str: str, delay: float, native: bool, dark: bool):
    """Overwrite the .env file with updated settings values, preserving other variables."""
    if not validate_db_url(db_url):
        logger.error(f"Invalid database URL format: {db_url}")
        return False
        
    env_path = ".env"
    
    # Read existing variables from .env to preserve them
    existing_vars = {}
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        existing_vars[k.strip()] = v.strip()
        except Exception as e:
            logger.warning(f"Could not read existing .env file: {e}")

    # Update modified settings
    existing_vars["DATABASE_URL"] = db_url
    existing_vars["NSE_START_DATE"] = start_date_str
    existing_vars["NSE_REQUEST_DELAY_SECONDS"] = str(delay)
    existing_vars["APP_NATIVE"] = str(native).upper()
    existing_vars["APP_DARK_MODE"] = str(dark).upper()
    
    # Ensure mandatory baseline settings are present
    if "APP_TITLE" not in existing_vars:
        existing_vars["APP_TITLE"] = settings.app_title
    if "APP_HOST" not in existing_vars:
        existing_vars["APP_HOST"] = settings.app_host
    if "APP_PORT" not in existing_vars:
        existing_vars["APP_PORT"] = str(settings.app_port)
    if "LOG_LEVEL" not in existing_vars:
        existing_vars["LOG_LEVEL"] = settings.log_level

    # Compile new env content
    lines = [f"{k}={v}\n" for k, v in existing_vars.items()]
    
    temp_env_path = env_path + ".tmp"
    try:
        with open(temp_env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(temp_env_path, env_path)
            
        # Update settings in-memory
        settings.database_url = db_url
        settings.nse_start_date = date.fromisoformat(start_date_str)
        settings.nse_request_delay_seconds = delay
        settings.app_native = native
        settings.app_dark_mode = dark
        
        return True
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        return False


def render():
    """Render the Settings page."""
    
    with ui.column().classes("w-full gap-6 p-6"):
        # Page Title
        with ui.column().classes("gap-1"):
            ui.label("Application Settings").classes("text-3xl font-bold text-white")
            ui.label("Configure database connection string, ingestion delays, and display preferences.").classes("text-sm text-slate-400")

        # Config Panel
        with ui.card().classes("glass-card w-full gap-6 max-w-3xl"):
            ui.label("Database & Connection Configuration").classes("text-lg font-bold text-white mb-2")
            
            # Database connection URL
            db_input = ui.input(label="Database URL", value=settings.database_url, password=True, password_toggle_button=True) \
                .props("outlined dark") \
                .classes("w-full")
            ui.label("Database Connection String (URL). Use a postgresql:// URL for cloud database storage.").classes("text-xs text-slate-500 -mt-4")

            ui.label("Ingestion & Rate Limit Parameters").classes("text-lg font-bold text-white mb-2 mt-4")
            
            with ui.row().classes("w-full gap-6 wrap"):
                # NSE start date
                start_date_input = ui.input(label="Download Start Date", value=settings.nse_start_date.strftime("%Y-%m-%d")) \
                    .props("outlined dark type=date") \
                    .classes("w-64")
                    
                # Request delay
                delay_input = ui.number(label="Request Delay (seconds)", value=settings.nse_request_delay_seconds, format="%.1f") \
                    .props("outlined dark step=0.5 min=1.0") \
                    .classes("w-64")
            ui.label("A delay of 3.0s is recommended to prevent your IP from being rate-limited or blocked by NSE India.").classes("text-xs text-slate-500 -mt-4")

            ui.label("Window & Display Options").classes("text-lg font-bold text-white mb-2 mt-4")
            
            with ui.row().classes("w-full gap-6 wrap"):
                native_cb = ui.checkbox("Launch as Desktop App Window (Native Mode)", value=settings.app_native).props("dark")
                dark_cb = ui.checkbox("Enable Dark Mode by Default", value=settings.app_dark_mode).props("dark")
            
            # Action Button
            with ui.row().classes("w-full mt-6 justify-end"):
                save_btn = ui.button("Save Settings", icon="save") \
                    .props('unelevated color=indigo') \
                    .classes("px-6 py-2 text-white font-semibold rounded-lg")
                    
            async def handle_save():
                url_to_save = db_input.value.strip()
                if not validate_db_url(url_to_save):
                    ui.notify("Invalid Database URL format. Must be a valid PostgreSQL (postgresql://...) or DuckDB (duckdb://...) connection string.", type="negative")
                    return
                    
                success = save_env_settings(
                    url_to_save,
                    start_date_input.value,
                    float(delay_input.value),
                    native_cb.value,
                    dark_cb.value
                )
                if success:
                    ui.notify("Settings saved successfully! Restart application to apply environment shifts.", type="positive")
                else:
                    ui.notify("Failed to save settings to .env file.", type="negative")
                    
            save_btn.on("click", handle_save)

        # Database Backups & Snapshot Management
        db_url = settings.database_url
        is_duckdb = db_url.startswith("duckdb:///")
        is_postgres = "postgresql" in db_url
        
        if is_duckdb or is_postgres:
            with ui.card().classes("glass-card w-full gap-4 max-w-3xl mt-6"):
                ui.label("Database Backups & Snapshot Management").classes("text-lg font-bold text-white mb-1")
                
                if is_duckdb:
                    ui.label("Backup, restore, and rebuild the local DuckDB database file if you encounter constraint errors or index corruption.").classes("text-xs text-slate-400 -mt-3")
                    db_file = get_db_file_path()
                    db_size_str = "Unknown"
                    if db_file and os.path.exists(db_file):
                        size_bytes = os.path.getsize(db_file)
                        db_size_str = f"{size_bytes / (1024 * 1024):.2f} MB"
                    with ui.row().classes("w-full justify-between items-center p-3 bg-slate-800/40 rounded-lg"):
                        ui.label(f"Active DuckDB File: {db_file}").classes("text-sm text-slate-300 font-mono")
                        ui.label(f"Size: {db_size_str}").classes("text-sm text-indigo-400 font-bold")
                else:
                    ui.label("Backup and restore your PostgreSQL database using pg_dump and pg_restore utilities.").classes("text-xs text-slate-400 -mt-3")
                    try:
                        url_obj = make_url(db_url)
                        connection_label = f"PostgreSQL: {url_obj.username}@{url_obj.host or 'localhost'}/{url_obj.database}"
                    except Exception:
                        connection_label = "PostgreSQL Connection"
                    with ui.row().classes("w-full justify-between items-center p-3 bg-slate-800/40 rounded-lg"):
                        ui.label(f"Active Host Connection: {connection_label}").classes("text-sm text-slate-300 font-mono")
                        ui.label("Online").classes("text-sm text-green-400 font-bold")
                
                # Backups management
                ui.label("Available Backups").classes("text-sm font-semibold text-white mt-2")
                
                def get_backups():
                    backup_dir = os.path.join(os.getcwd(), "backups")
                    if not os.path.exists(backup_dir):
                        return []
                    pattern_db = os.path.join(backup_dir, "market_backup_*.db")
                    pattern_dump = os.path.join(backup_dir, "market_backup_*.dump")
                    files = glob.glob(pattern_db) + glob.glob(pattern_dump)
                    return sorted([os.path.basename(f) for f in files], reverse=True)
                
                backups_list = get_backups()
                backup_select = ui.select(
                    options=backups_list, 
                    value=backups_list[0] if backups_list else None, 
                    label="Select Backup File"
                ).props("outlined dark").classes("w-full")
                
                with ui.row().classes("w-full gap-4 mt-2"):
                    # Manual backup button
                    backup_btn = ui.button("Create Backup", icon="backup") \
                        .props("unelevated color=indigo") \
                        .classes("px-4 py-2 font-semibold text-white rounded-lg")
                    
                    # Restore button
                    restore_btn = ui.button("Restore Backup", icon="restore") \
                        .props("unelevated color=amber") \
                        .classes("px-4 py-2 font-semibold text-white rounded-lg")
                    
                    # Rebuild indexes button (DuckDB only)
                    if is_duckdb:
                        rebuild_btn = ui.button("Rebuild & Repair Indexes", icon="build") \
                            .props("unelevated color=green") \
                            .classes("px-4 py-2 font-semibold text-white rounded-lg")

                # Dialog for restore confirmation
                with ui.dialog() as restore_dialog, ui.card().classes("p-6 gap-4 max-w-md bg-[#1e1e2e] border border-amber-500/30"):
                    ui.label("Restore Database?").classes("text-xl font-bold text-white")
                    ui.label("This will overwrite your active database with the selected backup snapshot. Any changes since the backup was taken will be lost.").classes("text-sm text-slate-300")
                    with ui.row().classes("w-full justify-end gap-3 mt-4"):
                        ui.button("Cancel", on_click=restore_dialog.close).props("flat color=grey").classes("text-white")
                        ui.button("Confirm Restore", on_click=lambda: execute_restore()).props("unelevated color=amber").classes("text-white")

                # Dialog for rebuild confirmation (DuckDB only)
                if is_duckdb:
                    with ui.dialog() as rebuild_dialog, ui.card().classes("p-6 gap-4 max-w-md bg-[#1e1e2e] border border-green-500/30"):
                        ui.label("Rebuild & Repair Indexes?").classes("text-xl font-bold text-white")
                        ui.label("This will temporarily export all tables, recreate the database structure, and re-import all data. This resolves index corruption and constraint errors without any data loss or internet downloads.").classes("text-sm text-slate-300")
                        with ui.row().classes("w-full justify-end gap-3 mt-4"):
                            ui.button("Cancel", on_click=rebuild_dialog.close).props("flat color=grey").classes("text-white")
                            ui.button("Confirm Rebuild", on_click=lambda: execute_rebuild()).props("unelevated color=green").classes("text-white")

                def is_sync_running():
                    from src.ui.pages import download
                    if download._sync_manager and download._sync_manager.is_running:
                        ui.notify("Cannot perform database administration tasks while a download is running.", type="warning")
                        return True
                    return False

                async def handle_backup():
                    if is_sync_running():
                        return
                    path = create_db_backup()
                    if path:
                        prune_old_backups(keep_count=5)
                        ui.notify(f"Database backup created: {os.path.basename(path)}", type="positive")
                        # Refresh select options
                        new_backups = get_backups()
                        backup_select.options = new_backups
                        backup_select.value = new_backups[0] if new_backups else None
                    else:
                        ui.notify("Failed to create database backup.", type="negative")

                def execute_restore():
                    restore_dialog.close()
                    if is_sync_running():
                        return
                    filename = backup_select.value
                    if not filename:
                        ui.notify("No backup selected for restore.", type="negative")
                        return
                    
                    backup_dir = os.path.join(os.getcwd(), "backups")
                    backup_path = os.path.join(backup_dir, filename)
                    
                    success = restore_db_from_backup(backup_path)
                    if success:
                        ui.notify("Database successfully restored from backup!", type="positive")
                    else:
                        ui.notify("Failed to restore database from backup. Make sure PG pg_restore is available.", type="negative")

                def execute_rebuild():
                    rebuild_dialog.close()
                    if is_sync_running():
                        return
                    ui.notify("Starting database index rebuild. Please wait...", type="info")
                    
                    logger.info("Disposing active connections to release database lock for rebuild...")
                    engine.dispose()
                    
                    try:
                        from scripts.db_recovery import run_db_rebuild
                        success = run_db_rebuild()
                        if success:
                            ui.notify("Database indexes successfully rebuilt and verified!", type="positive")
                        else:
                            ui.notify("Database rebuild failed. See logs for details.", type="negative")
                    except Exception as err:
                        ui.notify(f"Rebuild failed: {err}", type="negative")

                backup_btn.on("click", handle_backup)
                restore_btn.on("click", restore_dialog.open)
                if is_duckdb:
                    rebuild_btn.on("click", rebuild_dialog.open)

        # Database Health & Gap Repair Card
        with ui.card().classes("glass-card w-full gap-4 max-w-3xl mt-6"):
            ui.label("Database Health & Gap Repair").classes("text-lg font-bold text-white mb-1")
            ui.label("Audit and heal data integrity gaps across database tables. Useful for repairing interrupted sync runs.").classes("text-xs text-slate-400 -mt-3")
            
            with ui.row().classes("w-full gap-4 mt-2"):
                repair_adj_btn = ui.button("Repair Raw vs Adjusted Gaps", icon="healing") \
                    .props("unelevated color=indigo") \
                    .classes("px-4 py-2 font-semibold text-white rounded-lg")
                    
                repair_ind_btn = ui.button("Repair Adjusted vs Indicators Gaps", icon="analytics") \
                    .props("unelevated color=teal") \
                    .classes("px-4 py-2 font-semibold text-white rounded-lg")
                    
                repair_mcap_btn = ui.button("Repair Raw vs Market Cap Gaps", icon="trending_up") \
                    .props("unelevated color=purple") \
                    .classes("px-4 py-2 font-semibold text-white rounded-lg")
                    
                repair_index_btn = ui.button("Heal Index Completeness", icon="format_list_bulleted") \
                    .props("unelevated color=blue-grey") \
                    .classes("px-4 py-2 font-semibold text-white rounded-lg")

            # Click Handlers
            async def run_repair_adj():
                if is_sync_running(): return
                ui.notify("Auditing raw vs adjusted price gaps...", type="info")
                from src.db.engine import SessionLocal
                from src.services.price_adjuster import adjust_prices_for_security
                from src.services.sync_manager import SyncManager
                session = SessionLocal()
                try:
                    sm = SyncManager()
                    gaps = sm._find_securities_with_adj_gaps(session, date(2024, 1, 1))
                    if not gaps:
                        ui.notify("No raw vs adjusted price gaps found. Database is healthy!", type="positive")
                        return
                    ui.notify(f"Found {len(gaps)} securities with gaps. Repairing...", type="info")
                    count = 0
                    for sec_id in gaps:
                        await adjust_prices_for_security(session, sec_id)
                        count += 1
                        if count % 10 == 0:
                            ui.notify(f"Repaired adjusted prices for {count}/{len(gaps)} securities...", type="info")
                        await asyncio.sleep(0.01)
                    ui.notify(f"Successfully repaired adjusted price gaps for {len(gaps)} securities!", type="positive")
                except Exception as ex:
                    logger.error(f"Error repairing adjusted gaps: {ex}")
                    ui.notify(f"Failed to repair adjusted gaps: {ex}", type="negative")
                finally:
                    session.close()

            async def run_repair_ind():
                if is_sync_running(): return
                ui.notify("Auditing adjusted vs indicator gaps...", type="info")
                from src.db.engine import SessionLocal
                from src.services.indicators import calculate_indicators_for_security
                from src.services.sync_manager import SyncManager
                session = SessionLocal()
                try:
                    sm = SyncManager()
                    gaps = sm._find_securities_with_ind_gaps(session, date(2024, 1, 1))
                    if not gaps:
                        ui.notify("No adjusted vs indicator gaps found. Database is healthy!", type="positive")
                        return
                    ui.notify(f"Found {len(gaps)} securities with gaps. Repairing...", type="info")
                    count = 0
                    for sec_id in gaps:
                        await calculate_indicators_for_security(session, sec_id)
                        count += 1
                        if count % 10 == 0:
                            ui.notify(f"Repaired indicators for {count}/{len(gaps)} securities...", type="info")
                        await asyncio.sleep(0.01)
                    ui.notify(f"Successfully repaired technical indicator gaps for {len(gaps)} securities!", type="positive")
                except Exception as ex:
                    logger.error(f"Error repairing indicator gaps: {ex}")
                    ui.notify(f"Failed to repair indicator gaps: {ex}", type="negative")
                finally:
                    session.close()

            async def run_repair_mcap():
                if is_sync_running(): return
                ui.notify("Auditing raw vs market cap gaps...", type="info")
                from src.db.engine import SessionLocal
                from src.services.market_cap import calculate_historical_market_cap
                from src.models import Security, MarketCap
                from sqlalchemy import select
                session = SessionLocal()
                try:
                    subq = select(MarketCap.security_id).group_by(MarketCap.security_id).subquery()
                    stocks_with_raw_no_mcap = session.execute(
                        select(Security.id, Security.issued_shares)
                        .where(Security.security_type == "STOCK")
                        .where(Security.id.not_in(subq))
                    ).all()
                    
                    gaps = [r.id for r in stocks_with_raw_no_mcap if r.issued_shares]
                    if not gaps:
                        ui.notify("No market cap gaps found. Database is healthy!", type="positive")
                        return
                    ui.notify(f"Found {len(gaps)} stocks with missing market caps. Repairing...", type="info")
                    count = 0
                    for row in stocks_with_raw_no_mcap:
                        if row.issued_shares:
                            await calculate_historical_market_cap(session, row.id, row.issued_shares)
                            count += 1
                            if count % 10 == 0:
                                ui.notify(f"Calculated market caps for {count}/{len(gaps)} stocks...", type="info")
                            await asyncio.sleep(0.01)
                    ui.notify(f"Successfully repaired market cap gaps for {count} stocks!", type="positive")
                except Exception as ex:
                    logger.error(f"Error repairing market cap gaps: {ex}")
                    ui.notify(f"Failed to repair market cap gaps: {ex}", type="negative")
                finally:
                    session.close()

            async def run_heal_index():
                if is_sync_running(): return
                ui.notify("Scanning index completeness...", type="info")
                from src.db.engine import SessionLocal
                from src.services.sync_manager import SyncManager
                session = SessionLocal()
                try:
                    from src.models import Security, SecurityIndex
                    from sqlalchemy import select, delete
                    q_dates = session.execute(select(SecurityIndex.quarter_date).distinct()).scalars().all()
                    if not q_dates:
                        ui.notify("No historical quarters found to heal. Run a sync first.", type="warning")
                        return
                    
                    healed = 0
                    for q_date in q_dates:
                        active_count = session.execute(
                            select(func.count(Security.id))
                            .where(Security.security_type == "STOCK")
                            .where(Security.is_active == True)
                        ).scalar() or 0
                        membership_count = session.execute(
                            select(func.count(func.distinct(SecurityIndex.security_id)))
                            .where(SecurityIndex.quarter_date == q_date)
                        ).scalar() or 0
                        
                        if active_count > 0 and (membership_count < active_count * 0.5):
                            ui.notify(f"Healing incomplete index memberships for quarter {q_date.isoformat()}...", type="info")
                            sm = SyncManager()
                            session.execute(delete(SecurityIndex).where(SecurityIndex.quarter_date == q_date))
                            session.commit()
                            
                            success, failure = await sm._fetch_shares_via_get_quote_api(session, q_date)
                            ui.notify(f"Rebuilt quarter {q_date.isoformat()} memberships: {success} succeeded, {failure} failed.", type="info")
                            healed += 1
                    
                    if healed > 0:
                        ui.notify(f"Successfully healed {healed} incomplete quarters!", type="positive")
                    else:
                        ui.notify("All index quarters are complete and healthy!", type="positive")
                except Exception as ex:
                    logger.error(f"Error healing index completeness: {ex}")
                    ui.notify(f"Failed to heal index completeness: {ex}", type="negative")
                finally:
                    session.close()

            repair_adj_btn.on("click", run_repair_adj)
            repair_ind_btn.on("click", run_repair_ind)
            repair_mcap_btn.on("click", run_repair_mcap)
            repair_index_btn.on("click", run_heal_index)

        # Database Management Card
        with ui.card().classes("glass-card w-full gap-4 max-w-3xl mt-6 border border-red-500/20"):
            ui.label("Database Management").classes("text-lg font-bold text-red-400 mb-1")
            ui.label("Wipe all historical data, indicators, securities, and start fresh.").classes("text-xs text-slate-400 -mt-3")
            
            with ui.row().classes("w-full mt-2"):
                wipe_btn = ui.button("Wipe & Reinitialize Database", icon="delete_forever") \
                    .props('unelevated color=red') \
                    .classes("px-6 py-2 text-white font-semibold rounded-lg")
        
        # Double Confirmation Dialog
        with ui.dialog() as confirm_dialog, ui.card().classes("p-6 gap-4 max-w-md bg-[#1e1e2e] border border-red-500/30"):
            ui.label("Are you absolutely sure?").classes("text-xl font-bold text-white")
            ui.label("This will permanently delete all stocks, historical prices, calculated SMAs, and market cap logs from the database. This action cannot be undone.").classes("text-sm text-slate-300")
            
            # Type WIPE input
            wipe_input = ui.input(label="Type 'WIPE' to confirm").props("outlined dark").classes("w-full")
            
            with ui.row().classes("w-full justify-end gap-3 mt-4"):
                ui.button("Cancel", on_click=confirm_dialog.close).props("flat color=grey").classes("text-white")
                confirm_btn = ui.button("Confirm Wipe", on_click=lambda: execute_wipe()) \
                    .props("unelevated color=red") \
                    .classes("text-white")
                
                # Bind confirm button enable/disable state to wipe_input == 'WIPE'
                confirm_btn.bind_enabled_from(wipe_input, 'value', backward=lambda val: val == 'WIPE')

        def open_confirm_dialog():
            wipe_input.value = ""
            confirm_dialog.open()

        def execute_wipe():
            confirm_dialog.close()
            if is_sync_running():
                return
            from sqlalchemy import text
            from src.db.engine import SessionLocal, engine
            
            # If PostgreSQL: terminate other active backend connections first to prevent blocking/lockouts
            db_url = settings.database_url
            if "postgresql" in db_url:
                try:
                    url = make_url(db_url)
                    db_name = url.database
                    term_query = text("""
                        SELECT pg_terminate_backend(pg_stat_activity.pid)
                        FROM pg_stat_activity
                        WHERE pg_stat_activity.datname = :db_name
                          AND pid <> pg_backend_pid();
                    """)
                    with engine.connect() as conn:
                        conn.execute(term_query, {"db_name": db_name})
                        logger.info("Successfully terminated other active client connections for database wipe.")
                except Exception as conn_err:
                    logger.warning(f"Could not terminate other Postgres connections: {conn_err}")

            # Dispose engine after connections are terminated to close this pool too
            engine.dispose()
            
            session = SessionLocal()
            try:
                
                tables_to_clear = [
                    "sync_log",
                    "indicators",
                    "market_cap",
                    "adjusted_prices",
                    "raw_prices",
                    "corporate_actions",
                    "symbol_changes",
                    "historical_shares",
                    "security_indexes",
                    "securities"
                ]
                
                # If Postgres: we can use TRUNCATE ... CASCADE which is extremely fast and robust!
                if "postgresql" in db_url:
                    truncate_clause = ", ".join(tables_to_clear)
                    session.execute(text(f"TRUNCATE TABLE {truncate_clause} CASCADE;"))
                    session.commit()
                    logger.info("Database tables successfully truncated via Postgres TRUNCATE CASCADE.")
                else:
                    # Fallback to DELETE FROM for DuckDB
                    for table in tables_to_clear:
                        success = False
                        for prefix in ["market.main.", "main.main.", "market.", "main.", ""]:
                            try:
                                session.execute(text(f"DELETE FROM {prefix}{table};"))
                                success = True
                                break
                            except Exception as ex:
                                err_str = str(ex).lower()
                                if "does not exist" in err_str or "not found" in err_str:
                                    success = True
                                    break
                                continue
                        if not success:
                            raise Exception(f"Could not resolve table '{table}' in any catalog namespace.")
                    session.commit()
                
                # Re-run create_all
                Base.metadata.create_all(bind=engine)
                ui.notify("Database wiped successfully!", type="positive")
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to wipe database: {e}")
                ui.notify(f"Database wipe failed: {e}", type="negative")
            finally:
                session.close()
        
        wipe_btn.on("click", open_confirm_dialog)

