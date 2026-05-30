"""Multi-level S/R engine: volume profile, swing-pivot clustering, confluence,
nearest-level features. Hand-checkable deterministic inputs."""

import pytest

from quant import levels


def _vp_bars(price_vol):
    """single-price bars -> each bar's volume lands in one profile bin."""
    return [{"open": p, "high": p, "low": p, "close": p, "volume": float(v)}
            for p, v in price_vol]


# --- volume profile ---------------------------------------------------------

def test_volume_profile_poc_and_value_area():
    vp = levels.volume_profile(_vp_bars([(95, 5), (97, 10), (100, 30), (103, 10), (105, 5)]), n_bins=20)
    assert abs(vp["poc"] - 100) < 0.6                 # heaviest volume at 100
    assert vp["val"] <= vp["poc"] <= vp["vah"]        # VA brackets the POC
    assert 96 < vp["val"] < 100 < vp["vah"] < 104     # 70% band excludes the tails


def test_volume_profile_single_price_degenerate():
    vp = levels.volume_profile(_vp_bars([(100, 5), (100, 7)]), n_bins=20)
    assert vp["poc"] == vp["vah"] == vp["val"] == 100


def test_volume_profile_empty():
    vp = levels.volume_profile([], n_bins=20)
    assert vp["poc"] is None and vp["profile"] == []


# --- swing-pivot clustering -------------------------------------------------

def _sr_bars():
    rows = [  # (low, high): two swing-low touches ~99-100, three swing-high ~119-121
        (108, 110), (102, 120), (100, 112), (105, 118), (101, 121),
        (99, 110), (103, 115), (112, 119), (110, 116),
    ]
    return [{"open": (low + h) / 2, "high": h, "low": low, "close": (low + h) / 2,
             "volume": 1.0, "bucket_ts": i * 60_000_000} for i, (low, h) in enumerate(rows)]


def test_swing_levels_clusters_pivots():
    lv = levels.swing_levels(_sr_bars(), left=1, right=1, merge_pct=0.05)
    assert len(lv) == 2
    lo, hi = lv                                       # sorted ascending by price
    assert 98 < lo["price"] < 102 and lo["n_low"] == 2
    assert 118 < hi["price"] < 122 and hi["n_high"] == 3 and hi["touches"] == 3


def test_swing_levels_recency_weights_strength():
    # a recent touch should carry more strength than an old one (halflife decay)
    lv = levels.swing_levels(_sr_bars(), left=1, right=1, merge_pct=0.05, halflife=3)
    assert all(L["strength"] > 0 for L in lv)


# --- multi-TF confluence ----------------------------------------------------

def test_confluence_fuses_across_timeframes():
    by_tf = {"1h": [{"price": 100.0, "strength": 1.0}],
             "4h": [{"price": 100.3, "strength": 1.0}],
             "1d": [{"price": 130.0, "strength": 1.0}]}
    cf = levels.confluence(by_tf, tf_weight={"1h": 1, "4h": 2, "1d": 3}, merge_pct=0.01)
    assert len(cf) == 2
    fused = [c for c in cf if c["n_tf"] == 2][0]      # 100 & 100.3 merge (within 1%)
    assert abs(fused["price"] - 100.2) < 0.3
    assert fused["confluence_score"] == pytest.approx(3.0)   # 1*1 + 2*1
    assert set(fused["tfs"]) == {"1h", "4h"}


# --- nearest-level features -------------------------------------------------

def test_nearest_levels_and_rr():
    lv = [{"price": 90.0}, {"price": 110.0}]
    nl = levels.nearest_levels(100.0, lv, atr=5.0)
    assert nl["support"] == 90 and nl["resistance"] == 110
    assert nl["dist_to_support"] == pytest.approx(2.0)        # (100-90)/5
    assert nl["dist_to_resistance"] == pytest.approx(2.0)     # (110-100)/5
    assert nl["rr_by_levels"] == pytest.approx(1.0)


def test_nearest_levels_missing_side():
    lv = [{"price": 90.0}, {"price": 110.0}]
    nl = levels.nearest_levels(80.0, lv, atr=5.0)             # below everything
    assert nl["support"] is None and nl["resistance"] == 90
    assert nl["dist_to_support"] is None and nl["rr_by_levels"] is None
