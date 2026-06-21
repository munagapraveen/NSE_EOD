import os
import sys
import unittest
from datetime import date

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ui.pages.settings_page import save_env_settings
from config.settings import settings

class TestSettingsPreservation(unittest.TestCase):

    def setUp(self):
        self.env_path = ".env"
        self.backup_path = ".env.test_backup"
        
        # 1. Backup existing .env file if it exists
        if os.path.exists(self.env_path):
            os.rename(self.env_path, self.backup_path)
            
        # 2. Write a dummy .env file with custom settings
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("DATABASE_URL=duckdb:///data/original.db\n")
            f.write("NSE_START_DATE=2024-01-01\n")
            f.write("LOG_FILE=data/logs/custom.log\n")
            f.write("AUTO_SYNC_ENABLED=TRUE\n")
            f.write("AUTO_SYNC_TIME=18:30\n")

    def tearDown(self):
        # 1. Delete test .env file
        if os.path.exists(self.env_path):
            os.remove(self.env_path)
            
        # 2. Restore backup .env file if it exists
        if os.path.exists(self.backup_path):
            os.rename(self.backup_path, self.env_path)

    def test_save_env_settings_preserves_custom_vars(self):
        # Run save_env_settings with new values
        success = save_env_settings(
            db_url="duckdb:///data/new.db",
            start_date_str="2025-06-01",
            delay=4.0,
            native=False,
            dark=False
        )
        
        self.assertTrue(success)
        
        # Read the newly written .env file
        vars_dict = {}
        with open(self.env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vars_dict[k.strip()] = v.strip()
                    
        # Verify updated settings
        self.assertEqual(vars_dict["DATABASE_URL"], "duckdb:///data/new.db")
        self.assertEqual(vars_dict["NSE_START_DATE"], "2025-06-01")
        self.assertEqual(vars_dict["NSE_REQUEST_DELAY_SECONDS"], "4.0")
        self.assertEqual(vars_dict["APP_NATIVE"], "FALSE")
        self.assertEqual(vars_dict["APP_DARK_MODE"], "FALSE")
        
        # Verify PRESERVED custom settings
        self.assertEqual(vars_dict["LOG_FILE"], "data/logs/custom.log")
        self.assertEqual(vars_dict["AUTO_SYNC_ENABLED"], "TRUE")
        self.assertEqual(vars_dict["AUTO_SYNC_TIME"], "18:30")

if __name__ == "__main__":
    unittest.main()
