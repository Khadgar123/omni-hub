"""Point-in-time correctness + numeric sanity for quant.features.

Pure-core math (no store/deps); conftest puts the package dir on sys.path.
"""

import random

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


# --- candle geometry (continuous KBar ratios, NOT named patterns) ----------

def test_candle_geometry_known_proportions():
    g = features.candle_geometry(_bars([(100, 110, 90, 105)]))
    assert g["body"][0] == pytest.approx(0.05)        # (105-100)/100
    assert g["rng"][0] == pytest.approx(0.20)         # (110-90)/100
    assert g["body_pct"][0] == pytest.approx(0.25)    # 5/20
    assert g["up_shadow"][0] == pytest.approx(0.25)   # (110-105)/20
    assert g["dn_shadow"][0] == pytest.approx(0.50)   # (100-90)/20
    assert g["close_loc"][0] == pytest.approx(0.50)   # (210-200)/20


def test_candle_geometry_bearish_sign_and_zero_range():
    g = features.candle_geometry(_bars([(105, 106, 99, 100), (100, 100, 100, 100)]))
    assert g["body"][0] < 0                           # bearish body is negative
    assert g["rng"][1] == pytest.approx(0.0)          # flat bar: no div-by-zero
    assert g["body_pct"][1] == 0.0 and g["up_shadow"][1] == 0.0


# --- stochastic %K / z-score (range position + statistical extreme) --------

def test_stoch_k_top_of_band_is_100():
    bars = _bars([(10, 10 + i, 10, 10 + i) for i in range(5)])  # high==close climbs
    k = features.stoch_k(bars, 3)
    assert k[1] is None and k[2] is not None          # warmup then valid
    assert k[-1] == pytest.approx(100.0)              # last close == window high


def test_stoch_k_flat_band_is_50():
    assert features.stoch_k(_bars([(5, 5, 5, 5)] * 5), 3)[-1] == pytest.approx(50.0)


def test_zscore_constant_is_zero_and_stretch_is_positive():
    assert features.zscore([5.0] * 10, 5)[-1] == pytest.approx(0.0)
    assert features.zscore([1, 1, 1, 1, 10], 5)[-1] == pytest.approx(2.0)


# --- MACD (+ histogram == 背驰 substrate) ----------------------------------

def test_macd_aligned_and_positive_in_uptrend():
    vals = [float(i) for i in range(1, 80)]
    m = features.macd(vals, 12, 26, 9)
    assert len(m["macd"]) == len(m["signal"]) == len(m["hist"]) == len(vals)
    assert m["macd"][-1] is not None and m["macd"][-1] > 0   # fast EMA above slow
    assert m["hist"][-1] is not None


def test_macd_flat_series_is_zero():
    m = features.macd([5.0] * 80, 12, 26, 9)
    assert m["macd"][-1] == pytest.approx(0.0)
    assert m["hist"][-1] == pytest.approx(0.0)


# --- OBV (volume/flow) ------------------------------------------------------

def test_obv_accumulates_signed_volume():
    bars = [
        {"open": 1, "high": 1, "low": 1, "close": 10, "volume": 5.0},
        {"open": 1, "high": 1, "low": 1, "close": 11, "volume": 3.0},  # up  -> +3
        {"open": 1, "high": 1, "low": 1, "close": 9, "volume": 4.0},   # dn  -> -4
        {"open": 1, "high": 1, "low": 1, "close": 9, "volume": 2.0},   # flat ->  0
    ]
    assert features.obv(bars) == [0.0, 3.0, -1.0, -1.0]


# --- Hurst exponent (regime classifier: mean-revert vs trend) --------------

def test_hurst_orders_meanrevert_below_trend():
    random.seed(1234)

    def ar_walk(phi, n=3000):
        inc, price, out = 0.0, 100.0, [100.0]
        for _ in range(n):
            inc = phi * inc + random.gauss(0.0, 1.0)
            price += inc
            out.append(price)
        return out

    trend = features.hurst_exponent(ar_walk(0.6), max_lag=50)
    rw = features.hurst_exponent(ar_walk(0.0), max_lag=50)
    revert = features.hurst_exponent(ar_walk(-0.6), max_lag=50)
    assert None not in (trend, rw, revert)
    assert revert < rw < trend          # anti-persistent < random walk < persistent
    assert revert < 0.5 < trend         # straddle the random-walk line


def test_hurst_short_series_is_none():
    assert features.hurst_exponent([1.0, 2.0, 3.0], max_lag=20) is None
