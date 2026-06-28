import asyncio
from datetime import date
from pathlib import Path
import pandas as pd
from nicegui import ui

from src.db.engine import SessionLocal
from src.services.screener import run_sharpe_screener, export_screener_to_excel
from loguru import logger
from sqlalchemy import func
from src.models import RawPrice


def get_latest_trade_date() -> date:
    """Helper to query the latest available trade date from DB."""
    session = SessionLocal()
    try:
        latest = session.query(func.max(RawPrice.trade_date)).scalar()
        return latest if latest else date.today()
    except Exception:
        return date.today()
    finally:
        session.close()


def render():
    """Render the Sharpe Screener Page."""
    # Storage for results scoped locally per page render to prevent multi-client data leakage
    current_results = {
        "df_all": pd.DataFrame(),
        "df_filtered": pd.DataFrame(),
        "target_date": date.today(),
        "long_months": 6,
        "short_months": 3
    }
    latest_db_date = get_latest_trade_date()
    
    with ui.column().classes("w-full gap-6 p-6"):
        # Page Title Card
        with ui.column().classes("gap-1"):
            ui.label("Sharpe Ratio Based Screener").classes("text-3xl font-bold text-white")
            ui.label("Identify premium momentum stocks using dual-window Sharpe Ratio ranking and strict quality filters.").classes("text-sm text-slate-400")
            
        # Control Panel Card
        with ui.card().classes("glass-card w-full gap-6"):
            ui.label("Screening Configurations").classes("text-lg font-bold text-white mb-2")
            
            with ui.row().classes("w-full gap-6 wrap items-center"):
                # Date Input
                date_input = ui.input(
                    label="Target Date", 
                    value=latest_db_date.strftime("%Y-%m-%d")
                ).props("outlined dark type=date").classes("w-64")
                
                # Long Window
                long_months_input = ui.number(
                    label="Long Window (Months)", 
                    value=6, 
                    format="%d"
                ).props("outlined dark min=1 max=12 step=1").classes("w-64")
                
                # Short Window
                short_months_input = ui.number(
                    label="Short Window (Months)", 
                    value=3, 
                    format="%d"
                ).props("outlined dark min=1 max=12 step=1").classes("w-64")
                
            # Checkboxes for security types
            with ui.row().classes("w-full gap-6 wrap items-center p-2"):
                ui.label("Security Types:").classes("text-sm text-slate-300 font-bold")
                stocks_cb = ui.checkbox("Stocks", value=True).props("dark")
                etfs_cb = ui.checkbox("ETFs", value=False).props("dark")
                indexes_cb = ui.checkbox("Indexes", value=False).props("dark")
                
            with ui.expansion("Advanced Filtering Parameters", icon="tune").classes("text-white w-full border border-white/10 rounded-lg"):
                with ui.row().classes("w-full gap-6 wrap p-4"):
                    mcap_input = ui.number(
                        label="Min Market Cap (Cr)", 
                        value=1000, 
                        format="%d"
                    ).props("outlined dark min=1 step=100").classes("w-60")
                    
                    roc_annual_input = ui.number(
                        label="Min Annual ROC (%)", 
                        value=6.5, 
                        format="%.1f"
                    ).props("outlined dark step=0.5").classes("w-60")
                    
                    turnover_input = ui.number(
                        label="Min Median Turnover (Cr/day)", 
                        value=1.0, 
                        format="%.1f"
                    ).props("outlined dark step=0.1").classes("w-60")
                    
            with ui.row().classes("w-full gap-4 items-center justify-between mt-4"):
                # Action Buttons
                with ui.row().classes("gap-4"):
                    run_btn = ui.button("Run Sharpe Screener", icon="bolt") \
                        .props('unelevated color=indigo') \
                        .classes("px-6 py-2 text-white font-semibold rounded-lg")
                    
                    export_btn = ui.button("Export to Excel", icon="download") \
                        .props('outline dark') \
                        .classes("text-emerald-400 border-emerald-400/30 disabled:opacity-50")
                    export_btn.disable() # Disabled until run succeeds
                    
                # Status Spinner / Text
                status_container = ui.row().classes("items-center gap-2")
                with status_container:
                    status_spinner = ui.spinner(size="md").classes("hidden")
                    status_label = ui.label("Ready to run screener.").classes("text-sm text-slate-400")

        # Result Section Container (Dynamically updated)
        results_container = ui.column().classes("w-full gap-6")
        
        # Define AG Grid columns
        columns = [
            {"headerName": "Rank", "field": "Avg_sharpe_6_3_Rank", "width": 80, "pinned": "left", "sort": "asc"},
            {"headerName": "Symbol", "field": "symbol", "width": 110, "pinned": "left", "filter": "agTextColumnFilter", "cellStyle": {"fontWeight": "bold", "color": "#818cf8"}},
            {"headerName": "Company Name", "field": "company_name", "width": 240, "filter": "agTextColumnFilter"},
            {"headerName": "Type", "field": "security_type", "width": 90, "filter": "agTextColumnFilter"},
            {"headerName": "Close (Rs.)", "field": "close", "width": 110, "type": "numericColumn", ":valueFormatter": "x => x.value ? '₹' + Number(x.value).toFixed(2) : '-'"},
            {"headerName": "6M Sharpe", "field": "sharpe_6", "width": 115, "type": "numericColumn", ":valueFormatter": "x => x.value ? Number(x.value).toFixed(4) : '-'"},
            {"headerName": "3M Sharpe", "field": "sharpe_3", "width": 115, "type": "numericColumn", ":valueFormatter": "x => x.value ? Number(x.value).toFixed(4) : '-'"},
            {"headerName": "6M ROC", "field": "ROC_6", "width": 100, "type": "numericColumn", ":valueFormatter": "x => x.value ? Number(x.value).toFixed(2) + '%' : '-'"},
            {"headerName": "3M ROC", "field": "ROC_3", "width": 100, "type": "numericColumn", ":valueFormatter": "x => x.value ? Number(x.value).toFixed(2) + '%' : '-'"},
            {"headerName": "Annual ROC", "field": "ROC_annual", "width": 115, "type": "numericColumn", ":valueFormatter": "x => x.value ? Number(x.value).toFixed(2) + '%' : '-'"},
            {"headerName": "Away 52WH", "field": "away_52wh", "width": 120, "type": "numericColumn", ":valueFormatter": "x => x.value ? Number(x.value).toFixed(2) + '%' : '-'"},
            {"headerName": "Circuit Hits 3M", "field": "total_circuit_hits_3m", "width": 130, "type": "numericColumn"},
            {"headerName": "Med. Turnover (Cr)", "field": "median_turnover_cr", "width": 150, "type": "numericColumn", ":valueFormatter": "x => x.value ? '₹' + Number(x.value).toFixed(2) : '-'"},
            {"headerName": "MCAP (Cr)", "field": "market_cap_cr", "width": 130, "type": "numericColumn", ":valueFormatter": "x => x.value ? '₹' + Number(x.value).toLocaleString('en-IN') : '-'"},
            {"headerName": "52W High", "field": "week_52_high", "width": 110, "type": "numericColumn", ":valueFormatter": "x => x.value ? '₹' + Number(x.value).toFixed(2) : '-'"},
            {"headerName": "Industry", "field": "industry", "width": 160, "filter": "agTextColumnFilter"},
            {"headerName": "ISIN", "field": "isin", "width": 140, "filter": "agTextColumnFilter"}
        ]
        
        async def handle_run():
            # Basic validation
            try:
                t_date = date.fromisoformat(date_input.value)
            except Exception:
                ui.notify("Invalid date format. Use YYYY-MM-DD.", type="negative")
                return
                
            long_val = int(long_months_input.value)
            short_val = int(short_months_input.value)
            
            if long_val <= 0 or short_val <= 0 or long_val > 12 or short_val > 12:
                ui.notify("Sharpe lookback months must be between 1 and 12.", type="negative")
                return
                
            if long_val < short_val:
                ui.notify("Long Window must be greater than or equal to Short Window.", type="negative")
                return
                
            selected_types = []
            if stocks_cb.value:
                selected_types.append("STOCK")
            if etfs_cb.value:
                selected_types.append("ETF")
            if indexes_cb.value:
                selected_types.append("INDEX")
                
            if not selected_types:
                ui.notify("Please select at least one security type.", type="negative")
                return
                
            # Start spinner
            status_spinner.classes(remove="hidden")
            status_label.set_text("Analyzing database & running calculations...")
            run_btn.disable()
            export_btn.disable()
            results_container.clear()
            
            # Allow UI to render spinner before blocking compute
            await ui.run_javascript("new Promise(resolve => setTimeout(resolve, 100))")
            
            try:
                # 1. Execute Sharpe Screener calculation on worker thread using a thread-local session
                def run_screener_in_thread():
                    with SessionLocal() as session:
                        return run_sharpe_screener(
                            session=session,
                            target_date=t_date,
                            long_months=long_val,
                            short_months=short_val,
                            mcap_filter_cr=float(mcap_input.value),
                            roc_annual_filter=float(roc_annual_input.value),
                            turnover_filter_cr=float(turnover_input.value),
                            security_types=selected_types
                        )

                df_all = await asyncio.to_thread(run_screener_in_thread)
                
                if df_all.empty:
                    status_label.set_text("No stocks passed the base filters on selected date.")
                    ui.notify("Zero stocks matched filters.", type="warning")
                    return
                    
                # 2. Apply High-Conviction watchlist filters for the second sheet
                df_filtered = df_all.copy()
                df_filtered = df_filtered[
                    (df_filtered["ROC_3"] > 20.0) &
                    (df_filtered["away_52wh"] >= -25.0) &
                    (df_filtered["total_circuit_hits_3m"] <= 10)
                ].reset_index(drop=True)
                
                # Convert NaN values to None to ensure valid JSON serialization for AG Grid
                df_all_sanitized = df_all.where(pd.notnull(df_all), None)
                df_filtered_sanitized = df_filtered.where(pd.notnull(df_filtered), None)

                # Cache results for Excel export (keep original DataFrames for Excel writing)
                current_results["df_all"] = df_all
                current_results["df_filtered"] = df_filtered
                current_results["target_date"] = t_date
                current_results["long_months"] = long_val
                current_results["short_months"] = short_val
                
                # Update status
                status_label.set_text(
                    f"Sync completed! Passed base filters: {len(df_all)} stocks | "
                    f"Watchlist: {len(df_filtered)} stocks."
                )
                export_btn.enable()
                
                # Render results in tabbed view
                with results_container:
                    with ui.tabs().classes("w-full") as tabs:
                        tab_all = ui.tab(f"Passed Stocks ({len(df_all)})")
                        tab_filtered = ui.tab(f"High-Conviction Watchlist ({len(df_filtered)})")
                        
                    with ui.tab_panels(tabs, value=tab_all).classes("w-full bg-[#1a1a2e] border border-white/10 rounded-lg p-4"):
                        with ui.tab_panel(tab_all):
                            with ui.row().classes("w-full items-center justify-between mb-4"):
                                search_all = ui.input(placeholder="Search Passed Stocks...") \
                                    .props('outlined dense dark') \
                                    .classes("w-72 bg-[#2a2a3e] rounded-lg")
                                    
                            grid_all = ui.aggrid({
                                "columnDefs": columns,
                                "rowData": df_all_sanitized.to_dict(orient="records"),
                                "rowSelection": "single",
                                "theme": "balham",
                                "pagination": True,
                                "paginationPageSize": 15,
                                "defaultColDef": {
                                    "sortable": True,
                                    "resizable": True,
                                    "filter": True
                                }
                            }).classes("w-full h-[500px] border border-white/10 rounded-lg")
                            
                            search_all.on("value-change", lambda e: grid_all.run_grid_method("setGridOption", "quickFilterText", e.value))
                            grid_all.on("cellDoubleClicked", lambda e: ui.navigate.to(f"/stocks/{e.args['data']['symbol']}"))
                            
                        with ui.tab_panel(tab_filtered):
                            if df_filtered.empty:
                                with ui.column().classes("w-full items-center justify-center p-8 text-center"):
                                    ui.icon("info", size="lg").classes("text-slate-400 mb-2")
                                    ui.label("No Stocks Passed Strict Watchlist Criteria").classes("text-white font-bold text-lg")
                                    ui.label("None of the base stocks matched: 3M ROC > 20%, Close >= 75% of 52WH, and Circuit Hits <= 10.").classes("text-sm text-slate-400")
                            else:
                                with ui.row().classes("w-full items-center justify-between mb-4"):
                                    search_filt = ui.input(placeholder="Search Watchlist...") \
                                        .props('outlined dense dark') \
                                        .classes("w-72 bg-[#2a2a3e] rounded-lg")
                                        
                                grid_filt = ui.aggrid({
                                    "columnDefs": columns,
                                    "rowData": df_filtered_sanitized.to_dict(orient="records"),
                                    "rowSelection": "single",
                                    "theme": "balham",
                                    "pagination": True,
                                    "paginationPageSize": 15,
                                    "defaultColDef": {
                                        "sortable": True,
                                        "resizable": True,
                                        "filter": True
                                    }
                                }).classes("w-full h-[500px] border border-white/10 rounded-lg")
                                
                                search_filt.on("value-change", lambda e: grid_filt.run_grid_method("setGridOption", "quickFilterText", e.value))
                                grid_filt.on("cellDoubleClicked", lambda e: ui.navigate.to(f"/stocks/{e.args['data']['symbol']}"))

                ui.notify("Sharpe screening finished successfully!", type="positive")
                
            except Exception as e:
                logger.error(f"Screener failed: {e}")
                status_label.set_text("Error occurred during screening calculation.")
                ui.notify(f"Screening failed: {e}", type="negative")
            finally:
                status_spinner.classes(add="hidden")
                run_btn.enable()

        async def handle_export():
            if current_results["df_all"].empty:
                ui.notify("No data to export. Run the screener first.", type="warning")
                return
                
            try:
                # Export results to Excel (saves to reports/ on server)
                file_path = export_screener_to_excel(
                    df_all=current_results["df_all"],
                    df_filtered=current_results["df_filtered"],
                    target_date=current_results["target_date"],
                    long_months=current_results["long_months"],
                    short_months=current_results["short_months"]
                )
                
                # Read file bytes into memory then immediately delete from disk
                p = Path(file_path)
                file_bytes = p.read_bytes()
                p.unlink()
                logger.info(f"Deleted server-side copy after reading into memory: {file_path}")
                
                # Trigger browser download from in-memory bytes (no file left on server)
                ui.download(file_bytes, filename=p.name)
                
                # Notify the user of successful download trigger
                ui.notify("Excel export completed! Download started.", type="positive")
                status_label.set_text("Report exported successfully! Download initiated.")
            except Exception as e:
                logger.error(f"Excel export failed: {e}")
                ui.notify(f"Excel export failed: {e}", type="negative")
                
        run_btn.on("click", handle_run)
        export_btn.on("click", handle_export)
