from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.cli import main


class CliTests(unittest.TestCase):
    def test_capture_url_no_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            buffer = StringIO()
            try:
                os.chdir(tmpdir)
                with redirect_stdout(buffer):
                    exit_code = main(
                        [
                            "capture-url",
                            "--url",
                            "https://youtu.be/dQw4w9WgXcQ",
                            "--no-fetch",
                        ]
                    )
            finally:
                os.chdir(original_cwd)

            self.assertEqual(exit_code, 0)
            payload = json.loads(buffer.getvalue())
            self.assertEqual(payload["status"], "succeeded")
            markdown_path = Path(tmpdir) / payload["output"]["markdown_path"]
            self.assertTrue(markdown_path.exists())

    def test_memory_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            buffer = StringIO()
            try:
                os.chdir(tmpdir)
                with redirect_stdout(buffer):
                    exit_code = main(["memory-stats"])
            finally:
                os.chdir(original_cwd)

            self.assertEqual(exit_code, 0)
            payload = json.loads(buffer.getvalue())
            self.assertEqual(payload["status"], "succeeded")
            self.assertEqual(payload["output"]["documents"], 0)
            self.assertFalse((Path(tmpdir) / ".omni" / "memory.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
