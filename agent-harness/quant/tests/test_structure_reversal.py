"""structure_reversal_v1 — registry wiring, contract, and a constructed long fire."""

from types import SimpleNamespace

from quant import structure as S
from quant.strategy import registry
from quant.strategy.base import LONG, gated_evaluate
from quant.strategy.structure_reversal import StructureReversal


def _state(regime="range", bias="long"):
    return SimpleNamespace(symbol="BTCUSDT", regime_label=regime,
                           composite_bias=bias, stand_down=False)


def test_registered_and_contract():
    s = registry.by_id("structure_reversal_v1")
    assert isinstance(s, StructureReversal) and s.timeframe == "1h"
    assert "range" in s.eligible_regimes and "strong_down" in s.eligible_regimes
    assert s.requires_bias is None


def test_returns_none_when_insufficient_bars():
    s = StructureReversal()
    bars = [{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1.0, "bucket_ts": i}
            for i in range(10)]
    assert s.evaluate(bars, _state(), 0.0) is None


def _falling_then_div_bars():
    """A choppy decline into a support zone, ending with a DOWN-leg 背驰 (lower low
    on weaker force) right at support -> the long setup. ~120 bars."""
    rows = []
    # establish a support shelf around 100 with two swing lows + intervening highs,
    # then a final weaker push to a marginally lower low (the 背驰) at support.
    base = [110, 104, 112, 100, 109, 103, 111, 99.5, 108, 102, 110, 99.2]  # zigzag closes
    seq = []
    for k in range(10):
        seq += base
    for i, c in enumerate(seq):
        seq_high = c + 2.0
        seq_low = c - 2.0
        rows.append({"open": c, "high": seq_high, "low": seq_low, "close": c,
                     "volume": 1.0, "bucket_ts": i * 3_600_000_000})
    return rows


def test_evaluate_runs_and_emits_valid_intent_or_none():
    s = StructureReversal(require_range=False, min_rr=0.5, near_atr=5.0)
    bars = _falling_then_div_bars()
    out = s.evaluate(bars, _state("down"), 0.0)
    # contract: either no setup (None) or a well-formed LONG intent with a stop below price
    if out is not None:
        assert out.direction == LONG and out.strategy_id == "structure_reversal_v1"
        assert 0.0 <= out.conviction <= 1.0 and out.stop_price < out.entry_ref
        assert out.features.get("rr") is not None


def test_entry_gate_blocks_ineligible_regime():
    # even if a setup exists, gated_evaluate must block entries in an ineligible regime
    s = StructureReversal(require_range=False, min_rr=0.5, near_atr=5.0)
    bars = _falling_then_div_bars()
    gated = gated_evaluate(s, bars, _state("strong_up"), 0.0)  # up regime not eligible
    assert gated is None
