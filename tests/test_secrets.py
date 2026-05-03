from pathlib import Path
import os
import sys
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


if __name__ == "__main__":
    unittest.main()
