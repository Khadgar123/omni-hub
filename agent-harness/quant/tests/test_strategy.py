"""Strategy layer: sizing, the regime gate, and the two Phase-1 strategies."""

from types import SimpleNamespace

import pytest

from quant.strategy import sizing
from quant.strategy.base import gated_evaluate
from quant.strategy.range_bb_revert import RangeBBRevert
from quant.strategy.trend_donchian import TrendDonchian

_H = 3_600_000_000  # 1h in µs


def _bars(closes, *, band=0.5):
    bars, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        bars.append({"bucket_ts": i * _H, "open": o, "high": max(o, c) + band,
                     "low": min(o, c) - band, "close": c, "volume": 1.0})
        prev = c
    return bars


def _state(regime="up", bias="long", stand_down=False, symbol="BTCUSDT"):
    return SimpleNamespace(symbol=symbol, regime_label=regime, composite_bias=bias, stand_down=stand_down)


# --- sizing -----------------------------------------------------------------

def test_size_qty_risk_leg_dominates():
    # 1% of 10000 = 100 risk; stop 100 away -> 1.0 unit; but kelly cap 0.125*equity/entry
    # = 0.125*10000/1000=1.25; min=1.0; conviction 1.0; under max cap (0.25*10000/1000=2.5)
    q = sizing.size_qty(equity=10000, entry=1000, stop=900, conviction=1.0)
    assert q == pytest.approx(1.0)


def test_size_qty_conviction_scales_down():
    full = sizing.size_qty(equity=10000, entry=1000, stop=900, conviction=1.0)
    half = sizing.size_qty(equity=10000, entry=1000, stop=900, conviction=0.5)
    assert half == pytest.approx(full * 0.5)


def test_size_qty_kelly_leg_binds_when_stop_tiny():
    # tiny stop -> risk leg huge; the quarter-Kelly leg (0.25*0.5=12.5% of equity) binds
    q = sizing.size_qty(equity=10000, entry=1000, stop=999.9, conviction=1.0)
    assert q == pytest.approx(10000 * 0.125 / 1000)  # 1.25 units


def test_size_qty_hard_position_cap_with_high_edge():
    # a high edge pushes Kelly past the 25% hard cap -> the hard cap binds
    q = sizing.size_qty(equity=10000, entry=1000, stop=999.9, conviction=1.0, edge_estimate=2.0)
    assert q == pytest.approx(10000 * 0.25 / 1000)  # 2.5 units


def test_size_qty_zero_stop_distance_is_safe():
    assert sizing.size_qty(equity=10000, entry=1000, stop=1000) == 0.0


# --- regime gate ------------------------------------------------------------

def _breakout_bars():
    return _bars([100.0] * 40 + [110.0])  # 41 bars, clean breakout above the 20-high


def test_trend_entry_passes_gate_in_uptrend():
    intent = gated_evaluate(TrendDonchian(), _breakout_bars(), _state("up", "long"), 0.0)
    assert intent is not None and intent.direction == "long"
    assert intent.stop_price < intent.entry_ref
    assert intent.conviction == pytest.approx(0.6)


def test_trend_entry_blocked_by_stand_down():
    assert gated_evaluate(TrendDonchian(), _breakout_bars(), _state("up", "long", stand_down=True), 0.0) is None


def test_trend_entry_blocked_by_wrong_regime():
    assert gated_evaluate(TrendDonchian(), _breakout_bars(), _state("range", "flat"), 0.0) is None


def test_trend_entry_blocked_by_bias_mismatch():
    # 4h veto -> composite_bias flat -> trend (requires_bias long) must not fire
    assert gated_evaluate(TrendDonchian(), _breakout_bars(), _state("up", "flat"), 0.0) is None


def test_trend_exit_passes_gate_regardless_of_regime():
    bars = _bars([100.0 + i for i in range(30)] + [108.0])  # rising then break below 10-low
    intent = gated_evaluate(TrendDonchian(), bars, _state("range", "flat"), position_qty=1.0)
    assert intent is not None and intent.direction == "flat"  # exits never gated out


# --- range mean-reversion ---------------------------------------------------

def test_range_entry_on_oversold_dip():
    bars = _bars([100.0] * 25 + [97.0, 94.0, 90.0, 86.0, 82.0, 78.0])
    intent = gated_evaluate(RangeBBRevert(), bars, _state("range", "flat"), 0.0)
    assert intent is not None and intent.direction == "long"
    assert intent.stop_price < intent.entry_ref


def test_range_entry_blocked_outside_range_regime():
    bars = _bars([100.0] * 25 + [97.0, 94.0, 90.0, 86.0, 82.0, 78.0])
    assert gated_evaluate(RangeBBRevert(), bars, _state("up", "long"), 0.0) is None


def test_range_exit_at_mid_band():
    bars = _bars([98.0, 102.0] * 15 + [103.0])  # mean ~100, last >= mid -> exit
    intent = gated_evaluate(RangeBBRevert(), bars, _state("range", "flat"), position_qty=1.0)
    assert intent is not None and intent.direction == "flat"
