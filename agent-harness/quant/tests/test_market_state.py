"""MarketState multi-timeframe assembly tests (writes a synthetic store).

Bars use GEOMETRIC trends (constant log-returns => stable realized vol, so the
CUSUM change-point doesn't spuriously trip) anchored at a REAL epoch
(2020-01-01) so market_store's seconds/millis/micros magnitude heuristic and the
``asof`` cutoff behave as they do on live data.
"""

import pytest

from quant import market_state, market_store

_BASE = market_store.parse_ts("2020-01-01")  # epoch micros


def _write_trend(root, symbol, freq, n, *, start=100.0, rate=0.01):
    """Write a geometric series to bars_<freq> (rate>0 up, <0 down)."""
    bucket_us = market_store.freq_to_seconds(freq) * market_store.MICROS
    rows = []
    prev = start
    for i in range(n):
        c = start * (1.0 + rate) ** i
        rows.append({
            "bucket_ts": _BASE + i * bucket_us,
            "open": prev, "high": max(prev, c) * 1.005, "low": min(prev, c) * 0.995,
            "close": c, "volume": 1.0, "vwap": c, "trades": 1,
        })
        prev = c
    market_store.write_bars(rows, symbol=symbol, freq=freq, root=root)


def test_htf_up_confirm_up_is_long(tmp_path):
    root = tmp_path / "market"
    _write_trend(root, "BTCUSDT", "1d", 120, rate=0.01)
    _write_trend(root, "BTCUSDT", "4h", 300, rate=0.004)
    ms = market_state.build_market_state("BTCUSDT", root=root)
    assert ms.composite_bias == "long"
    assert ms.regime_label in {"up", "strong_up"}
    assert ms.per_tf["1d"] in {"up", "strong_up"}
    assert not ms.stand_down


def test_htf_up_confirm_down_vetoes_to_flat(tmp_path):
    root = tmp_path / "market"
    _write_trend(root, "BTCUSDT", "1d", 120, start=100.0, rate=0.01)   # 1d up
    _write_trend(root, "BTCUSDT", "4h", 300, start=300.0, rate=-0.004)  # 4h down
    ms = market_state.build_market_state("BTCUSDT", root=root)
    assert ms.direction == "up"          # 1d still sets the up direction...
    assert ms.composite_bias == "flat"   # ...but the 4h disagreement stands us aside


def test_htf_down_confirm_down_is_short(tmp_path):
    root = tmp_path / "market"
    _write_trend(root, "BTCUSDT", "1d", 120, start=300.0, rate=-0.01)
    _write_trend(root, "BTCUSDT", "4h", 300, start=300.0, rate=-0.004)
    ms = market_state.build_market_state("BTCUSDT", root=root)
    assert ms.composite_bias == "short"
    assert ms.regime_label in {"down", "strong_down"}


def test_missing_confirm_tf_is_safe(tmp_path):
    root = tmp_path / "market"
    _write_trend(root, "BTCUSDT", "1d", 120, rate=0.01)
    # no 4h bars -> confirm classifies as insufficient -> stand aside (flat)
    ms = market_state.build_market_state("BTCUSDT", root=root)
    assert ms.composite_bias == "flat"
    assert ms.confirm["insufficient"] is True


def test_asof_is_point_in_time(tmp_path):
    root = tmp_path / "market"
    _write_trend(root, "BTCUSDT", "1d", 200, rate=0.01)
    _write_trend(root, "BTCUSDT", "4h", 400, rate=0.004)
    day_us = market_store.freq_to_seconds("1d") * market_store.MICROS
    cutoff = _BASE + 80 * day_us
    ms = market_state.build_market_state("BTCUSDT", root=root, asof=cutoff)
    assert ms.as_of <= cutoff          # no look-ahead beyond the cutoff bar
    assert ms.to_dict()["schema_version"] == "ms-v1"
