"""HR #3 coverage for the gated quant sub-instance
(``agent-harness/quant/market_store.py``).

The quant store is a separate sub-package with its own deps (duckdb /
pyarrow); the main repo stays stdlib-only.  We test ONLY the dependency-
free pure core — ``partition_path`` (the Hive layout that makes
``(symbol, date)`` range scans fast) and ``bars_from_trades`` (the
'trades are the TRUTH, OHLCV bars are DERIVED' step).  The Parquet/DuckDB
I/O is lazy-imported and intentionally not exercised here.

Loaded by file path via importlib so the test needs no PYTHONPATH change
and no third-party deps.
"""

import importlib.util
import unittest
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "agent-harness" / "quant" / "market_store.py"
)


def _load_market_store():
    spec = importlib.util.spec_from_file_location("quant_market_store", _MODULE_PATH)
    assert spec and spec.loader, _MODULE_PATH
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MarketStoreCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ms = _load_market_store()

    def test_partition_path_is_hive_symbol_date(self) -> None:
        p = self.ms.partition_path("/data", "bars_1d", "NVDA", "2026-05-29")
        self.assertEqual(
            p, Path("/data/bars_1d/symbol=NVDA/date=2026-05-29/part.parquet")
        )

    def test_bars_from_trades_aggregates_ohlcv(self) -> None:
        trades = [
            {"ts": 0, "price": 10.0, "size": 1},
            {"ts": 30, "price": 11.0, "size": 2},
            {"ts": 61, "price": 9.0, "size": 1},
        ]
        bars = self.ms.bars_from_trades(trades, interval_seconds=60)
        self.assertEqual(len(bars), 2)
        self.assertEqual(
            bars[0],
            {"bucket": 0, "open": 10.0, "high": 11.0, "low": 10.0,
             "close": 11.0, "volume": 3, "trades": 2},
        )
        self.assertEqual(bars[1]["bucket"], 60)
        self.assertEqual(bars[1]["open"], 9.0)

    def test_bars_sorted_and_unordered_input(self) -> None:
        # out-of-order input must still bucket + sort correctly (OHLC integrity)
        trades = [
            {"ts": 125, "price": 5.0, "size": 1},
            {"ts": 5, "price": 7.0, "size": 1},
            {"ts": 65, "price": 6.0, "size": 1},
        ]
        bars = self.ms.bars_from_trades(trades, interval_seconds=60)
        self.assertEqual([b["bucket"] for b in bars], [0, 60, 120])

    def test_empty(self) -> None:
        self.assertEqual(self.ms.bars_from_trades([]), [])


if __name__ == "__main__":
    unittest.main()
