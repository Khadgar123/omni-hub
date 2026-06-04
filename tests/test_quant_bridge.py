"""quant_bridge: stdlib subprocess seam to the quant market-data CLI (no quant install needed).

Mocks subprocess so these run in the stdlib-only core env, asserting the (payload, status)
degrade contract that callers rely on.
"""

import json
import unittest
from unittest import mock

from omni_hub.connectors import quant_bridge


def _proc(stdout="", returncode=0):
    m = mock.Mock()
    m.stdout = stdout
    m.returncode = returncode
    return m


class QuantBridgeTest(unittest.TestCase):
    def test_market_bars_ok(self):
        rows = [{"bucket_ts": 1, "close": 100.0}, {"bucket_ts": 2, "close": 101.0}]
        with mock.patch.object(quant_bridge, "has_quant", return_value=True), \
             mock.patch("subprocess.run", return_value=_proc(json.dumps(rows))) as run:
            got, status = quant_bridge.market_bars("BTCUSDT", "1d", "2026-06-01", "2026-06-04")
        self.assertEqual(status, "ok")
        self.assertEqual(got, rows)
        # global --format json goes BEFORE the subcommand
        argv = run.call_args.args[0]
        self.assertIn("--format", argv)
        self.assertLess(argv.index("--format"), argv.index("bars"))

    def test_not_installed_degrades_quietly(self):
        with mock.patch.object(quant_bridge, "has_quant", return_value=False):
            got, status = quant_bridge.market_bars("BTCUSDT", "1d", "a", "b")
        self.assertEqual((got, status), ([], "not_installed"))

    def test_bad_json_is_error(self):
        with mock.patch.object(quant_bridge, "has_quant", return_value=True), \
             mock.patch("subprocess.run", return_value=_proc("not-json")):
            got, status = quant_bridge.market_bars("BTCUSDT", "1d", "a", "b")
        self.assertEqual((got, status), ([], "error"))

    def test_nonzero_exit_is_error(self):
        with mock.patch.object(quant_bridge, "has_quant", return_value=True), \
             mock.patch("subprocess.run", return_value=_proc("", returncode=2)):
            got, status = quant_bridge.market_bars("BTCUSDT", "1d", "a", "b")
        self.assertEqual((got, status), ([], "error"))

    def test_timeout_degrades(self):
        import subprocess
        with mock.patch.object(quant_bridge, "has_quant", return_value=True), \
             mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1)):
            got, status = quant_bridge.market_bars("BTCUSDT", "1d", "a", "b")
        self.assertEqual((got, status), ([], "timeout"))

    def test_last_price_unwraps_single_row(self):
        with mock.patch.object(quant_bridge, "has_quant", return_value=True), \
             mock.patch("subprocess.run", return_value=_proc(json.dumps([{"last_price": 63000.0}]))):
            got, status = quant_bridge.last_price("BTCUSDT", "2026-06-04")
        self.assertEqual(status, "ok")
        self.assertEqual(got["last_price"], 63000.0)


if __name__ == "__main__":
    unittest.main()
