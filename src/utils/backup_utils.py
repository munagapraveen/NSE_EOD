import os
import shutil
import glob
from datetime import datetime
from loguru import logger
from config.settings import settings

def get_db_file_path() -> str:
    """Helper to extract the physical database file path from settings."""
    db_url = settings.database_url
    if db_url.startswith("duckdb:///"):
        return db_url.replace("duckdb:///", "")
    return ""

def create_db_backup() -> str:
    """
    Creates a timestamped backup of the current database.
    Returns the path to the backup file, or an empty string if failed.
    """
    db_path = get_db_file_path()
    if not db_path:
        logger.warning("Database URL is not a local DuckDB file. Skipping backup.")
        return ""

    if not os.path.exists(db_path):
        logger.warning(f"Database file does not exist at {db_path}. Skipping backup.")
        return ""

    # Ensure backups directory exists
    backup_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(db_path))), "backups")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_filename = f"market_backup_{timestamp}.db"

    backup_path = os.path.join(backup_dir, backup_filename)

    try:
        # Proactively dispose engine to release file locks on Windows (WinError 32)
        from src.db.engine import engine as _engine
        _engine.dispose()

        # Since DuckDB file copy is safe when there are no active writers,
        # copy the database file.
        shutil.copy2(db_path, backup_path)
        logger.info(f"Successfully created database backup: {backup_path}")
        return backup_path
    except Exception as e:
        logger.error(f"Failed to create database backup: {e}")
        return ""

def prune_old_backups(keep_count: int = 5):
    """
    Prunes older database backups, retaining only the most recent 'keep_count' backups.
    """
    db_path = get_db_file_path()
    if not db_path:
        return

    backup_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(db_path))), "backups")
    if not os.path.exists(backup_dir):
        return

    pattern = os.path.join(backup_dir, "market_backup_*.db")
    backup_files = glob.glob(pattern)

    # Sort files by name/timestamp (since filename format naturally sorts chronologically)
    backup_files.sort()

    if len(backup_files) <= keep_count:
        return

    to_remove = backup_files[:-keep_count]
    for file_path in to_remove:
        try:
            os.remove(file_path)
            logger.info(f"Pruned old backup file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to remove old backup {file_path}: {e}")

def restore_db_from_backup(backup_path: str) -> bool:
    """
    Restores the active database from a specified backup file.
    Disposes of the engine connections before replacing the file to prevent locking.
    """
    if not os.path.exists(backup_path):
        logger.error(f"Backup file does not exist: {backup_path}")
        return False

    db_path = get_db_file_path()
    if not db_path:
        logger.error("Could not determine database path for restoration.")
        return False

    # Dispose of engine to close connections
    from src.db.engine import engine
    logger.info("Disposing engine connections for database restore...")
    engine.dispose()

    try:
        # If active DB exists, move it to a temp path first for safety
        temp_backup = db_path + ".restore_temp"
        if os.path.exists(db_path):
            if os.path.exists(temp_backup):
                os.remove(temp_backup)
            os.rename(db_path, temp_backup)

        try:
            # Copy backup to active database path
            shutil.copy2(backup_path, db_path)
            logger.info(f"Successfully restored database from {backup_path}")
            
            # Clean up temp backup
            if os.path.exists(temp_backup):
                os.remove(temp_backup)
            return True
        except Exception as copy_err:
            # Rollback active DB if copy failed
            logger.error(f"Copy failed during restore: {copy_err}. Restoring original file.")
            if os.path.exists(temp_backup):
                if os.path.exists(db_path):
                    os.remove(db_path)
                os.rename(temp_backup, db_path)
            raise copy_err

    except Exception as e:
        logger.error(f"Failed to restore database from backup: {e}")
        return False
