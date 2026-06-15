from sqlalchemy import select, func
from nicegui import ui

from src.db.engine import SessionLocal
from src.models import Security, RawPrice
from src.ui.components.stock_table import create_stock_table


def get_etfs_list():
    """Fetch all ETFs with their latest close price."""
    session = SessionLocal()
    try:
        # Get latest trading date
        latest_date = session.query(func.max(RawPrice.trade_date)).scalar()
        
        # Select ETFs with outer joins to get latest price
        query = (
            select(
                Security.symbol,
                Security.company_name,
                Security.security_type,
                Security.isin,
                RawPrice.close
            )
            .outerjoin(RawPrice, (Security.id == RawPrice.security_id) & (RawPrice.trade_date == latest_date))
            .where(Security.security_type == 'ETF')
            .order_by(Security.symbol.asc())
        )
        
        results = session.execute(query).all()
        
        data = []
        for r in results:
            data.append({
                "symbol": r.symbol,
                "company_name": r.company_name or "-",
                "security_type": r.security_type,
                "isin": r.isin or "-",
                "close": float(r.close) if r.close else None,
                "market_cap": None  # ETFs do not have market cap
            })
        return data
    except Exception:
        return []
    finally:
        session.close()


def render():
    """Render the ETFs directory page."""
    etfs_data = get_etfs_list()
    
    with ui.column().classes("w-full gap-6 p-6"):
        # Page Title
        with ui.column().classes("gap-1"):
            ui.label("ETFs Directory").classes("text-3xl font-bold text-white")
            ui.label("List of all registered Exchange Traded Funds (ETFs) from the NSE ETF master list.").classes("text-sm text-slate-400")
            
        # Stocks Table Component
        if etfs_data:
            create_stock_table(etfs_data)
        else:
            with ui.card().classes("glass-card w-full p-8 text-center items-center"):
                ui.icon("warning", size="lg").classes("text-amber-500 mb-2")
                ui.label("No ETFs Data Found").classes("text-lg font-bold text-white")
                ui.label("Please run the Sync Downloader to populate the database with NSE bhavcopy files.").classes("text-sm text-slate-400 mt-1")
                ui.button("Go to Download Manager", on_click=lambda: ui.navigate.to("/download")).classes("mt-4 text-white bg-indigo-500")
