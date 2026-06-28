import os
import shutil
import glob
import subprocess
from datetime import datetime
from loguru import logger
from config.settings import settings
from sqlalchemy.engine import make_url
from src.utils.date_utils import get_now_ist

def get_db_file_path() -> str:
    """Helper to extract the physical database file path from settings."""
    db_url = settings.database_url
    if db_url.startswith("duckdb:///"):
        return db_url.replace("duckdb:///", "")
    return ""

def create_db_backup() -> str:
    """
    Creates a timestamped backup of the current database (DuckDB or PostgreSQL).
    Returns the path to the backup file, or an empty string if failed.
    """
    db_url = settings.database_url
    if db_url.startswith("duckdb:///"):
        db_path = get_db_file_path()
        if not db_path:
            return ""
        if not os.path.exists(db_path):
            logger.warning(f"Database file does not exist at {db_path}. Skipping backup.")
            return ""

        # Ensure backups directory exists
        backup_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(db_path))), "backups")
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = get_now_ist().strftime("%Y%m%d_%H%M%S_%f")
        backup_filename = f"market_backup_{timestamp}.db"
        backup_path = os.path.join(backup_dir, backup_filename)

        try:
            from src.db.engine import engine as _engine
            _engine.dispose()
            shutil.copy2(db_path, backup_path)
            logger.info(f"Successfully created database backup: {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"Failed to create database backup: {e}")
            return ""

    elif "postgresql" in db_url:
        try:
            url = make_url(db_url)
            backup_dir = os.path.join(os.getcwd(), "backups")
            os.makedirs(backup_dir, exist_ok=True)

            timestamp = get_now_ist().strftime("%Y%m%d_%H%M%S_%f")
            backup_filename = f"market_backup_{timestamp}.dump"
            backup_path = os.path.join(backup_dir, backup_filename)

            env = os.environ.copy()
            if url.password:
                env["PGPASSWORD"] = url.password

            cmd = [
                "pg_dump",
                "-h", url.host or "localhost",
                "-p", str(url.port or 5432),
                "-U", url.username or "postgres",
                "-F", "c",  # custom format
                "-b",       # large objects
                "-v",
                "-f", backup_path,
                url.database
            ]

            try:
                res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
                if res.returncode == 0:
                    logger.info(f"Successfully created PostgreSQL database backup: {backup_path}")
                    return backup_path
                else:
                    logger.error(f"pg_dump failed with error: {res.stderr}")
            except subprocess.TimeoutExpired:
                logger.error("pg_dump timed out after 600 seconds.")
            except FileNotFoundError:
                # Search standard Windows installation paths for pg_dump.exe
                pg_paths = glob.glob(r"C:\Program Files\PostgreSQL\*\bin\pg_dump.exe")
                if pg_paths:
                    pg_paths.sort()
                    pg_dump_exe = pg_paths[-1]
                    cmd[0] = pg_dump_exe
                    try:
                        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
                        if res.returncode == 0:
                            logger.info(f"Successfully created PostgreSQL database backup using path: {backup_path}")
                            return backup_path
                        else:
                            logger.error(f"pg_dump path failed with error: {res.stderr}")
                    except subprocess.TimeoutExpired:
                        logger.error("pg_dump timed out after 600 seconds during fallback execution.")
                else:
                    logger.error("pg_dump utility not found in PATH or standard installation directories.")
            return ""
        except Exception as e:
            logger.error(f"Failed to create PostgreSQL database backup: {e}")
            return ""
    else:
        logger.info("Unsupported database connection for backups.")
        return ""

def prune_old_backups(keep_count: int = 5):
    """
    Prunes older database backups, retaining only the most recent 'keep_count' backups.
    """
    backup_dir = os.path.join(os.getcwd(), "backups")
    if not os.path.exists(backup_dir):
        return

    # Find both .db (DuckDB) and .dump (PostgreSQL) backup files
    pattern_db = os.path.join(backup_dir, "market_backup_*.db")
    pattern_dump = os.path.join(backup_dir, "market_backup_*.dump")
    backup_files = glob.glob(pattern_db) + glob.glob(pattern_dump)

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
    """
    if not os.path.exists(backup_path):
        logger.error(f"Backup file does not exist: {backup_path}")
        return False

    db_url = settings.database_url
    if db_url.startswith("duckdb:///"):
        db_path = get_db_file_path()
        if not db_path:
            return False

        from src.db.engine import engine
        logger.info("Disposing engine connections for database restore...")
        engine.dispose()

        try:
            temp_backup = db_path + ".restore_temp"
            if os.path.exists(db_path):
                if os.path.exists(temp_backup):
                    os.remove(temp_backup)
                os.rename(db_path, temp_backup)

            try:
                shutil.copy2(backup_path, db_path)
                logger.info(f"Successfully restored database from {backup_path}")
                if os.path.exists(temp_backup):
                    os.remove(temp_backup)
                return True
            except Exception as copy_err:
                logger.error(f"Copy failed during restore: {copy_err}. Restoring original file.")
                if os.path.exists(temp_backup):
                    if os.path.exists(db_path):
                        os.remove(db_path)
                    os.rename(temp_backup, db_path)
                raise copy_err
        except Exception as e:
            logger.error(f"Failed to restore database from backup: {e}")
            return False

    elif "postgresql" in db_url:
        try:
            url = make_url(db_url)
            from src.db.engine import engine
            logger.info("Disposing active connections for Postgres restore...")
            engine.dispose()

            env = os.environ.copy()
            if url.password:
                env["PGPASSWORD"] = url.password

            cmd = [
                "pg_restore",
                "-h", url.host or "localhost",
                "-p", str(url.port or 5432),
                "-U", url.username or "postgres",
                "-d", url.database,
                "-c",       # clean (drop) database objects before recreating
                "-v",
                backup_path
            ]

            try:
                res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
                if res.returncode in (0, 1):  # pg_restore returncode 1 is warning which is acceptable
                    logger.info(f"Successfully restored PostgreSQL database from: {backup_path}")
                    return True
                else:
                    logger.error(f"pg_restore failed with error: {res.stderr}")
                    return False
            except subprocess.TimeoutExpired:
                logger.error("pg_restore timed out after 600 seconds.")
                return False
            except FileNotFoundError:
                # Search standard Windows installation paths for pg_restore.exe
                pg_paths = glob.glob(r"C:\Program Files\PostgreSQL\*\bin\pg_restore.exe")
                if pg_paths:
                    pg_paths.sort()
                    pg_restore_exe = pg_paths[-1]
                    cmd[0] = pg_restore_exe
                    try:
                        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
                        if res.returncode in (0, 1):
                            logger.info(f"Successfully restored PostgreSQL database using path from: {backup_path}")
                            return True
                        else:
                            logger.error(f"pg_restore path failed with error: {res.stderr}")
                    except subprocess.TimeoutExpired:
                        logger.error("pg_restore timed out after 600 seconds during fallback execution.")
                else:
                    logger.error("pg_restore utility not found in PATH or standard installation directories.")
                return False
        except Exception as e:
            logger.error(f"Failed to restore PostgreSQL database: {e}")
            return False
    else:
        logger.info("Unsupported database connection for restoration.")
        return False
