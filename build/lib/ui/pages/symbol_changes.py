from sqlalchemy import select
from nicegui import ui

from src.db.engine import SessionLocal
from src.models import Security, SymbolChange


def get_symbol_changes():
    """Fetch all applied symbol changes from database."""
    session = SessionLocal()
    try:
        query = (
            select(
                Security.symbol,
                Security.company_name,
                SymbolChange.old_symbol,
                SymbolChange.new_symbol,
                SymbolChange.effective_date,
                SymbolChange.applied_at
            )
            .join(SymbolChange, Security.id == SymbolChange.security_id)
            .order_by(SymbolChange.applied_at.desc())
        )
        
        results = session.execute(query).all()
        
        data = []
        for r in results:
            eff_date_str = r.effective_date.strftime("%d-%b-%Y") if r.effective_date else "-"
            applied_at_str = r.applied_at.strftime("%d-%b-%Y %H:%M") if r.applied_at else "-"
            
            data.append({
                "symbol": r.symbol,
                "company_name": r.company_name or "-",
                "old_symbol": r.old_symbol,
                "new_symbol": r.new_symbol,
                "effective_date": eff_date_str,
                "applied_at": applied_at_str
            })
        return data
    except Exception as e:
        return []
    finally:
        session.close()


def render():
    """Render the symbol changes logs page."""
    changes_data = get_symbol_changes()
    
    with ui.column().classes("w-full gap-6 p-6"):
        # Page Title
        with ui.column().classes("gap-1"):
            ui.label("Symbol Changes Log").classes("text-3xl font-bold text-white")
            ui.label("Automatic ticker rename mappings synced and propagated to the database tables.").classes("text-sm text-slate-400")
            
        if changes_data:
            columns = [
                {"headerName": "Current Symbol", "field": "symbol", "width": 140, "cellStyle": {"fontWeight": "bold", "color": "#818cf8"}, "filter": "agTextColumnFilter"},
                {"headerName": "Company Name", "field": "company_name", "width": 260, "filter": "agTextColumnFilter"},
                {"headerName": "Old Symbol", "field": "old_symbol", "width": 130, "filter": "agTextColumnFilter", "cellStyle": {"color": "#ef4444"}},
                {"headerName": "New Symbol", "field": "new_symbol", "width": 130, "filter": "agTextColumnFilter", "cellStyle": {"color": "#10b981", "fontWeight": "bold"}},
                {"headerName": "NSE Effective Date", "field": "effective_date", "width": 150},
                {"headerName": "Applied In DB", "field": "applied_at", "width": 180}
            ]
            
            ui.aggrid({
                "columnDefs": columns,
                "rowData": changes_data,
                "theme": "balham-dark",
                "pagination": True,
                "paginationPageSize": 15,
                "defaultColDef": {"sortable": True, "resizable": True, "filter": True}
            }).classes("w-full h-[580px] border border-white/10 rounded-lg")
            
        else:
            with ui.card().classes("glass-card w-full p-8 text-center items-center"):
                ui.icon("warning", size="lg").classes("text-amber-500 mb-2")
                ui.label("No Rename Logs Found").classes("text-lg font-bold text-white")
                ui.label("Please run the Sync Downloader to retrieve the symbol rename logs from NSE.").classes("text-sm text-slate-400 mt-1")
                ui.button("Go to Download Manager", on_click=lambda: ui.navigate.to("/download")).classes("mt-4 text-white bg-indigo-500")
