"""structure_reversal_v2 — registry, contract, op_atr fallback, and the harness
operating-level ATR injection (② level separation)."""

from types import SimpleNamespace

from quant.strategy import registry
from quant.strategy.base import LONG
from quant.strategy.structure_reversal_v2 import StructureReversalV2


def test_v2_registered_and_contract():
    s = registry.by_id("structure_reversal_v2")
    assert isinstance(s, StructureReversalV2) and s.timeframe == "1h"
    assert "range" in s.eligible_regimes and "strong_down" in s.eligible_regimes
    assert s.requires_bias is None


def test_v2_insufficient_bars():
    s = StructureReversalV2()
    bars = [{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1.0, "bucket_ts": i}
            for i in range(10)]
    assert s.evaluate(bars, SimpleNamespace(symbol="X", regime_label="range",
                                            composite_bias="long", stand_down=False), 0.0) is None


def test_v2_runs_without_op_atr_falls_back():
    # a state lacking op_atr must fall back to the entry-TF ATR (no crash)
    s = StructureReversalV2(require_range=False)
    bars = [{"open": 100 + (i % 5), "high": 102 + (i % 5), "low": 98 + (i % 5),
             "close": 100 + (i % 5), "volume": 1.0, "bucket_ts": i * 3_600_000_000} for i in range(90)]
    st = SimpleNamespace(symbol="X", regime_label="down", composite_bias="long", stand_down=False)
    out = s.evaluate(bars, st, 0.0)
    assert out is None or out.direction == LONG


def test_build_states_attaches_op_atr():
    from quant.backtest.harness import _build_states
    T = 3_600_000_000
    strat_bars = [{"bucket_ts": i * T} for i in range(3)]
    htf = [{"as_of": 0, "label": "range", "direction": "flat", "stand_down": False, "insufficient": False}]
    op = [(0, 150.0), (T, 175.0)]                              # operating-level ATR track
    states = _build_states(strat_bars, htf, htf, "BTCUSDT", op_atr_track=op)
    assert states[0].op_atr == 150.0                           # as-of ts=0
    assert states[2].op_atr == 175.0                           # as-of ts=2T -> latest (T,175)
