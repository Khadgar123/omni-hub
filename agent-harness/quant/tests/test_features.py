"""Point-in-time correctness + numeric sanity for quant.features.

Pure-core math (no store/deps); conftest puts the package dir on sys.path.
"""

import pytest

from quant import features


def _bars(ohlc):
    """ohlc: list of (open, high, low, close) -> bar dicts."""
    return [
        {"open": o, "high": h, "low": low, "close": c, "volume": 1.0}
        for (o, h, low, c) in ohlc
    ]


# --- moving averages -------------------------------------------------------

def test_sma_warmup_and_values():
    assert features.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]


def test_ema_seeds_with_sma_and_is_causal():
    out = features.ema([2, 4, 6], 3)
    assert out[0] is None and out[1] is None
    assert out[2] == pytest.approx(4.0)  # seed = mean(2,4,6)


def test_ema_constant_series_is_flat():
    out = features.ema([5, 5, 5, 5, 5], 3)
    assert all(v == pytest.approx(5.0) for v in out[2:])


def test_slope_propagates_none_then_measures():
    assert features.slope([None, None, 1.0, 2.0, 3.0], 1) == [None, None, None, 1.0, 1.0]


# --- RSI --------------------------------------------------------------------

def test_rsi_all_up_is_100():
    out = features.rsi(list(range(1, 30)), 14)
    assert out[14] == pytest.approx(100.0)


def test_rsi_all_down_is_0():
    out = features.rsi(list(range(30, 1, -1)), 14)
    assert out[14] == pytest.approx(0.0)


def test_rsi_flat_is_50():
    out = features.rsi([5.0] * 30, 14)
    assert out[14] == pytest.approx(50.0)


# --- true range / ATR -------------------------------------------------------

def test_true_range_first_is_none():
    bars = _bars([(10, 11, 9, 10), (10, 12, 10, 11)])
    tr = features.true_range(bars)
    assert tr[0] is None
    # max(12-10, |12-10|, |10-10|) = 2
    assert tr[1] == pytest.approx(2.0)


def test_atr_warmup_and_positive():
    bars = _bars([(i, i + 2, i - 1, i + 1) for i in range(1, 40)])
    a = features.atr(bars, 14)
    assert a[13] is None and a[14] is not None
    assert a[-1] > 0


# --- ADX --------------------------------------------------------------------

def test_adx_high_in_trend_low_in_chop():
    n = 14
    trend = _bars([(i, i + 2, i, i + 1.8) for i in range(1, 70)])  # monotone up
    chop = _bars(
        [(100, 101, 99, 100.5) if i % 2 == 0 else (100.5, 101, 99, 100) for i in range(70)]
    )
    adx_trend = features.last_valid(features.adx(trend, n)["adx"])
    adx_chop = features.last_valid(features.adx(chop, n)["adx"])
    assert adx_trend is not None and adx_chop is not None
    assert adx_trend > 25          # sustained move => strong trend reading
    assert adx_trend > adx_chop    # and clearly stronger than the choppy band


def test_adx_plus_di_dominates_in_uptrend():
    res = features.adx(_bars([(i, i + 2, i, i + 1.8) for i in range(1, 70)]), 14)
    assert features.last_valid(res["plus_di"]) > features.last_valid(res["minus_di"])


# --- Bollinger / ROC / realized vol ----------------------------------------

def test_bollinger_width_zero_on_constant():
    out = features.bollinger([5.0] * 25, 20, 2.0)
    assert out["width"][-1] == pytest.approx(0.0)


def test_bollinger_width_positive_when_varying():
    out = features.bollinger([5.0 + (i % 5) for i in range(40)], 20, 2.0)
    assert out["width"][-1] > 0


def test_roc():
    assert features.roc([10, 11], 1)[1] == pytest.approx(0.1)
    assert features.roc([10, 20], 1)[1] == pytest.approx(1.0)


def test_realized_vol_zero_on_constant_positive_when_varying():
    assert features.realized_vol([5.0] * 30, 20)[-1] == pytest.approx(0.0)
    varying = [100.0 * (1.0 + 0.01 * ((-1) ** i)) for i in range(30)]
    assert features.realized_vol(varying, 20)[-1] > 0


def test_last_valid():
    assert features.last_valid([None, 1.0, None, 3.0]) == 3.0
    assert features.last_valid([None, None]) is None
