"""Core: pure helpers + Parquet writer + DuckDB query round-trips."""

from __future__ import annotations

import pytest

duckdb = pytest.importorskip("duckdb")
pyarrow = pytest.importorskip("pyarrow")

from quant import market_store as ms  # noqa: E402
from quant import sample  # noqa: E402


# ---- pure helpers (no deps) ------------------------------------------------


def test_freq_to_seconds():
    assert ms.freq_to_seconds("1m") == 60
    assert ms.freq_to_seconds("5m") == 300
    assert ms.freq_to_seconds("1h") == 3600
    assert ms.freq_to_seconds("1d") == 86400
    assert ms.freq_to_seconds("15s") == 15
    assert ms.freq_to_seconds("1w") == 604800
    assert ms.freq_to_seconds("d") == 86400  # implicit leading 1
    for bad in ("1x", "0m", "-1m", ""):
        with pytest.raises(ValueError):
            ms.freq_to_seconds(bad)


def test_parse_ts_magnitude_and_dates():
    assert ms.parse_ts(1) == 1 * ms.MICROS                  # seconds
    assert ms.parse_ts(1_700_000_000) == 1_700_000_000 * ms.MICROS
    assert ms.parse_ts(1_700_000_000_000) == 1_700_000_000 * ms.MICROS  # millis
    assert ms.parse_ts(1_700_000_000_000_000) == 1_700_000_000 * ms.MICROS  # micros
    start = ms.parse_ts("2026-01-02")
    end = ms.parse_ts("2026-01-02", end_of_day=True)
    assert end - start == 86_400 * ms.MICROS - 1
    assert ms.micros_to_utc_date(start) == "2026-01-02"
    # ISO datetime
    assert ms.parse_ts("2026-01-02T00:00:00Z") == start


def test_parse_ts_year_and_month_bounds():
    # YYYY -> Jan 1 .. Dec 31 23:59:59.999999 (the harness CLI documents --from 2024-07)
    assert ms.micros_to_utc_date(ms.parse_ts("2026")) == "2026-01-01"
    assert ms.micros_to_utc_date(ms.parse_ts("2026", end_of_day=True)) == "2026-12-31"
    # YYYY-MM -> first .. last day of that month, honoring month length / leap year
    assert ms.micros_to_utc_date(ms.parse_ts("2026-06")) == "2026-06-01"
    assert ms.micros_to_utc_date(ms.parse_ts("2026-06", end_of_day=True)) == "2026-06-30"
    assert ms.micros_to_utc_date(ms.parse_ts("2026-02", end_of_day=True)) == "2026-02-28"
    assert ms.micros_to_utc_date(ms.parse_ts("2024-02", end_of_day=True)) == "2024-02-29"  # leap
    assert ms.micros_to_utc_date(ms.parse_ts("2026-12", end_of_day=True)) == "2026-12-31"  # year wrap
    # end-of-month is the last microsecond, mirroring the YYYY-MM-DD contract
    assert ms.parse_ts("2026-06", end_of_day=True) - ms.parse_ts("2026-06") == 30 * 86_400 * ms.MICROS - 1


def test_bars_from_trades_buckets_vwap_dedup():
    trades = [
        {"exchange_ts": 0, "price": 10.0, "size": 1.0, "trade_id": "a"},
        {"exchange_ts": 30 * ms.MICROS, "price": 12.0, "size": 3.0, "trade_id": "b"},
        {"exchange_ts": 30 * ms.MICROS, "price": 12.0, "size": 3.0, "trade_id": "b"},  # dup
        {"exchange_ts": 61 * ms.MICROS, "price": 9.0, "size": 1.0, "trade_id": "c"},
    ]
    bars = ms.bars_from_trades(trades, freq="1m", symbol="X")
    assert len(bars) == 2
    b0 = bars[0]
    assert (b0["open"], b0["high"], b0["low"], b0["close"]) == (10.0, 12.0, 10.0, 12.0)
    assert b0["volume"] == 4.0 and b0["trades"] == 2          # dup ignored
    assert b0["vwap"] == pytest.approx((10 * 1 + 12 * 3) / 4)  # 11.5
    assert b0["symbol"] == "X" and b0["bucket_ts"] == 0
    assert bars[1]["bucket_ts"] == 60 * ms.MICROS


def test_bars_from_trades_legacy_seconds():
    bars = ms.bars_from_trades(
        [{"ts": 0, "price": 10.0, "size": 1}, {"ts": 30, "price": 11.0, "size": 2}],
        freq="1m",
    )
    assert len(bars) == 1 and bars[0]["close"] == 11.0 and bars[0]["volume"] == 3


def test_partition_path():
    p = ms.partition_path("/r", "trades", "NVDA", "2026-01-02")
    assert str(p).endswith("trades/symbol=NVDA/date=2026-01-02/part-00000.parquet")


# ---- writer + DuckDB query round-trip --------------------------------------


def test_write_and_query_roundtrip(empty_root):
    rows = sample.sample_trades("BTC")
    paths = ms.write_trades(rows, root=empty_root)
    assert paths and all(p.exists() for p in paths)
    got = ms.trades("BTC", "2026-01-02", "2026-01-03", root=empty_root)
    assert len(got) == len(rows)
    # ordered by (exchange_ts, sequence)
    assert got == sorted(got, key=lambda r: (r["exchange_ts"], r["sequence"]))
    # symbol/date are partition-derived (Hive path), surfaced on read
    assert got[0]["symbol"] == "BTC" and "date" in got[0]


def test_append_creates_multiple_parts(empty_root):
    rows = sample.sample_trades("BTC")
    ms.write_trades(rows[:5], root=empty_root)
    ms.write_trades(rows[5:6], root=empty_root)  # second append into 2026-01-02
    day1_dir = empty_root / "trades" / "symbol=BTC" / "date=2026-01-02"
    assert len(list(day1_dir.glob("part-*.parquet"))) == 2  # append-safe, no clobber
    got = ms.trades("BTC", "2026-01-02", "2026-01-02", root=empty_root)
    assert len(got) == 6  # both parts read via glob


def test_empty_store_is_safe(empty_root):
    assert ms.bars("X", "1d", "2026-01-01", "2026-01-10", root=empty_root) == []
    assert ms.trades("X", "2026-01-01", "2026-01-10", root=empty_root) == []
    assert ms.last_price("X", "2026-01-10", root=empty_root) is None


def test_range_filter_prunes_by_date(store):
    got = ms.trades("DEMO", "2026-01-03", "2026-01-03", root=store)
    assert {r["trade_id"] for r in got} == {"D7", "D8", "D9"}


def test_bars_roundtrip_and_order(store):
    tr = ms.trades("DEMO", "2026-01-02", "2026-01-03", root=store)
    written = ms.write_bars(ms.bars_from_trades(tr, freq="1d", symbol="DEMO"),
                            symbol="DEMO", freq="1d", root=store)
    assert written
    bars = ms.bars("DEMO", "1d", "2026-01-01", "2026-01-10", root=store)
    assert [b["bucket_ts"] for b in bars] == sorted(b["bucket_ts"] for b in bars)
    assert bars[0]["open"] == 100.0 and bars[0]["close"] == 100.5
