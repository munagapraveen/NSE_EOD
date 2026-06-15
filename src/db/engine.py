import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config.settings import settings

# Parse the database URL to see if it's DuckDB and ensure directories exist
db_url = settings.database_url

if db_url.startswith("duckdb:///"):
    # Extract file path
    db_file_path = db_url.replace("duckdb:///", "")
    # If the file path contains directories, ensure they exist
    if "/" in db_file_path or "\\" in db_file_path:
        db_dir = os.path.dirname(db_file_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

# Create engine
engine = create_engine(db_url, echo=False)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Dependency for retrieving database session context."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
