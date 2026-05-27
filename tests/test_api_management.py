from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.api_management import (
    api_management_dir,
    api_management_status,
    load_api_management_defaults,
)


class ApiManagementTests(unittest.TestCase):
    def test_status_reports_configured_forks(self) -> None:
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

            status = api_management_status(root, timeout_seconds=0.01)

            self.assertEqual(api_management_dir(root), api_dir.resolve())
            self.assertTrue(status["ready_for_local_run"])
            self.assertFalse(status["all_services_reachable"])
            self.assertEqual(status["api_management_dir"], "api-management")
            self.assertEqual(status["defaults"]["default_provider"], "deepseek")
            self.assertEqual(status["defaults"]["default_model"], "deepseek-v4-pro")
            self.assertEqual(
                [service["path_exists"] for service in status["services"]],
                [True, True],
            )

    def test_status_handles_missing_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status = api_management_status(tmpdir, timeout_seconds=0.01)

            self.assertFalse(status["ready_for_local_run"])
            self.assertFalse(status["compose"]["compose_file_exists"])
            self.assertEqual(
                [service["path_exists"] for service in status["services"]],
                [False, False],
            )

    def test_load_defaults_handles_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            defaults = load_api_management_defaults(tmpdir)

            self.assertFalse(defaults["file_exists"])
            self.assertEqual(defaults["default_provider"], "")


if __name__ == "__main__":
    unittest.main()
