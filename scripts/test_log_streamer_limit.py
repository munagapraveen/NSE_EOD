import asyncio
import os
import sys
import unittest
import tempfile
from pathlib import Path

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ui.pages.download import LogStreamer
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

class TestLogStreamerLimit(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_file_path = Path(self.temp_dir.name) / "test_app.log"
        
        # Save original log_file setting
        self.original_log_file = settings.log_file
        settings.log_file = self.log_file_path

        # Write initial log line
        with open(self.log_file_path, "w", encoding="utf-8") as f:
            f.write("Initial log line\n")

    def tearDown(self):
        settings.log_file = self.original_log_file
        self.temp_dir.cleanup()

    def test_log_streamer_limits_lines(self):
        log_area = MockLogArea()
        
        # Instantiate LogStreamer with max_lines=5
        streamer = LogStreamer(log_area, max_lines=5)

        async def run_test_sequence():
            # Start in background task
            task = asyncio.create_task(streamer.start())
            
            # Wait for streamer to seek to end
            await asyncio.sleep(0.1)
            
            # Write 8 new lines to the log file (exceeds max_lines=5)
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                for i in range(1, 9):
                    f.write(f"Log line {i}\n")
                    f.flush()
                    await asyncio.sleep(0.02)  # Give loop a brief moment to pick up

            # Wait for streamer to process all lines
            await asyncio.sleep(0.6)
            streamer.stop()
            await task

        asyncio.run(run_test_sequence())

        # Check that the text area contains exactly the last 5 lines, not all 8
        lines = log_area.value.strip().split("\n")
        self.assertEqual(len(lines), 5)
        self.assertEqual(lines, [
            "Log line 4",
            "Log line 5",
            "Log line 6",
            "Log line 7",
            "Log line 8"
        ])
        
        # Check that run_javascript was called on the client for scrolling
        self.assertTrue(len(log_area.client.javascript_called) > 0)
        self.assertTrue(any("scrollTop = ta.scrollHeight" in code for code in log_area.client.javascript_called))

if __name__ == "__main__":
    unittest.main()
