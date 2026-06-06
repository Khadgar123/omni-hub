"""levels.round_levels + scored_levels — the symmetric, confluence-scored S/R map."""

from quant import levels as L


def test_round_levels_tiers_and_band():
    r = L.round_levels(70123, atr=200, span_atr=8)        # band ~ ±1600 around price
    prices = [x["price"] for x in r]
    assert 70000 in prices and 71000 in prices
    tier = {x["price"]: x["tier"] for x in r}
    assert tier[70000] == 2.0 and tier[71000] == 1.0      # 70000 is a multiple of 10×grid
    assert all(68500 <= p <= 71800 for p in prices)       # stays in the ATR band


def test_round_levels_tiers_by_magnitude():
    assert any(x["price"] == 3000 for x in L.round_levels(3010, atr=20))   # 1k≤p<10k -> 500 grid
    assert all(p % 500 == 0 for p in (x["price"] for x in L.round_levels(3010, atr=20)))


def _zigzag(n):
    seq = [70000, 70200, 70500, 70900, 71000, 70900, 70500, 70200, 70000]   # repeating pivots
    closes = [seq[i % len(seq)] for i in range(n)]
    return [{"open": c, "high": c + 60, "low": c - 60, "close": c, "volume": 10.0,
             "bucket_ts": i * 60_000_000} for i, c in enumerate(closes)]


def test_scored_levels_structure_and_confluence():
    lv = L.scored_levels(_zigzag(80), left=3, right=3, atr=300.0)
    assert lv, "expected at least one level"
    for x in lv:                                          # contract
        assert {"price", "strength", "kind"} <= set(x) and x["strength"] > 0
    assert lv == sorted(lv, key=lambda x: x["price"])     # sorted ascending
    kinds = " ".join(x["kind"] for x in lv)
    assert "round" in kinds                               # 70000/71000 round walls surfaced/boosted


def _two_regime_bars():
    """OLD 60 bars: heavy volume parked at ~80. RECENT 60 bars: light volume up at ~100."""
    old = [{"open": 80.0, "high": 81.0, "low": 79.0, "close": 80.0, "volume": 100.0,
            "bucket_ts": i * 3_600_000_000} for i in range(60)]
    recent = [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0,
               "bucket_ts": (60 + i) * 3_600_000_000} for i in range(60)]
    return old + recent


def test_volume_profile_lookback_windows_to_recent():
    bars = _two_regime_bars()
    poc_all = L.volume_profile(bars)["poc"]                # all history -> heavy OLD volume wins
    poc_recent = L.volume_profile(bars[-60:])["poc"]       # recent window only
    assert abs(poc_all - 80) < abs(poc_all - 100)          # POC pulled to the old ~80 cluster
    assert abs(poc_recent - 100) < abs(poc_recent - 80)    # windowed POC sits in the recent band


def test_scored_levels_vp_lookback_sharpens_vp():
    bars = _two_regime_bars()
    deep = L.scored_levels(bars, atr=3.0)                  # VP over ALL history
    win = L.scored_levels(bars, atr=3.0, vp_lookback=60)   # VP over recent 60 only (swing still full)
    vp_deep = [x["price"] for x in deep if x["kind"] == "vp"]
    vp_win = [x["price"] for x in win if x["kind"] == "vp"]
    assert any(p < 90 for p in vp_deep)                    # full-history VP surfaces the old ~80 node
    assert vp_win and all(p > 90 for p in vp_win)          # windowed VP is recent-only (~100)
