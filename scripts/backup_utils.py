import os
import shutil
from datetime import datetime

def backup_file(filepath: str, backup_dir: str = "backups"):
    """
    Back up a file to the specified backup directory with a timestamped suffix.
    """
    if not os.path.exists(filepath):
        print(f"File {filepath} does not exist. Skipping backup.")
        return None
    
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
        
    filename = os.path.basename(filepath)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"{filename}.{timestamp}.bak"
    backup_filepath = os.path.join(backup_dir, backup_filename)
    
    shutil.copy2(filepath, backup_filepath)
    print(f"Backed up {filepath} -> {backup_filepath}")
    return backup_filepath

def rollback_file(filepath: str, backup_filepath: str):
    """
    Roll back a file from a specific backup file.
    """
    if not os.path.exists(backup_filepath):
        print(f"Backup file {backup_filepath} does not exist.")
        return False
        
    shutil.copy2(backup_filepath, filepath)
    print(f"Rolled back {filepath} from {backup_filepath}")
    return True
