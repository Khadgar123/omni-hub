import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "agent-harness"
    / "integrations"
    / "finance"
    / "binance_spot_live.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("binance_spot_live", MODULE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class BinanceSpotLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_sign_params_uses_hmac_sha256(self):
        out = self.mod.sign_params(
            {"symbol": "BTCUSDT", "timestamp": 123456789},
            "secret",
        )
        self.assertIn("symbol=BTCUSDT", out)
        self.assertIn("timestamp=123456789", out)
        self.assertIn(
            "signature=24bb009b09520f76b5ed14737fb17bb02b9e96224f5df51d32f23dbee72c8194",
            out,
        )

    def test_mask_secret(self):
        self.assertEqual(self.mod.mask_secret(""), "")
        self.assertEqual(self.mod.mask_secret("abcd"), "****")
        self.assertEqual(self.mod.mask_secret("abcdefghijkl"), "abcd...ijkl")

    def test_validate_credentials_shape(self):
        creds = self.mod.BinanceCredentials(api_key="abc def", api_secret="")
        issues = self.mod.validate_credentials_shape(creds)
        self.assertIn("API secret is empty", issues)
        self.assertIn("API key contains whitespace", issues)

    def test_parser_has_ip_command(self):
        parser = self.mod.build_parser()
        with self.assertRaises(SystemExit) as raised:
            parser.parse_args(["ip", "--bad-arg"])
        self.assertNotEqual(raised.exception.code, 0)

    def test_summarize_account_hides_balances(self):
        account = {
            "canTrade": True,
            "accountType": "SPOT",
            "permissions": ["SPOT"],
            "balances": [
                {"asset": "BTC", "free": "0.1", "locked": "0"},
                {"asset": "ETH", "free": "0", "locked": "0"},
                {"asset": "USDT", "free": "0", "locked": "5"},
            ],
        }
        summary = self.mod.summarize_account(account)
        self.assertEqual(summary["nonzero_asset_count"], 2)
        self.assertEqual(summary["nonzero_assets"], ["BTC", "USDT"])
        self.assertNotIn("balances", summary)


if __name__ == "__main__":
    unittest.main()
