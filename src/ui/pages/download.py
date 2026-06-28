import asyncio
import os
from datetime import date, timedelta
from nicegui import ui

from src.db.engine import SessionLocal
from src.services.nse_client import NSEClient
from src.services.sync_manager import SyncManager
from loguru import logger
from sqlalchemy import func
from src.models import RawPrice
from config.settings import settings

# Global state to prevent concurrent downloads
_sync_task = None
_sync_manager = None
_log_listener_active = False


class LogStreamer:
    """Read logs from file and push to textarea in real time, with a limit of 1000 lines to prevent browser freeze."""
    def __init__(self, log_area, max_lines=1000):
        self.log_area = log_area
        self.active = False
        import collections
        self.buffer = collections.deque(maxlen=max_lines)

    async def start(self):
        self.active = True
        log_file = str(settings.log_file)
        
        # Ensure log file exists
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        if not os.path.exists(log_file):
            with open(log_file, "w") as f:
                pass

        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            # Pre-populate the buffer with the last 200 lines from the log file
            import collections
            last_lines = collections.deque(f, maxlen=200)
            self.buffer.clear()
            for line in last_lines:
                self.buffer.append(line.strip())
            
            # Update the textarea initially
            self.log_area.value = "\n".join(self.buffer) + "\n" if self.buffer else ""
            
            # Since collections.deque(f) consumes the entire file, the file cursor
            # is now at the EOF. Subsequent readline calls will read new lines.
            while self.active:
                try:
                    lines_added = False
                    # Read available lines in batches (up to 100 lines at a time) to prevent WebSocket congestion
                    for _ in range(100):
                        line = f.readline()
                        if not line:
                            break
                        self.buffer.append(line.strip())
                        lines_added = True
                    
                    if lines_added:
                        self.log_area.value = "\n".join(self.buffer) + "\n"
                        try:
                            await self.log_area.client.run_javascript(
                                f'const el = document.getElementById("c{self.log_area.id}"); '
                                'if (el) { const ta = el.querySelector("textarea"); if (ta) ta.scrollTop = ta.scrollHeight; }',
                                timeout=1.0
                            )
                        except Exception as js_err:
                            logger.debug(f"Failed to scroll log area: {js_err}")
                    
                    await asyncio.sleep(0.3)
                except Exception:
                    # Client disconnected or UI element deleted, stop streaming
                    self.active = False
                    break

    def stop(self):
        self.active = False


def get_last_updated_date():
    """Get the maximum trade date from raw_prices for stocks/ETFs and indexes (self-healing)."""
    session = SessionLocal()
    try:
        from src.models import Security
        
        # 1. Fetch all distinct trading dates for active Stocks/ETFs
        stock_dates_rows = session.query(RawPrice.trade_date).distinct()\
            .join(Security, Security.id == RawPrice.security_id)\
            .filter(Security.security_type.in_(["STOCK", "ETF"]))\
            .filter(Security.is_active == True)\
            .filter(~Security.symbol.like("TEST%"))\
            .filter(~Security.symbol.like("MOCK%"))\
            .all()
        stock_dates = {r[0] for r in stock_dates_rows if r[0] is not None}
        
        # 2. Fetch all distinct trading dates for active Indexes
        index_dates_rows = session.query(RawPrice.trade_date).distinct()\
            .join(Security, Security.id == RawPrice.security_id)\
            .filter(Security.security_type == "INDEX")\
            .filter(Security.is_active == True)\
            .filter(~Security.symbol.like("TEST%"))\
            .filter(~Security.symbol.like("MOCK%"))\
            .all()
        index_dates = {r[0] for r in index_dates_rows if r[0] is not None}
        
        # Find dates with Stocks/ETFs but no Indexes
        gap_stock_dates = stock_dates - index_dates
        # Find dates with Indexes but no Stocks/ETFs
        gap_index_dates = index_dates - stock_dates
        
        first_gap = None
        if gap_stock_dates and gap_index_dates:
            first_gap = min(min(gap_stock_dates), min(gap_index_dates))
        elif gap_stock_dates:
            first_gap = min(gap_stock_dates)
        elif gap_index_dates:
            first_gap = min(gap_index_dates)
            
        if first_gap:
            logger.info(f"Detected incomplete daily sync (gap date: {first_gap}). Resuming sync from gap date to backfill.")
            return first_gap - timedelta(days=1)
            
        # 3. If no gaps, return the max date
        if stock_dates and index_dates:
            return min(max(stock_dates), max(index_dates))
        elif stock_dates:
            return max(stock_dates)
        elif index_dates:
            return max(index_dates)
        return None
    except Exception as e:
        logger.error(f"Failed to fetch last updated date: {e}")
        return None
    finally:
        session.close()


def render():
    """Render the Download Manager page."""
    global _sync_manager
    if _sync_manager is None:
        client = NSEClient()
        _sync_manager = SyncManager(client)

    # State variables
    running = False
    
    # Calculate default From Date based on database contents
    last_update = get_last_updated_date()
    
    if last_update is None:
        default_from_date = date(2024, 1, 1)
        status_info = "Database is empty. Ready for Initial Ingestion."
        disable_from = False
    else:
        default_from_date = last_update + timedelta(days=1)
        status_info = f"Last Database Update: {last_update.strftime('%d-%b-%Y')}."
        disable_from = True

    with ui.column().classes("w-full gap-6 p-6"):
        # Page Title
        with ui.column().classes("gap-1"):
            ui.label("Sync Downloader").classes("text-3xl font-bold text-white")
            ui.label("Ingest historical or daily incremental prices and corporate actions from NSE India.").classes("text-sm text-slate-400")

        # Config Panel
        with ui.card().classes("glass-card w-full gap-6"):
            ui.label("1. Select Ingestion Settings").classes("text-lg font-bold text-white")
            
            with ui.row().classes("w-full gap-8 wrap items-center"):
                # Date picker
                with ui.row().classes("items-center gap-3"):
                    ui.label("From Date:").classes("text-sm text-slate-400")
                    from_input = ui.input(value=default_from_date.strftime("%Y-%m-%d")).props("outlined dense dark type=date").classes("w-40")
                    if disable_from:
                        from_input.disable()
                    
                    # Status Label next to it
                    status_lbl_info = ui.label(status_info).classes("text-sm text-indigo-400 font-semibold ml-4")



            with ui.row().classes("w-full gap-6 mt-1 wrap"):
                refresh_cb = ui.checkbox("Force Refresh Outstanding Shares", value=False).props("dark disabled")

            # Buttons
            with ui.row().classes("w-full gap-4 mt-4"):
                start_btn = ui.button("Start Download", icon="play_arrow") \
                    .props('unelevated color=indigo') \
                    .classes("px-6 py-2 text-white font-semibold rounded-lg")
                    
                cancel_btn = ui.button("Cancel", icon="cancel") \
                    .props('outline color=red') \
                    .classes("px-6 py-2 rounded-lg")
                cancel_btn.disable()

        # Progress / Status Panel
        progress_card = ui.card().classes("glass-card w-full gap-4 hidden")
        with progress_card:
            ui.label("Sync Status: Running...").classes("text-md font-bold text-indigo-400").name = "status_lbl"
            progress_bar = ui.linear_progress(value=0.0, show_value=False).props("stripe rounded color=indigo")
            progress_lbl = ui.label("Initializing Ingestion pipeline...").classes("text-xs text-slate-400")

        # Logs Console Panel
        with ui.card().classes("glass-card w-full gap-2"):
            ui.label("Real-time Output Log").classes("text-lg font-bold text-white")
            log_area = ui.textarea(value="") \
                .props('readonly dark borderless') \
                .classes("w-full h-[320px] bg-[#14141e] text-slate-300 font-mono text-xs p-4 rounded-lg overflow-y-auto")

        log_streamer = LogStreamer(log_area)

        # Progress reporting helper defined at page level so it can be re-registered
        def handle_progress(stage, percentage, msg):
            try:
                progress_bar.set_value(percentage / 100.0)
                progress_lbl.set_text(f"[{stage}] {msg}")
                if percentage >= 100.0:
                    progress_card.classes(add="hidden")
                else:
                    progress_card.classes(remove="hidden")
            except Exception:
                pass

        # Disconnect handling
        def handle_disconnect():
            log_streamer.stop()
            status_timer.cancel()
            if _sync_manager and _sync_manager.progress_callback == handle_progress:
                _sync_manager.progress_callback = None

        ui.context.client.on_disconnect(handle_disconnect)

        # Restore UI state if sync is already running in background
        if _sync_manager and _sync_manager.is_running:
            running = True
            from_input.disable()
            refresh_cb.disable()
            start_btn.disable()
            cancel_btn.enable()
            
            # Populate active values
            progress_bar.set_value(_sync_manager.current_progress / 100.0)
            progress_lbl.set_text(f"[{_sync_manager.current_stage}] {_sync_manager.current_message}")
            progress_card.classes(remove="hidden")
            
            # Attach progress callback
            _sync_manager.progress_callback = handle_progress
            
            # Start streaming the logs
            asyncio.create_task(log_streamer.start())

        # Trigger logic
        async def on_start():
            nonlocal running
            if _sync_manager and _sync_manager.is_running:
                ui.notify("A data synchronization task is already running in the background.", type="warning")
                return
            running = True
            
            # Disable inputs
            from_input.disable()
            refresh_cb.disable()
            
            start_btn.disable()
            cancel_btn.enable()
            
            # Clear logs and display progress
            log_area.set_value("")
            progress_card.classes(remove="hidden")
            
            # Setup streamer
            asyncio.create_task(log_streamer.start())
            
            # Parse parameters
            try:
                dt_from = date.fromisoformat(from_input.value)
                dt_to = date.today()
            except Exception as e:
                ui.notify(f"Invalid date range format: {e}", type="negative")
                on_finish("FAILED", "Invalid dates")
                return

            options = {
                "stocks": True,
                "etfs": True,
                "indexes": True,
                "corporate_actions": True,
                "market_cap": True,
                "indicators": True,
                "force_shares_refresh": refresh_cb.value
            }

            # Run sync manager in background
            session = SessionLocal()
            try:
                # Attach callback
                _sync_manager.progress_callback = handle_progress
                summary = await _sync_manager.run_sync(
                    session, dt_from, dt_to, options, progress_callback=handle_progress
                )
                on_finish(summary["status"], summary["message"])
            except Exception as ex:
                logger.error(f"Ingestion pipeline failed: {ex}")
                on_finish("FAILED", str(ex))
            finally:
                session.close()

        def on_finish(status, message):
            nonlocal running
            running = False
            log_streamer.stop()
            if _sync_manager:
                _sync_manager.progress_callback = None
            
            # Enable/Disable based on new database state
            last_up = get_last_updated_date()
            if last_up is None:
                from_input.set_value("2024-01-01")
                from_input.enable()
                status_lbl_info.set_text("Database is empty. Ready for Initial Ingestion.")
            else:
                next_st = last_up + timedelta(days=1)
                from_input.set_value(next_st.strftime("%Y-%m-%d"))
                from_input.disable()
                status_lbl_info.set_text(f"Last Database Update: {last_up.strftime('%d-%b-%Y')}.")
                
            start_btn.enable()
            cancel_btn.disable()
            # refresh_cb.enable()
            progress_card.classes(add="hidden")

            # Notify user
            if status == "SUCCESS":
                ui.notify("Data sync completed successfully!", type="positive")
            elif status == "CANCELLED":
                ui.notify("Data sync cancelled by user.", type="warning")
            else:
                ui.notify(f"Data sync failed: {message}", type="negative")

        def on_cancel():
            if _sync_manager and _sync_manager.is_running:
                _sync_manager.request_cancel()
                ui.notify("Requesting sync cancellation...", type="warning")

        # Periodically check status to recover UI when background sync completes
        def check_sync_status():
            nonlocal running
            if _sync_manager and not _sync_manager.is_running and running:
                status = "SUCCESS" if _sync_manager.current_progress >= 100.0 else "FAILED"
                on_finish(status, _sync_manager.current_message)

        status_timer = ui.timer(2.0, check_sync_status)

        # Bind button clicks
        start_btn.on("click", on_start)
        cancel_btn.on("click", on_cancel)
