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

    def test_skill_recommend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            register_buffer = StringIO()
            recommend_buffer = StringIO()
            try:
                os.chdir(tmpdir)
                with redirect_stdout(register_buffer):
                    main(
                        [
                            "skill-register",
                            "--id",
                            "memory-search",
                            "--name",
                            "Memory Search",
                            "--kind",
                            "memory",
                            "--description",
                            "Search canonical local memory.",
                            "--entrypoint",
                            "operation:search_memory",
                            "--risk",
                            "L0",
                            "--tag",
                            "memory",
                        ]
                    )
                with redirect_stdout(recommend_buffer):
                    exit_code = main(
                        ["skill-recommend", "--query", "search memory", "--limit", "3"]
                    )
            finally:
                os.chdir(original_cwd)

            self.assertEqual(exit_code, 0)
            payload = json.loads(recommend_buffer.getvalue())
            self.assertEqual(payload["output"]["count"], 1)
            self.assertEqual(
                payload["output"]["recommendations"][0]["skill_id"],
                "memory-search",
            )

    def test_api_management_status_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            api_dir = root / "api-management"
            (api_dir / "metapi" / ".git").mkdir(parents=True)
            (api_dir / "ccLoad" / ".git").mkdir(parents=True)
            (api_dir / "compose.yml").write_text("services: {}\n", encoding="utf-8")
            (api_dir / "compose.build.yml").write_text(
                "services: {}\n",
                encoding="utf-8",
            )
            (api_dir / "env.example").write_text(
                "METAPI_AUTH_TOKEN=change-me\n",
                encoding="utf-8",
            )
            (api_dir / "defaults.json").write_text(
                """{
  "version": 1,
  "default_project": "*",
  "default_provider": "deepseek",
  "default_model": "deepseek-v4-pro",
  "providers": {
    "deepseek": {
      "secret_ref": "local:omni-hub/api/deepseek/default"
    }
  }
}
""",
                encoding="utf-8",
            )

            original_cwd = os.getcwd()
            buffer = StringIO()
            try:
                os.chdir(tmpdir)
                with redirect_stdout(buffer):
                    exit_code = main(
                        ["api-management-status", "--timeout-seconds", "0.01"]
                    )
            finally:
                os.chdir(original_cwd)

            self.assertEqual(exit_code, 0)
            payload = json.loads(buffer.getvalue())
            output = payload["output"]
            self.assertEqual(payload["status"], "succeeded")
            self.assertTrue(output["ready_for_local_run"])
            self.assertFalse(output["all_services_reachable"])
            self.assertEqual(output["defaults"]["default_provider"], "deepseek")
            self.assertEqual(
                [service["id"] for service in output["services"]],
                ["metapi", "ccload"],
            )


if __name__ == "__main__":
    unittest.main()
