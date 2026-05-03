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

    def test_skill_register_and_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            register_buffer = StringIO()
            list_buffer = StringIO()
            try:
                os.chdir(tmpdir)
                with redirect_stdout(register_buffer):
                    register_exit = main(
                        [
                            "skill-register",
                            "--id",
                            "url-capture",
                            "--name",
                            "URL Capture",
                            "--kind",
                            "connector",
                            "--description",
                            "Capture HTTP pages into the inbox.",
                            "--entrypoint",
                            "operation:capture_url",
                            "--risk",
                            "L1",
                            "--connector",
                            "web",
                            "--tag",
                            "capture",
                        ]
                    )
                with redirect_stdout(list_buffer):
                    list_exit = main(["skill-list", "--kind", "connector"])
            finally:
                os.chdir(original_cwd)

            self.assertEqual(register_exit, 0)
            self.assertEqual(list_exit, 0)
            self.assertTrue((Path(tmpdir) / "registry/skills.json").exists())
            self.assertTrue((Path(tmpdir) / "vault/30_Skills/url-capture.md").exists())
            payload = json.loads(list_buffer.getvalue())
            self.assertEqual(payload["output"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
