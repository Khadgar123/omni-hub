"""resample (1s -> coarser, DuckDB) + regime.classify_series (per-bar labels)."""

import pytest

from quant import market_store, regime, resample

_BASE = market_store.parse_ts("2024-01-01")  # µs, aligned to a UTC day
_S = market_store.MICROS  # 1s in µs


def test_resample_1s_to_1m_ohlcv(tmp_path):
    root = tmp_path / "market"
    rows = []
    for i in range(120):  # 2 minutes of 1s bars
        c = 100.0 + i
        rows.append({"bucket_ts": _BASE + i * _S, "open": (100.0 + i - 1),
                     "high": c + 1.0, "low": c - 1.0, "close": c,
                     "volume": 1.0, "vwap": c, "trades": 1})
    market_store.write_bars(rows, symbol="BTCUSDT", freq="1s", root=root)

    out = resample.resample("BTCUSDT", "1m", root=root, source_interval="1s")
    assert len(out) == 2                      # 120s -> 2 minutes
    m0 = out[0]
    assert m0["bucket_ts"] == _BASE           # aligned to the minute
    assert m0["open"] == pytest.approx(99.0)          # first second's open (100+0-1)
    assert m0["close"] == pytest.approx(159.0)        # last second's close
    assert m0["high"] == pytest.approx(160.0)         # max high
    assert m0["low"] == pytest.approx(99.0)           # min low
    assert m0["volume"] == pytest.approx(60.0)        # sum
    assert m0["trades"] == 60                         # sum


def test_resample_empty_when_no_data(tmp_path):
    assert resample.resample("NOPE", "1h", root=tmp_path / "market") == []


def test_classify_series_shape_and_trend():
    bars, prev = [], 100.0
    for i in range(120):
        c = 100.0 * (1.01 ** i)
        bars.append({"bucket_ts": _BASE + i * 86_400_000_000, "open": prev,
                     "high": max(prev, c) * 1.003, "low": min(prev, c) * 0.997,
                     "close": c, "volume": 1.0})
        prev = c
    track = regime.classify_series(bars)
    assert len(track) == len(bars)
    assert track[0]["insufficient"] is True            # warmup
    assert track[-1]["direction"] == "up"
    assert track[-1]["label"] in {"up", "strong_up"}
    assert set(track[-1]) >= {"as_of", "label", "direction", "stand_down", "insufficient"}


def test_resample_prefers_materialized_cache(tmp_path):
    root = tmp_path / "market"
    # 1s base: 120 flat bars at close 100
    s1 = [{"bucket_ts": _BASE + i * _S, "open": 100.0, "high": 101.0, "low": 99.0,
           "close": 100.0, "volume": 1.0, "vwap": 100.0, "trades": 1} for i in range(120)]
    market_store.write_bars(s1, symbol="BTCUSDT", freq="1s", root=root)
    # a DISTINCT materialized 1m bar (marker close 42) — the gold cache
    market_store.write_bars([{"bucket_ts": _BASE, "open": 1.0, "high": 1.0, "low": 1.0,
                              "close": 42.0, "volume": 9.0, "vwap": 1.0, "trades": 1}],
                            symbol="BTCUSDT", freq="1m", root=root)
    cached = resample.resample("BTCUSDT", "1m", root=root)            # default prefers cache
    assert len(cached) == 1 and cached[0]["close"] == 42.0           # returned the materialized table
    agg = resample.resample("BTCUSDT", "1m", root=root, prefer_materialized=False)  # force 1s aggregation
    assert agg and agg[0]["close"] == 100.0                          # aggregated from 1s, not the marker
