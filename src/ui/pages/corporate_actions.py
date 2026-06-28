from sqlalchemy import select
from nicegui import ui
from loguru import logger

from src.db.engine import SessionLocal
from src.models import Security, CorporateAction


def get_corporate_actions():
    """Fetch all parsed corporate actions from database."""
    session = SessionLocal()
    try:
        query = (
            select(
                Security.symbol,
                Security.company_name,
                CorporateAction.action_type,
                CorporateAction.ex_date,
                CorporateAction.record_date,
                CorporateAction.description,
                CorporateAction.old_face_value,
                CorporateAction.new_face_value,
                CorporateAction.bonus_ratio_new,
                CorporateAction.bonus_ratio_existing,
                CorporateAction.adjustment_factor
            )
            .join(CorporateAction, Security.id == CorporateAction.security_id)
            .order_by(CorporateAction.ex_date.desc())
        )
        
        results = session.execute(query).all()
        
        data = []
        for r in results:
            ex_date_str = r.ex_date.strftime("%d-%b-%Y") if r.ex_date else "-"
            rec_date_str = r.record_date.strftime("%d-%b-%Y") if r.record_date else "-"
            
            # Formulate detail string
            if r.action_type == "SPLIT":
                old_fv = "-"
                new_fv = "-"
                if r.old_face_value is not None:
                    fv_float = float(r.old_face_value)
                    old_fv = int(fv_float) if fv_float.is_integer() else fv_float
                if r.new_face_value is not None:
                    fv_float = float(r.new_face_value)
                    new_fv = int(fv_float) if fv_float.is_integer() else fv_float
                detail = f"Split {old_fv} → {new_fv} (Factor: {float(r.adjustment_factor):.1f})"
            else:
                detail = f"Bonus {r.bonus_ratio_new}:{r.bonus_ratio_existing} (Factor: {float(r.adjustment_factor):.1f})"
                
            data.append({
                "symbol": r.symbol,
                "company_name": r.company_name or "-",
                "action_type": r.action_type,
                "ex_date": ex_date_str,
                "record_date": rec_date_str,
                "detail": detail,
                "description": r.description or "-"
            })
        return data
    except Exception as e:
        logger.exception(f"Failed to fetch corporate actions: {e}")
        return []
    finally:
        session.close()


def render():
    """Render the corporate actions logs page."""
    actions_data = get_corporate_actions()
    
    with ui.column().classes("w-full gap-6 p-6"):
        # Page Title
        with ui.column().classes("gap-1"):
            ui.label("Corporate Actions Log").classes("text-3xl font-bold text-white")
            ui.label("Historical splits and bonuses synchronized from the official NSE API.").classes("text-sm text-slate-400")
            
        if actions_data:
            columns = [
                {"headerName": "Symbol", "field": "symbol", "width": 110, "cellStyle": {"fontWeight": "bold", "color": "#818cf8"}, "filter": "agTextColumnFilter"},
                {"headerName": "Company Name", "field": "company_name", "width": 250, "filter": "agTextColumnFilter"},
                {"headerName": "Action Type", "field": "action_type", "width": 120, "filter": "agTextColumnFilter",
                 ":cellStyle": "params => ({ color: params.value === 'SPLIT' ? '#f59e0b' : '#6366f1', fontWeight: 'bold' })"},
                {"headerName": "Ex-Date", "field": "ex_date", "width": 120},
                {"headerName": "Record Date", "field": "record_date", "width": 120},
                {"headerName": "Detail Ratio", "field": "detail", "width": 180},
                {"headerName": "Description", "field": "description", "width": 300}
            ]
            
            ui.aggrid({
                "columnDefs": columns,
                "rowData": actions_data,
                "theme": "balham",
                "pagination": True,
                "paginationPageSize": 15,
                "defaultColDef": {"sortable": True, "resizable": True, "filter": True}
            }).classes("w-full h-[580px] border border-white/10 rounded-lg")
            
        else:
            with ui.card().classes("glass-card w-full p-8 text-center items-center"):
                ui.icon("warning", size="lg").classes("text-amber-500 mb-2")
                ui.label("No Corporate Actions Found").classes("text-lg font-bold text-white")
                ui.label("Please run the Sync Downloader to synchronize and parse corporate action documents.").classes("text-sm text-slate-400 mt-1")
                ui.button("Go to Download Manager", on_click=lambda: ui.navigate.to("/download")).classes("mt-4 text-white bg-indigo-500")
