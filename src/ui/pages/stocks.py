from sqlalchemy import select, func
from nicegui import ui

from src.db.engine import SessionLocal
from src.models import Security, RawPrice, MarketCap
from src.ui.components.stock_table import create_stock_table


def get_stocks_list():
    """Fetch all stocks with their latest close price and market cap."""
    session = SessionLocal()
    try:
        # Get latest trading date
        latest_date = session.query(func.max(RawPrice.trade_date)).scalar()
        
        # Select stocks with outer joins to get latest price and market cap if available
        query = (
            select(
                Security.symbol,
                Security.company_name,
                Security.security_type,
                Security.isin,
                RawPrice.close,
                MarketCap.market_cap
            )
            .outerjoin(RawPrice, (Security.id == RawPrice.security_id) & (RawPrice.trade_date == latest_date))
            .outerjoin(MarketCap, (Security.id == MarketCap.security_id) & (MarketCap.trade_date == latest_date))
            .where(Security.security_type == 'STOCK')
            .order_by(Security.symbol.asc())
        )
        
        results = session.execute(query).all()
        
        data = []
        for r in results:
            mcap_cr = round(float(r.market_cap) / 10000000.0, 2) if r.market_cap else None
            data.append({
                "symbol": r.symbol,
                "company_name": r.company_name or "-",
                "security_type": r.security_type,
                "isin": r.isin or "-",
                "close": float(r.close) if r.close else None,
                "market_cap": mcap_cr
            })
        return data
    except Exception as e:
        return []
    finally:
        session.close()


def render():
    """Render the stocks directory page."""
    stocks_data = get_stocks_list()
    
    with ui.column().classes("w-full gap-6 p-6"):
        # Page Title
        with ui.column().classes("gap-1"):
            ui.label("Stocks Directory").classes("text-3xl font-bold text-white")
            ui.label("List of all auto-discovered equities (EQ & BE series) from NSE bhavcopies.").classes("text-sm text-slate-400")
            
        # Stocks Table Component
        if stocks_data:
            create_stock_table(stocks_data)
        else:
            with ui.card().classes("glass-card w-full p-8 text-center items-center"):
                ui.icon("warning", size="lg").classes("text-amber-500 mb-2")
                ui.label("No Stocks Data Found").classes("text-lg font-bold text-white")
                ui.label("Please run the Sync Downloader to populate the database with NSE bhavcopy files.").classes("text-sm text-slate-400 mt-1")
                ui.button("Go to Download Manager", on_click=lambda: ui.navigate.to("/download")).classes("mt-4 text-white bg-indigo-500")
