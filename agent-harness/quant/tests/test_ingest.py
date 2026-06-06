"""ingest: API → dedup → store. Network-free (fetch injected), tmp root, idempotent."""

import json

from quant import ingest, market_store as ms


def _bars(n, *, start_us=1_700_000_000_000_000, step_us=3_600_000_000):
    """n contiguous 1h bars (step in µs)."""
    return [{"open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i, "close": 100.5 + i,
             "volume": 10.0, "bucket_ts": start_us + i * step_us} for i in range(n)]


def _count(root, symbol="BTCUSDT", freq="1h"):
    return len(ms.bars(symbol, freq, "2000-01-01", "2100-01-01", root=root))


def test_ingest_writes_and_is_idempotent(tmp_path):
    root = tmp_path / "market"
    bars = _bars(50)
    r1 = ingest.refresh("BTCUSDT", "1h", root=root, fetch=lambda s, f, m: bars, stamp=1.0)
    assert r1["written"] == 50 and r1["fetched"] == 50
    assert _count(root) == 50

    # re-ingest the SAME window -> nothing new (idempotent), store unchanged
    r2 = ingest.refresh("BTCUSDT", "1h", root=root, fetch=lambda s, f, m: bars, stamp=2.0)
    assert r2["written"] == 0
    assert _count(root) == 50

    # manifest recorded ONLY the run that actually wrote
    lines = (root / ingest.MANIFEST).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["written"] == 50


def test_ingest_appends_only_new_bars(tmp_path):
    root = tmp_path / "market"
    full = _bars(75)
    ingest.refresh("ETHUSDT", "1h", root=root, fetch=lambda s, f, m: full[:50], stamp=1.0)
    r = ingest.refresh("ETHUSDT", "1h", root=root, fetch=lambda s, f, m: full[25:75], stamp=2.0)
    assert r["written"] == 25                                  # 25 overlapping bars deduped
    assert _count(root, "ETHUSDT") == 75


def test_refresh_all_sweeps_pairs(tmp_path):
    root = tmp_path / "market"
    out = ingest.refresh_all(symbols=("BTCUSDT",), freqs=("1h", "4h"), root=root,
                             fetch=lambda s, f, m: _bars(10), stamp=1.0)
    assert len(out) == 2 and all(r["written"] == 10 for r in out)
    assert _count(root, "BTCUSDT", "1h") == 10 and _count(root, "BTCUSDT", "4h") == 10


def test_refresh_all_is_resilient_to_one_bad_pair(tmp_path):
    root = tmp_path / "market"

    def fetch(s, f, m):
        if f == "5m":
            raise ValueError("boom")                               # one freq blows up
        return _bars(8)

    out = ingest.refresh_all(symbols=("BTCUSDT",), freqs=("1h", "5m", "4h"),
                             root=root, fetch=fetch, stamp=1.0)
    assert len(out) == 3                                           # sweep did NOT abort
    bad = [r for r in out if r.get("error")]
    assert len(bad) == 1 and bad[0]["freq"] == "5m"               # the bad pair is recorded
    assert all(r["written"] == 8 for r in out if not r.get("error"))   # good pairs still wrote


def test_empty_fetch_is_safe(tmp_path):
    root = tmp_path / "market"
    r = ingest.refresh("BTCUSDT", "1h", root=root, fetch=lambda s, f, m: [], stamp=1.0)
    assert r["written"] == 0 and r["fetched"] == 0
    assert not (root / ingest.MANIFEST).exists()               # nothing written -> no provenance line
