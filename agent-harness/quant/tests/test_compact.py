"""compact: merge small per-day part files into one, dedup by bucket_ts, idempotent."""

from quant import compact, market_store as ms


def _bars(idxs, *, day_us=1_711_152_000_000_000, step_us=60_000_000):
    """1m bars on a single UTC day (2024-03-23) at the given minute indexes."""
    return [{"open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i, "close": 100.5 + i,
             "volume": 10.0, "symbol": "BTCUSDT", "bucket_ts": day_us + i * step_us} for i in idxs]


def _parts(root):
    base = root / "bars_1m" / "symbol=BTCUSDT"
    return [p for d in base.glob("date=*") for p in d.glob("*.parquet")]


def test_compact_merges_multipart_and_dedups(tmp_path):
    root = tmp_path / "market"
    ms.write_bars(_bars(range(0, 10)), symbol="BTCUSDT", freq="1m", root=root)      # part 0: 0..9
    ms.write_bars(_bars(range(5, 15)), symbol="BTCUSDT", freq="1m", root=root)      # part 1: 5..14 (5..9 overlap)
    assert len(_parts(root)) == 2                                                   # two small files, same day

    out = compact.compact("BTCUSDT", "1m", root=root)
    assert out["compacted_dirs"] == 1 and out["files_merged"] == 2
    assert len(_parts(root)) == 1                                                   # merged to one file

    got = ms.bars("BTCUSDT", "1m", "2000-01-01", "2100-01-01", root=root)
    assert len(got) == 15                                                           # 0..14 unique (dedup of overlap)
    assert [r["bucket_ts"] for r in got] == sorted(r["bucket_ts"] for r in got)     # ordered


def test_compact_is_idempotent(tmp_path):
    root = tmp_path / "market"
    ms.write_bars(_bars(range(0, 10)), symbol="BTCUSDT", freq="1m", root=root)
    ms.write_bars(_bars(range(10, 20)), symbol="BTCUSDT", freq="1m", root=root)
    compact.compact("BTCUSDT", "1m", root=root)
    again = compact.compact("BTCUSDT", "1m", root=root)                             # already 1 file/day
    assert again["files_merged"] == 0 and again["compacted_dirs"] == 0
    assert len(ms.bars("BTCUSDT", "1m", "2000-01-01", "2100-01-01", root=root)) == 20
