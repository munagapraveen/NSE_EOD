from pydantic_settings import BaseSettings
from pydantic import Field
from datetime import date, time
from pathlib import Path


class Settings(BaseSettings):
    # Database Configuration
    database_url: str = "duckdb:///data/market.db"

    # NSE Configurations
    nse_start_date: date = date(2024, 1, 1)
    nse_request_delay_seconds: float = 3.0
    nse_session_refresh_minutes: int = 5
    nse_max_retries: int = 3

    # App Configurations
    app_title: str = "NSE EOD Data Manager"
    app_host: str = "127.0.0.1"
    app_port: int = 8080
    app_native: bool = True
    app_dark_mode: bool = True

    # Logging Configuration
    log_level: str = "INFO"
    log_file: Path = Path("data/logs/app.log")

    # Scheduling Configuration
    auto_sync_enabled: bool = False
    auto_sync_time: time = time(18, 30)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore"
    }


settings = Settings()
