from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.secrets import has_secret, resolve_secret_ref, store_api_key


class SecretStoreTests(unittest.TestCase):
    def test_memory_backend_uses_keychain_ref_without_losing_value(self) -> None:
        with patch.dict(os.environ, {"OMNI_HUB_SECRET_BACKEND": "memory"}):
            secret_ref = store_api_key("openai-main", "sk-test-secret")

            self.assertEqual(secret_ref, "keychain:omni-hub/openai-main")
            self.assertEqual(resolve_secret_ref(secret_ref), "sk-test-secret")
            self.assertTrue(has_secret(secret_ref))

    def test_local_backend_uses_ignored_file_ref_for_cross_platform(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            secret_file = Path(tmpdir) / "secrets.json"
            with patch.dict(
                os.environ,
                {
                    "OMNI_HUB_SECRET_BACKEND": "local",
                    "OMNI_HUB_SECRET_FILE": str(secret_file),
                },
            ):
                secret_ref = store_api_key("openai-main", "sk-local-secret")

                self.assertEqual(secret_ref, "local:omni-hub/openai-main")
                self.assertEqual(resolve_secret_ref(secret_ref), "sk-local-secret")
                self.assertTrue(has_secret(secret_ref))
                self.assertIn("sk-local-secret", secret_file.read_text())


if __name__ == "__main__":
    unittest.main()
