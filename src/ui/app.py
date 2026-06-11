from nicegui import ui, app
from .theme import apply_theme
from .layout import create_layout
from src.db.engine import SessionLocal, engine
from src.models import Security
from .pages import (
    dashboard,
    stocks,
    stock_detail,
    indexes,
    etfs,
    download,
    corporate_actions,
    symbol_changes,
    settings_page,
    screener
)


def create_app():
    """Create and configure the NiceGUI application."""
    apply_theme()

    # Define pages with routes
    @ui.page("/")
    def page_dashboard():
        create_layout(active="dashboard")
        dashboard.render()

    @ui.page("/stocks")
    def page_stocks():
        create_layout(active="stocks")
        stocks.render()

    @ui.page("/stocks/{symbol}")
    def page_stock_detail(symbol: str):
        session = SessionLocal()
        try:
            sec = session.query(Security).filter(Security.symbol == symbol).first()
            active_tab = sec.security_type.lower() + "s" if sec and sec.security_type in ["STOCK", "ETF", "INDEX"] else "stocks"
        except Exception:
            active_tab = "stocks"
        finally:
            session.close()

        create_layout(active=active_tab)
        stock_detail.render(symbol)

    @ui.page("/indexes")
    def page_indexes():
        create_layout(active="indexes")
        indexes.render()

    @ui.page("/etfs")
    def page_etfs():
        create_layout(active="etfs")
        etfs.render()

    @ui.page("/download")
    def page_download():
        create_layout(active="download")
        download.render()

    @ui.page("/corporate-actions")
    def page_corporate_actions():
        create_layout(active="corporate_actions")
        corporate_actions.render()

    @ui.page("/symbol-changes")
    def page_symbol_changes():
        create_layout(active="symbol_changes")
        symbol_changes.render()

    @ui.page("/settings")
    def page_settings():
        create_layout(active="settings")
        settings_page.render()

    @ui.page("/screener")
    def page_screener():
        create_layout(active="screener")
        screener.render()

    @app.on_shutdown
    def cleanup_db_connections():
        from loguru import logger
        logger.info("Application shutting down: Disposing database engine connections.")
        engine.dispose()



def main():
    """Application entry point."""
    import os
    from loguru import logger
    from config.settings import settings
    
    # Configure file logging for real-time console streaming
    os.makedirs(os.path.dirname(settings.log_file), exist_ok=True)
    logger.add(
        settings.log_file,
        level=settings.log_level,
        rotation="10 MB",
        retention="10 days",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"
    )
    
    # Run database integrity check on startup
    from src.ui.layout import check_db_integrity
    logger.info("Running startup database integrity check...")
    is_corrupt, err_msg = check_db_integrity()
    if is_corrupt:
        logger.warning(f"Database corruption or inconsistency detected on startup: {err_msg}")
        logger.info("Attempting automatic database recovery and index rebuild...")
        
        # Dispose the active engine connections to ensure no locks are held
        engine.dispose()
        
        from scripts.db_recovery import run_db_rebuild
        success = run_db_rebuild()
        if success:
            logger.info("Automatic database recovery completed successfully.")
        else:
            logger.error("Automatic database recovery failed. Please check the logs.")
    else:
        logger.info("Database integrity check passed.")
        
    create_app()
    
    run_kwargs = {
        "title": settings.app_title,
        "host": settings.app_host,
        "port": settings.app_port,
        "native": settings.app_native,  # True = desktop window, False = web browser
        "reload": False,
        "dark": settings.app_dark_mode,
    }
    
    # Only pass native window parameters if native mode is enabled
    if settings.app_native:
        run_kwargs["window_size"] = (1400, 900)
        
    ui.run(**run_kwargs)

