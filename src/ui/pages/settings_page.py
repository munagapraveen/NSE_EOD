import os
from datetime import date
from nicegui import ui

from config.settings import settings
from loguru import logger
from src.models import Base
from src.db.engine import engine
import glob
from src.utils.backup_utils import create_db_backup, prune_old_backups, restore_db_from_backup, get_db_file_path



def save_env_settings(db_url: str, start_date_str: str, delay: float, native: bool, dark: bool):
    """Overwrite the .env file with updated settings values, preserving other variables."""
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
    
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
            
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
            db_input = ui.input(label="Database URL", value=settings.database_url) \
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
                success = save_env_settings(
                    db_input.value.strip(),
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

        # Database Backups & Index Repair Card
        db_file = get_db_file_path()
        with ui.card().classes("glass-card w-full gap-4 max-w-3xl mt-6" + (" hidden" if not db_file else "")):
            ui.label("Database Backups & Index Repair").classes("text-lg font-bold text-white mb-1")
            ui.label("Backup, restore, and rebuild the database file if you encounter constraint errors or index corruption.").classes("text-xs text-slate-400 -mt-3")
            
            db_size_str = "Unknown"
            if db_file and os.path.exists(db_file):
                size_bytes = os.path.getsize(db_file)
                db_size_str = f"{size_bytes / (1024 * 1024):.2f} MB"
            
            with ui.row().classes("w-full justify-between items-center p-3 bg-slate-800/40 rounded-lg"):
                ui.label(f"Active DB Location: {db_file or 'None'}").classes("text-sm text-slate-300 font-mono")
                ui.label(f"Size: {db_size_str}").classes("text-sm text-indigo-400 font-bold")
            
            # Backups management
            ui.label("Available Backups").classes("text-sm font-semibold text-white mt-2")
            
            def get_backups():
                if not db_file:
                    return []
                backup_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(db_file))), "backups")
                if not os.path.exists(backup_dir):
                    return []
                files = glob.glob(os.path.join(backup_dir, "market_backup_*.db"))
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
                
                # Rebuild indexes button
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

            # Dialog for rebuild confirmation
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
                
                db_file_path = get_db_file_path()
                if not db_file_path:
                    ui.notify("Invalid database path.", type="negative")
                    return
                
                backup_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(db_file_path))), 
                    "backups", 
                    filename
                )
                
                success = restore_db_from_backup(backup_path)
                if success:
                    ui.notify("Database successfully restored from backup!", type="positive")
                else:
                    ui.notify("Failed to restore database from backup.", type="negative")

            def execute_rebuild():
                rebuild_dialog.close()
                if is_sync_running():
                    return
                ui.notify("Starting database index rebuild. Please wait...", type="info")
                
                # Dispose of SQLAlchemy connections to unlock the file
                logger.info("Disposing active connections to release database lock for rebuild...")
                engine.dispose()
                
                from scripts.db_recovery import run_db_rebuild
                success = run_db_rebuild()
                if success:
                    ui.notify("Database indexes successfully rebuilt and verified!", type="positive")
                else:
                    ui.notify("Database rebuild failed. See logs for details.", type="negative")

            backup_btn.on("click", handle_backup)
            restore_btn.on("click", restore_dialog.open)
            rebuild_btn.on("click", rebuild_dialog.open)

        # Database Management Card
        with ui.card().classes("glass-card w-full gap-4 max-w-3xl mt-6 border border-red-500/20"):
            ui.label("Database Management").classes("text-lg font-bold text-red-400 mb-1")
            ui.label("Wipe all historical data, indicators, securities, and start fresh.").classes("text-xs text-slate-400 -mt-3")
            
            with ui.row().classes("w-full mt-2"):
                wipe_btn = ui.button("Wipe & Reinitialize Database", icon="delete_forever") \
                    .props('unelevated color=red') \
                    .classes("px-6 py-2 text-white font-semibold rounded-lg")
        
        # Confirmation Dialog
        with ui.dialog() as confirm_dialog, ui.card().classes("p-6 gap-4 max-w-md bg-[#1e1e2e] border border-red-500/30"):
            ui.label("Are you absolutely sure?").classes("text-xl font-bold text-white")
            ui.label("This will permanently delete all stocks, historical prices, calculated SMAs, and market cap logs from the database. This action cannot be undone.").classes("text-sm text-slate-300")
            with ui.row().classes("w-full justify-end gap-3 mt-4"):
                ui.button("Cancel", on_click=confirm_dialog.close).props("flat color=grey").classes("text-white")
                ui.button("Confirm Wipe", on_click=lambda: execute_wipe()).props("unelevated color=red").classes("text-white")

        def execute_wipe():
            confirm_dialog.close()
            if is_sync_running():
                return
            from sqlalchemy import text
            from src.db.engine import SessionLocal
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
                    "securities"
                ]
                # Delete rows in committed transaction in dependency order (children first)
                for table in tables_to_clear:
                    success = False
                    # Try all possible catalog/schema prefixes (handles any DuckDB attachment quirks)
                    for prefix in ["market.main.", "main.main.", "market.", "main.", ""]:
                        try:
                            session.execute(text(f"DELETE FROM {prefix}{table};"))
                            success = True
                            break
                        except Exception as ex:
                            err_str = str(ex).lower()
                            # If the table doesn't exist, we consider it successfully cleared/empty
                            if "does not exist" in err_str or "not found" in err_str:
                                success = True
                                break
                            logger.warning(f"Prefix '{prefix}' failed for '{table}': {ex}")
                            continue
                    if not success:
                        raise Exception(f"Could not resolve table '{table}' in any catalog namespace.")
                session.commit()
                # Re-run schema creation to ensure all tables exist
                Base.metadata.create_all(bind=engine)
                ui.notify("Database wiped successfully!", type="positive")
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to wipe database: {e}")
                ui.notify(f"Database wipe failed: {e}", type="negative")
            finally:
                session.close()
        
        wipe_btn.on("click", confirm_dialog.open)

