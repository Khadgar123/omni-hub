"""level_analytics: reversal detection, regime classification, CIs, full-sweep structure (no network)."""

import math

from quant import level_analytics as la
from quant.features import atr


def _bars(closes, wick=1.0):
    return [{"open": c, "high": c + wick, "low": c - wick, "close": c, "volume": 100.0,
             "bucket_ts": i * 3600 * 1_000_000} for i, c in enumerate(closes)]


def test_find_reversals_detects_significant_top():
    closes = [100 + i for i in range(21)] + [120 - 4 * (i + 1) for i in range(20)]   # rise to 120, drop hard
    bars = _bars(closes)
    a = list(atr(bars, 14))
    revs = la.find_reversals(bars, atr_series=a, min_move_atr=1.0, horizon=10)
    assert any(k == "high" for (_, _, k) in revs)                       # the top that led to a drop is flagged


def test_regime_at_up_down_range():
    assert la.regime_at([100 + i for i in range(100)], 90, 2.0, window=30) == "up"
    assert la.regime_at([100 - i for i in range(100)], 90, 2.0, window=30) == "down"
    assert la.regime_at([100.0] * 100, 90, 2.0, window=30) == "range"


def test_wilson_ci_bounds():
    lo, hi = la.wilson_ci(5, 10)
    assert 0 <= lo < 0.5 < hi <= 1
    assert la.wilson_ci(0, 0) == (0.0, 0.0)
    lo2, hi2 = la.wilson_ci(10, 10)
    assert hi2 == 1.0 or hi2 <= 1.0                                      # never exceeds 1


def test_reversal_clustering_returns_coverage_and_base():
    closes = [100 + 12 * math.sin(i / 6.0) + 0.02 * i for i in range(600)]
    bars = _bars(closes)
    a = list(atr(bars, 14))
    r = la.reversal_clustering(bars, atr_series=a, level_window=200, controls=5)
    assert set(r) >= {"n", "coverage", "base", "lift", "mult", "cov_ci", "base_ci"}
    assert 0 <= r["coverage"] <= 1 and 0 <= r["base"] <= 1


def test_run_structure_with_injected_fetch():
    closes = [100 + 10 * math.sin(i / 5.0) + 0.05 * i for i in range(600)]
    bars = _bars(closes)

    def fetch(sym, tf, *, limit):
        return bars[:limit]
    rows = la.run(["X"], ["1h"], fetch=fetch, limit=600)
    assert len(rows) == 1 and rows[0]["symbol"] == "X"
    assert "clustering" in rows[0] and rows[0]["clustering"]
    assert set(rows[0]["regime_edge"]) == {"up", "down", "range"}        # all three regimes bucketed
    assert "analytics" in la.render(rows)                                # renders without error
