"""ccLoad BudgetReader tests (refactor step 10) — happy path via a stub
HTTP server, plus the load-bearing fail-open behavior."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from omni_hub.budget import BudgetReader, BudgetSnapshot


class _Handler(BaseHTTPRequestHandler):
    payload = b"{}"

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.__class__.payload)

    def log_message(self, *_a) -> None:  # silence
        pass


class BudgetReaderTests(unittest.TestCase):
    def _serve(self, body: dict) -> HTTPServer:
        _Handler.payload = json.dumps(body).encode("utf-8")
        srv = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def test_fail_open_when_gateway_unreachable(self) -> None:
        # nothing listening on port 1 -> connection refused -> fail open
        snap = BudgetReader(base_url="http://127.0.0.1:1", timeout=0.2).snapshot()
        self.assertFalse(snap.available)
        self.assertEqual(snap.token_used_microusd, 0)
        self.assertFalse(snap.over_soft_ceiling(0.5))  # unavailable is never over

    def test_parses_snapshot_and_units(self) -> None:
        srv = self._serve(
            {
                "token_used_microusd": 800_000,
                "token_limit_microusd": 1_000_000,
                "channel_daily_used_usd": 1.5,
                "channel_daily_limit_usd": 10.0,
            }
        )
        try:
            _, port = srv.server_address
            snap = BudgetReader(base_url=f"http://127.0.0.1:{port}", timeout=1.0).snapshot()
            self.assertTrue(snap.available)
            self.assertIsInstance(snap.token_used_microusd, int)  # microUSD int
            self.assertIsInstance(snap.channel_daily_used_usd, float)  # USD float
            self.assertEqual(snap.token_used_microusd, 800_000)
            self.assertAlmostEqual(snap.token_fraction(), 0.8)
            self.assertTrue(snap.over_soft_ceiling(0.75))
            self.assertFalse(snap.over_soft_ceiling(0.9))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_bad_json_is_fail_open(self) -> None:
        _Handler.payload = b"not json"
        srv = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            _, port = srv.server_address
            snap = BudgetReader(base_url=f"http://127.0.0.1:{port}", timeout=1.0).snapshot()
            self.assertFalse(snap.available)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_over_soft_ceiling_requires_available(self) -> None:
        snap = BudgetSnapshot(
            available=False, token_used_microusd=999, token_limit_microusd=1000
        )
        self.assertFalse(snap.over_soft_ceiling(0.1))


if __name__ == "__main__":
    unittest.main()
