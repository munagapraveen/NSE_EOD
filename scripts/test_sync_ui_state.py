import asyncio
import os
import sys
import unittest
import tempfile
from pathlib import Path
from datetime import date

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ui.pages.download import LogStreamer
from src.services.sync_manager import SyncManager
from src.services.nse_client import NSEClient
from config.settings import settings

class MockClient:
    def __init__(self):
        self.javascript_called = []

    def run_javascript(self, code, timeout=1.0):
        self.javascript_called.append(code)
        class AwaitableMock:
            def __await__(self):
                async def mock_wait():
                    return None
                return mock_wait().__await__()
        return AwaitableMock()

class MockLogArea:
    def __init__(self):
        self.value = ""
        self.id = 123
        self.client = MockClient()


class TestSyncUIState(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_file_path = Path(self.temp_dir.name) / "test_app_sync.log"
        
        # Save original log_file setting
        self.original_log_file = settings.log_file
        settings.log_file = self.log_file_path

        # Write 250 initial log lines to test pre-population limit (should keep last 200)
        with open(self.log_file_path, "w", encoding="utf-8") as f:
            for i in range(1, 251):
                f.write(f"Pre-existing line {i}\n")
            f.flush()

    def tearDown(self):
        settings.log_file = self.original_log_file
        self.temp_dir.cleanup()

    def test_log_streamer_pre_populates_last_200_lines(self):
        log_area = MockLogArea()
        streamer = LogStreamer(log_area, max_lines=1000)

        async def run_test():
            task = asyncio.create_task(streamer.start())
            await asyncio.sleep(0.2)  # Wait for startup and read
            streamer.stop()
            await task

        asyncio.run(run_test())

        # Check that it pre-populated exactly the last 200 lines
        lines = log_area.value.strip().split("\n")
        self.assertEqual(len(lines), 200)
        self.assertEqual(lines[0], "Pre-existing line 51")
        self.assertEqual(lines[-1], "Pre-existing line 250")

    def test_log_streamer_batch_reading(self):
        log_area = MockLogArea()
        streamer = LogStreamer(log_area, max_lines=1000)

        async def run_test():
            # Start streamer
            task = asyncio.create_task(streamer.start())
            await asyncio.sleep(0.2)
            
            # Reset javascript calls count
            log_area.client.javascript_called.clear()
            
            # Write 50 new lines rapidly
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                for i in range(1, 51):
                    f.write(f"New line {i}\n")
                f.flush()
                
            await asyncio.sleep(0.5)  # Wait for streamer to read them
            streamer.stop()
            await task

        asyncio.run(run_test())

        # Since they were written at the same time and the log streamer sleeps for 0.3s,
        # it should process them in a single batch, meaning run_javascript is called once or twice,
        # rather than 50 times!
        js_call_count = len(log_area.client.javascript_called)
        self.assertTrue(js_call_count >= 1)
        self.assertTrue(js_call_count <= 5, f"Expected few JS calls due to batching, but got {js_call_count}")

        # Check that new lines are in log area
        self.assertTrue("New line 50" in log_area.value)

    def test_sync_manager_holds_and_forwards_progress(self):
        # Instantiate a mock client and SyncManager
        nse_client = NSEClient()
        manager = SyncManager(nse_client)
        
        # Initially progress properties are default
        self.assertEqual(manager.current_stage, "Initializing")
        self.assertEqual(manager.current_progress, 0.0)
        self.assertEqual(manager.current_message, "Initializing Ingestion pipeline...")
        
        # Test report_progress updating properties
        manager.report_progress("TEST_STAGE", 45.0, "Testing progress forwarding")
        self.assertEqual(manager.current_stage, "TEST_STAGE")
        self.assertEqual(manager.current_progress, 45.0)
        self.assertEqual(manager.current_message, "Testing progress forwarding")
        
        # Register a callback
        callback_called = []
        def my_callback(stage, percentage, msg):
            callback_called.append((stage, percentage, msg))
            
        manager.progress_callback = my_callback
        manager.report_progress("STAGE_2", 60.0, "Message 2")
        
        self.assertEqual(len(callback_called), 1)
        self.assertEqual(callback_called[0], ("STAGE_2", 60.0, "Message 2"))


if __name__ == "__main__":
    unittest.main()
