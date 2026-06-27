from nicegui import ui


def create_stock_table(stocks_data: list[dict]):
    """
    Create an AG Grid table for stock/ETF/Index listing.
    
    Args:
        stocks_data: List of dicts containing keys: symbol, company_name, security_type, close, market_cap, isin
    """
    
    columns = [
        {"headerName": "Symbol", "field": "symbol", "width": 120,
         "filter": "agTextColumnFilter", "pinned": "left",
         "cellStyle": {"fontWeight": "bold", "color": "#818cf8"},
         "sort": "asc"},
        {"headerName": "Company Name", "field": "company_name", "width": 280,
         "filter": "agTextColumnFilter"},
        {"headerName": "Type", "field": "security_type", "width": 100,
         "filter": "agTextColumnFilter"},
        {"headerName": "ISIN", "field": "isin", "width": 150,
         "filter": "agTextColumnFilter"},
        {"headerName": "Close Price", "field": "close", "width": 130,
         "type": "numericColumn",
         "valueFormatter": "x => x.value ? '₹' + Number(x.value).toFixed(2) : '-'"},
        {"headerName": "Market Cap (Cr)", "field": "market_cap", "width": 160,
         "type": "numericColumn",
         "valueFormatter": "x => x.value ? '₹' + Number(x.value).toLocaleString('en-IN') : '-'"},
    ]
    
    with ui.column().classes("w-full gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            # Search Bar
            search_input = ui.input(placeholder="Filter table...") \
                .props('outlined dense dark') \
                .classes("w-72 bg-[#2a2a3e] rounded-lg")
                
            # CSV Export button
            export_btn = ui.button("Export to CSV", icon="download") \
                .props('outline dark') \
                .classes("text-indigo-400 border-indigo-400/30")
        
        # AG Grid Table
        grid = ui.aggrid({
            "columnDefs": columns,
            "rowData": stocks_data,
            "rowSelection": "single",
            "theme": "balham",
            "pagination": True,
            "paginationPageSize": 15,
            "defaultColDef": {
                "sortable": True,
                "resizable": True,
                "filter": True
            }
        }).classes("w-full h-[550px] border border-white/10 rounded-lg")
        
        # Wire events
        search_input.on("value-change", lambda e: grid.run_grid_method("setGridOption", "quickFilterText", e.value))
        export_btn.on("click", lambda: grid.run_grid_method("exportDataAsCsv"))
        grid.on("cellDoubleClicked", lambda e: ui.navigate.to(f"/stocks/{e.args['data']['symbol']}"))
        
    return grid
