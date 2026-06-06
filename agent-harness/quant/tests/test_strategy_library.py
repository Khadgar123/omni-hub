"""tsmom / zscore_revert / divergence_reversal — each fires on a constructed case."""

from types import SimpleNamespace

from quant.strategy.base import gated_evaluate
from quant.strategy.divergence_reversal import DivergenceReversal
from quant.strategy.ma_cross import MACross
from quant.strategy.squeeze_breakout import SqueezeBreakout
from quant.strategy.tsmom import TSMomentum
from quant.strategy.zscore_revert import ZScoreRevert

_H = 3_600_000_000
_BASE = 1_704_067_200_000_000


def _bars(closes, band=0.5):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append({"bucket_ts": _BASE + i * _H, "open": prev, "high": max(prev, c) + band,
                    "low": min(prev, c) - band, "close": c, "volume": 1.0})
        prev = c
    return out


def _st(regime, bias, stand_down=False):
    return SimpleNamespace(symbol="BTCUSDT", regime_label=regime, composite_bias=bias, stand_down=stand_down)


def test_tsmom_long_in_uptrend():
    bars = _bars([100.0 * (1.005 ** i) for i in range(260)])
    intent = gated_evaluate(TSMomentum(), bars, _st("up", "long"), 0.0)
    assert intent is not None and intent.direction == "long"


def test_tsmom_no_entry_in_downtrend():
    bars = _bars([100.0 * (0.995 ** i) for i in range(260)])
    assert gated_evaluate(TSMomentum(), bars, _st("up", "long"), 0.0) is None


def test_zscore_long_on_deep_negative_z():
    closes = [100.0 + (0.3 if i % 2 else -0.3) for i in range(59)] + [96.0]
    intent = gated_evaluate(ZScoreRevert(), _bars(closes), _st("range", "flat"), 0.0)
    assert intent is not None and intent.direction == "long"


def test_zscore_exit_on_revert_to_mean():
    closes = [100.0 + (0.3 if i % 2 else -0.3) for i in range(59)] + [101.0]
    intent = gated_evaluate(ZScoreRevert(), _bars(closes), _st("range", "flat"), position_qty=1.0)
    assert intent is not None and intent.direction == "flat"


def _divergence_closes():
    closes = [100.0] * 60
    seg = [100, 98, 95, 92, 89, 87, 86, 88, 90, 92, 94, 95, 96, 96, 96]
    seg += [96] * (30 - len(seg))
    closes += seg[:30]
    p = 96.0
    for _ in range(30):
        p -= 11.0 / 30.0
        closes.append(round(p, 3))
    return closes


def test_divergence_reversal_fires_long():
    bars = _bars(_divergence_closes(), band=0.3)
    intent = gated_evaluate(DivergenceReversal(), bars, _st("down", "short"), 0.0)
    assert intent is not None and intent.direction == "long"
    assert intent.stop_price < intent.entry_ref


def test_divergence_blocked_by_stand_down():
    bars = _bars(_divergence_closes(), band=0.3)
    assert gated_evaluate(DivergenceReversal(), bars, _st("down", "short", stand_down=True), 0.0) is None


# --- ma cross ---------------------------------------------------------------

def test_ma_cross_long_in_uptrend():
    bars = _bars([100.0 * (1.004 ** i) for i in range(220)])
    intent = gated_evaluate(MACross(), bars, _st("up", "long"), 0.0)
    assert intent is not None and intent.direction == "long"


# --- squeeze breakout (中枢突破) -------------------------------------------

def test_squeeze_breakout_fires_on_break_after_squeeze():
    # 90 wide-oscillation bars (high BB width) -> 40 tight bars (squeeze) -> breakout
    closes = [100.0 + (2.5 if i % 2 else -2.5) for i in range(90)]
    closes += [100.0 + (0.15 if i % 2 else -0.15) for i in range(40)]
    closes += [106.0]
    intent = gated_evaluate(SqueezeBreakout(), _bars(closes, band=0.3), _st("range", "flat"), 0.0)
    assert intent is not None and intent.direction == "long"
    assert intent.stop_price < intent.entry_ref
