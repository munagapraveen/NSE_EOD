import os
from datetime import date
from sqlalchemy import select, func, desc
from nicegui import ui

from src.db.engine import SessionLocal
from src.models import Security, RawPrice, SyncLog
from config.settings import settings


def get_dashboard_stats():
    """Fetch counts and database size for dashboard cards."""
    session = SessionLocal()
    try:
        # Stock/ETF counts
        total_stocks = session.query(func.count(Security.id)).filter(Security.security_type == 'STOCK').scalar() or 0
        total_etfs = session.query(func.count(Security.id)).filter(Security.security_type == 'ETF').scalar() or 0
        total_indexes = session.query(func.count(Security.id)).filter(Security.security_type == 'INDEX').scalar() or 0
        
        # Last sync
        last_log = session.query(SyncLog).filter(SyncLog.status == 'SUCCESS').order_by(SyncLog.completed_at.desc()).first()
        last_sync = last_log.completed_at.strftime("%d-%b-%Y %H:%M") if last_log else "Never"
        
        # Database size
        db_path = "data/market.db"
        db_url = settings.database_url
        if db_url.startswith("duckdb:///"):
            db_path = db_url.replace("duckdb:///", "")
            
        db_size_mb = os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0.0
        db_size = f"{db_size_mb:.2f} MB"
        
        return {
            "stocks": total_stocks,
            "etfs": total_etfs,
            "indexes": total_indexes,
            "last_sync": last_sync,
            "db_size": db_size
        }
    except Exception as e:
        return {"stocks": 0, "etfs": 0, "indexes": 0, "last_sync": "Error", "db_size": "0.00 MB"}
    finally:
        session.close()


def get_top_gainers_losers(limit: int = 5):
    """Fetch top gainers and losers for the latest trading date."""
    session = SessionLocal()
    try:
        # 1. Get the latest trade date in the DB
        latest_date = session.query(func.max(RawPrice.trade_date)).scalar()
        if not latest_date:
            return [], []

        # 2. Fetch all raw prices for stocks on that date
        query = (
            select(Security.symbol, Security.company_name, RawPrice.close, RawPrice.prev_close)
            .join(RawPrice, Security.id == RawPrice.security_id)
            .where(RawPrice.trade_date == latest_date)
            .where(Security.security_type == 'STOCK')
            .where(RawPrice.prev_close > 0)
        )
        
        rows = session.execute(query).all()
        
        # 3. Calculate percentage changes
        stock_changes = []
        for row in rows:
            close = float(row.close)
            prev = float(row.prev_close)
            pct = round(((close - prev) / prev) * 100, 2)
            stock_changes.append({
                "symbol": row.symbol,
                "company_name": row.company_name,
                "close": close,
                "change_pct": pct
            })
            
        # 4. Sort and return top 5
        gainers = sorted(stock_changes, key=lambda x: x["change_pct"], reverse=True)[:limit]
        losers = sorted(stock_changes, key=lambda x: x["change_pct"])[:limit]
        
        return gainers, losers
    except Exception as e:
        return [], []
    finally:
        session.close()


def get_recent_activity(limit: int = 10):
    """Fetch last 10 sync log entries."""
    session = SessionLocal()
    try:
        logs = session.query(SyncLog).order_by(SyncLog.started_at.desc()).limit(limit).all()
        return [
            {
                "type": log.sync_type,
                "date": log.sync_date.strftime("%d-%b-%Y") if log.sync_date else "-",
                "status": log.status,
                "processed": log.records_processed,
                "started": log.started_at.strftime("%H:%M:%S"),
                "duration": f"{(log.completed_at - log.started_at).seconds}s" if log.completed_at else "-"
            } for log in logs
        ]
    except Exception as e:
        return []
    finally:
        session.close()


def render():
    """Render the dashboard page content."""
    stats = get_dashboard_stats()
    gainers, losers = get_top_gainers_losers()
    activities = get_recent_activity()
    
    with ui.column().classes("w-full gap-6 p-6"):
        # Header / Greeting
        with ui.row().classes("w-full justify-between items-center"):
            with ui.column().classes("gap-1"):
                ui.label("System Dashboard").classes("text-3xl font-bold text-white")
                ui.label("Overview of market data sync, summaries, and quick metrics.").classes("text-sm text-slate-400")
            
            ui.button("Go to Sync Manager", icon="sync", on_click=lambda: ui.navigate.to("/download")) \
                .props('unelevated color=indigo') \
                .classes("px-4 py-2 text-white font-semibold rounded-lg")

        # 1. Metric Cards Row
        with ui.grid(columns=4).classes("w-full gap-4"):
            # Total Stocks Card
            with ui.card().classes("stat-card w-full"):
                ui.label("TOTAL STOCKS").classes("text-xs text-slate-400 font-bold tracking-wider")
                ui.label(str(stats["stocks"])).classes("text-4xl font-extrabold text-white mt-1")
                ui.label(f"{stats['indexes']} indexes seeded").classes("text-xs text-indigo-400 mt-2")
                
            # Total ETFs Card
            with ui.card().classes("stat-card w-full"):
                ui.label("TOTAL ETFS").classes("text-xs text-slate-400 font-bold tracking-wider")
                ui.label(str(stats["etfs"])).classes("text-4xl font-extrabold text-white mt-1")
                ui.label("Unified tracking active").classes("text-xs text-indigo-400 mt-2")
                
            # DB File Size Card
            with ui.card().classes("stat-card w-full"):
                ui.label("DATABASE SIZE").classes("text-xs text-slate-400 font-bold tracking-wider")
                ui.label(stats["db_size"]).classes("text-4xl font-extrabold text-white mt-1")
                ui.label("DuckDB Storage active").classes("text-xs text-indigo-400 mt-2")
                
            # Last Sync Date Card
            with ui.card().classes("stat-card w-full"):
                ui.label("LAST SYNC COMPLETED").classes("text-xs text-slate-400 font-bold tracking-wider")
                ui.label(stats["last_sync"]).classes("text-2xl font-extrabold text-white mt-2")
                ui.label("All tables reconciled").classes("text-xs text-indigo-400 mt-2")

        # 2. Gainers & Losers Tables Side by Side
        with ui.row().classes("w-full gap-6 wrap md:nowrap"):
            # Gainers Table
            with ui.card().classes("glass-card w-full md:w-1/2"):
                with ui.row().classes("items-center gap-2 mb-4"):
                    ui.icon("trending_up").classes("text-emerald-400 text-2xl")
                    ui.label("Top 5 Daily Gainers").classes("text-lg font-bold text-white")
                
                if gainers:
                    columns = [
                        {"headerName": "Symbol", "field": "symbol", "width": 100, "cellStyle": {"fontWeight": "bold", "color": "#818cf8"}},
                        {"headerName": "Close", "field": "close", "width": 110, "type": "numericColumn", "valueFormatter": "x => '₹' + Number(x.value).toFixed(2)"},
                        {"headerName": "Change", "field": "change_pct", "width": 110, "type": "numericColumn", "valueFormatter": "x => '+' + Number(x.value).toFixed(2) + '%'", "cellStyle": {"color": "#10b981", "fontWeight": "500"}}
                    ]
                    grid = ui.aggrid({
                        "columnDefs": columns,
                        "rowData": gainers,
                        "theme": "balham-dark",
                        "defaultColDef": {"sortable": True, "resizable": True}
                    }).classes("w-full h-[220px]")
                    grid.on("cellDoubleClicked", lambda e: ui.navigate.to(f"/stocks/{e.args['data']['symbol']}"))
                else:
                    ui.label("No data available. Run sync to load data.").classes("text-slate-500 py-8 text-center w-full")

            # Losers Table
            with ui.card().classes("glass-card w-full md:w-1/2"):
                with ui.row().classes("items-center gap-2 mb-4"):
                    ui.icon("trending_down").classes("text-red-400 text-2xl")
                    ui.label("Top 5 Daily Losers").classes("text-lg font-bold text-white")
                
                if losers:
                    columns = [
                        {"headerName": "Symbol", "field": "symbol", "width": 100, "cellStyle": {"fontWeight": "bold", "color": "#818cf8"}},
                        {"headerName": "Close", "field": "close", "width": 110, "type": "numericColumn", "valueFormatter": "x => '₹' + Number(x.value).toFixed(2)"},
                        {"headerName": "Change", "field": "change_pct", "width": 110, "type": "numericColumn", "valueFormatter": "x => Number(x.value).toFixed(2) + '%'", "cellStyle": {"color": "#ef4444", "fontWeight": "500"}}
                    ]
                    grid = ui.aggrid({
                        "columnDefs": columns,
                        "rowData": losers,
                        "theme": "balham-dark",
                        "defaultColDef": {"sortable": True, "resizable": True}
                    }).classes("w-full h-[220px]")
                    grid.on("cellDoubleClicked", lambda e: ui.navigate.to(f"/stocks/{e.args['data']['symbol']}"))
                else:
                    ui.label("No data available. Run sync to load data.").classes("text-slate-500 py-8 text-center w-full")

        # 3. Recent Sync Activity Card
        with ui.card().classes("glass-card w-full"):
            with ui.row().classes("items-center gap-2 mb-4"):
                ui.icon("history").classes("text-indigo-400 text-2xl")
                ui.label("Recent Sync Log Activity").classes("text-lg font-bold text-white")
                
            if activities:
                activity_cols = [
                    {"headerName": "Sync Type", "field": "type", "width": 180},
                    {"headerName": "Sync Date", "field": "date", "width": 120},
                    {"headerName": "Status", "field": "status", "width": 120, 
                     "cellStyle": "params => ({ color: params.value === 'SUCCESS' ? '#10b981' : params.value === 'STARTED' ? '#f59e0b' : '#ef4444', fontWeight: 'bold' })"},
                    {"headerName": "Records Processed", "field": "processed", "width": 160, "type": "numericColumn"},
                    {"headerName": "Started At", "field": "started", "width": 120},
                    {"headerName": "Duration", "field": "duration", "width": 100}
                ]
                ui.aggrid({
                    "columnDefs": activity_cols,
                    "rowData": activities,
                    "theme": "balham-dark",
                    "defaultColDef": {"sortable": True, "resizable": True}
                }).classes("w-full h-[250px]")
            else:
                ui.label("No sync history found. Go to Sync Manager to get started.").classes("text-slate-500 py-8 text-center w-full")
