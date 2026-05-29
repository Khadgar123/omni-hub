"""Regime committee + CUSUM change-point tests (synthetic bars; pure core)."""

import pytest

from quant import regime

_DAY_US = 86_400_000_000


def _bars(closes, *, band=0.5):
    bars = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        h = max(o, c) + band
        low = min(o, c) - band
        bars.append(
            {"bucket_ts": i * _DAY_US, "open": o, "high": h, "low": low, "close": c, "volume": 1.0}
        )
        prev = c
    return bars


def test_uptrend_is_up_regime():
    bars = _bars([100.0 + i for i in range(120)])
    r = regime.classify(bars)
    assert r.direction == "up"
    assert r.label in {"up", "strong_up"}
    assert r.adx is not None and r.adx > regime.ADX_TREND
    assert r.label in regime.TREND_REGIMES
    assert not r.stand_down  # steady vol => no change-point


def test_downtrend_is_down_regime():
    bars = _bars([300.0 - i for i in range(120)])
    r = regime.classify(bars)
    assert r.direction == "down"
    assert r.label in {"down", "strong_down"}
    assert r.label in regime.SHORT_BIAS_REGIMES


def test_choppy_is_range():
    closes = [100.0 + (1.0 if i % 2 == 0 else -1.0) for i in range(120)]
    r = regime.classify(closes_bars := _bars(closes, band=0.2))
    assert r.label == "range"
    assert r.label in regime.RANGE_REGIMES


def test_as_of_is_last_bucket():
    bars = _bars([100.0 + i for i in range(80)])
    assert regime.classify(bars).as_of == 79 * _DAY_US


# --- CUSUM change-point ----------------------------------------------------

def test_cusum_flat_never_trips():
    vol = [0.01] * 120
    flags = regime.cusum_standdown(vol)
    assert not any(flags)


def test_cusum_trips_on_vol_expansion_then_cools_down():
    vol = [0.01] * 60 + [0.25] * 20 + [0.01] * 40
    flags = regime.cusum_standdown(vol, cooldown=12)
    assert not any(flags[:55])             # quiet regime: no trip
    assert any(flags[60:75])               # trips at the expansion
    # cooldown is finite: it eventually relaxes once vol normalizes
    assert not flags[-1]


def test_cusum_tolerates_none_warmup():
    vol = [None] * 20 + [0.01] * 100
    flags = regime.cusum_standdown(vol)
    assert len(flags) == len(vol)
    assert not any(flags)
