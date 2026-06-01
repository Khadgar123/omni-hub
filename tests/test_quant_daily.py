"""Daily crypto vol+trend reference indicator job (quant scheduling seam).

The brief/feed builders are fail-soft and the CLI shell-out is injected, so these
tests are venv-free + network-free.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

NOW = datetime(2026, 6, 2, tzinfo=UTC)
AS_OF_DT = datetime(2026, 5, 31, tzinfo=UTC)          # 2 days before NOW
AS_OF = int(AS_OF_DT.timestamp() * 1_000_000)


def _load():
    spec = importlib.util.spec_from_file_location(
        "quant_daily_uut", _ROOT / "scripts" / "quant_daily.py"
    )
    m = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(m)
    return m


M = _load()


def _market_state(bias="long", regime="range", direction="up", vol="low",
                  stand_down=False, adx=23.7):
    return {
        "symbol": "BTCUSDT", "as_of": AS_OF, "composite_bias": bias,
        "regime_label": regime, "direction": direction, "vol_bucket": vol,
        "stand_down": stand_down, "htf": {"adx": adx, "slope_per_atr": 0.076},
    }


class ToRecordTests(unittest.TestCase):
    def test_projects_and_stamps(self) -> None:
        rec = M._to_record(_market_state(), source="live:coinbase", now=NOW)
        self.assertEqual(rec["symbol"], "BTCUSDT")
        self.assertEqual(rec["source"], "live:coinbase")
        self.assertEqual(rec["stale_days"], 2.0)              # NOW - AS_OF
        self.assertEqual(rec["composite_bias"], "long")
        self.assertEqual(rec["regime_label"], "range")
        self.assertEqual(rec["vol_bucket"], "low")
        self.assertAlmostEqual(rec["adx"], 23.7)              # pulled from htf
        self.assertTrue(rec["as_of_utc"].startswith("2026-05-31"))

    def test_stale_days_none_when_no_asof(self) -> None:
        rec = M._to_record({"symbol": "X", "as_of": 0}, source="stored", now=NOW)
        self.assertIsNone(rec["stale_days"])


class FetchCascadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = M._run_market_state

    def tearDown(self) -> None:
        M._run_market_state = self._orig

    def test_live_first(self) -> None:
        M._run_market_state = lambda s, *, quant_py, live, venue, timeout=45.0: _market_state()
        rec = M._default_fetch("BTCUSDT", quant_py="/x/python", now=NOW)
        self.assertEqual(rec["source"], "live:binance")          # binance leads (CN-reachable)

    def test_falls_back_to_stored(self) -> None:
        calls = []

        def fake(s, *, quant_py, live, venue, timeout=45.0):
            calls.append((live, venue))
            if live:
                raise RuntimeError("venue blocked")
            return _market_state()

        M._run_market_state = fake
        rec = M._default_fetch("BTCUSDT", quant_py="/x/python", now=NOW)
        self.assertEqual(rec["source"], "stored")
        self.assertEqual(
            calls,
            [(True, "binance"), (True, "coinbase"), (True, "kraken"), (False, "coinbase")],
        )

    def test_all_fail_raises(self) -> None:
        def boom(s, *, quant_py, live, venue, timeout=45.0):
            raise RuntimeError("down")

        M._run_market_state = boom
        with self.assertRaises(RuntimeError):
            M._default_fetch("BTCUSDT", quant_py="/x/python", now=NOW)

    def test_no_quant_py_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            M._default_fetch("BTCUSDT", quant_py=None, now=NOW)


class CollectAndRenderTests(unittest.TestCase):
    def test_collect_fail_soft(self) -> None:
        def boom(s):
            raise RuntimeError("all sources failed")

        recs = M.collect_indicators(["BTCUSDT"], fetch=boom, now=NOW)
        self.assertIn("error", recs[0])
        self.assertIn("unavailable", M.render_brief(recs, today="2026-06-02"))

    def test_render_indicator_line(self) -> None:
        rec = M._to_record(_market_state(bias="long", adx=24), source="live:coinbase", now=NOW)
        out = M.render_brief([rec], today="2026-06-02")
        self.assertIn("Crypto vol+trend reference — 2026-06-02", out)
        self.assertIn("BTCUSDT", out)
        self.assertIn("long", out)
        self.assertIn("range", out)
        self.assertIn("ADX 24", out)
        self.assertIn("2d old", out)
        self.assertIn("非投资建议", out)              # disclaimer always present

    def test_render_standdown(self) -> None:
        rec = M._to_record(_market_state(stand_down=True), source="stored", now=NOW)
        self.assertIn("STAND-DOWN", M.render_brief([rec], today="2026-06-02"))


class WriteOutputsTests(unittest.TestCase):
    def test_writes_three_artifacts(self) -> None:
        good = M._to_record(_market_state(), source="live:coinbase", now=NOW)
        bad = {"symbol": "ETHUSDT", "error": "RuntimeError: down"}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            M.write_outputs([good, bad], root=root, today="2026-06-02", now=NOW)
            brief = root / ".omni" / "briefs" / "quant-2026-06-02.md"
            feed = root / ".omni" / "quant" / "regime-indicator.jsonl"
            latest = root / ".omni" / "quant" / "regime-latest.json"
            self.assertTrue(brief.exists())
            lines = feed.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)                   # one per symbol
            self.assertIn("run_ts", json.loads(lines[0]))
            snap = json.loads(latest.read_text(encoding="utf-8"))
            self.assertEqual(len(snap["indicators"]), 2)
            self.assertEqual(snap["date"], "2026-06-02")

    def test_feed_is_append_only(self) -> None:
        rec = M._to_record(_market_state(), source="stored", now=NOW)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            M.write_outputs([rec], root=root, today="2026-06-02", now=NOW)
            M.write_outputs([rec], root=root, today="2026-06-03", now=NOW)
            feed = root / ".omni" / "quant" / "regime-indicator.jsonl"
            self.assertEqual(len(feed.read_text(encoding="utf-8").strip().splitlines()), 2)


class QuantPyTests(unittest.TestCase):
    def test_env_override(self) -> None:
        old = os.environ.get("QUANT_PY")
        os.environ["QUANT_PY"] = sys.executable               # an interpreter that exists
        try:
            self.assertEqual(M._quant_py(), sys.executable)
        finally:
            if old is None:
                os.environ.pop("QUANT_PY", None)
            else:
                os.environ["QUANT_PY"] = old


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
