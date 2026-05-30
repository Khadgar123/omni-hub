"""Daily quant brief job (v0.49 quant scheduling seam).

build_quant_brief is fail-soft and CLI-shell-out is injected, so this is
venv-free + network-free.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "quant_daily_uut", _ROOT / "scripts" / "quant_daily.py"
    )
    m = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(m)
    return m


M = _load()


class QuantBriefTests(unittest.TestCase):
    def test_brief_with_bars(self) -> None:
        out = M.build_quant_brief(
            ["BTCUSDT"],
            fetch=lambda s: [{"close": 100.0}, {"close": 110.0}],
            today="2026-05-30",
        )
        self.assertIn("# Quant daily brief — 2026-05-30", out)
        self.assertIn("BTCUSDT", out)
        self.assertIn("close 110.0", out)
        self.assertIn("+10.0%", out)

    def test_fail_soft_on_missing_cli(self) -> None:
        def boom(s):
            raise FileNotFoundError("quant-market-store not on PATH")
        out = M.build_quant_brief(["BTCUSDT"], fetch=boom, today="2026-05-30")
        self.assertIn("unavailable", out)
        self.assertIn("FileNotFoundError", out)

    def test_no_bars(self) -> None:
        out = M.build_quant_brief(["X"], fetch=lambda s: [], today="2026-05-30")
        self.assertIn("no bars", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
