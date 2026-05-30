"""Discrete structural events (swings / fractals / BOS / CHoCH) — the
non-morphology price-structure layer. Hand-traced deterministic bar paths."""

import pytest

from quant import structure


def _bars(rows):
    """rows: list of (low, high, close) -> bar dicts (open == close; unused)."""
    return [
        {"open": c, "high": h, "low": low, "close": c, "volume": 1.0,
         "bucket_ts": i * 3_600_000_000}
        for i, (low, h, c) in enumerate(rows)
    ]


# up-leg -> pullback (swing low) -> higher-high (close breaks prior swing high
# == BOS up) -> higher-low (swing low) -> close below it (== CHoCH down).
ROWS = [
    (10, 12, 11),   # 0
    (11, 13, 12),   # 1
    (12, 15, 14),   # 2  swing high = 15
    (11, 13, 11),   # 3
    (10, 12, 10),   # 4  swing low  = 10
    (12, 14, 13),   # 5
    (14, 17, 16),   # 6  close 16 > 15  -> BOS up
    (13, 15, 13),   # 7
    (11, 13, 11),   # 8  swing low  = 11
    (12, 14, 12),   # 9
    (9, 11, 10),    # 10 close 10 < 11  -> CHoCH down
]


def test_swings_detects_pivots_window_1():
    sw = structure.swings(_bars(ROWS), 1, 1)
    his = {s["idx"]: s["price"] for s in sw if s["kind"] == "high"}
    los = {s["idx"]: s["price"] for s in sw if s["kind"] == "low"}
    assert his.get(2) == 15 and his.get(6) == 17
    assert los.get(4) == 10 and los.get(8) == 11


def test_wider_window_drops_minor_pivots():
    # bar 9's minor high (14) is a pivot at window 1 but not at window 2
    w1 = {s["idx"] for s in structure.swings(_bars(ROWS), 1, 1) if s["kind"] == "high"}
    w2 = {s["idx"] for s in structure.swings(_bars(ROWS), 2, 2) if s["kind"] == "high"}
    assert 9 in w1 and 9 not in w2          # the window IS the timeframe knob


def test_fractals_equal_swings_1_1():
    bars = _bars(ROWS)
    assert structure.fractals(bars) == structure.swings(bars, 1, 1)


def test_market_structure_bos_then_choch():
    ev = structure.market_structure(_bars(ROWS), left=1, right=1)
    assert [(e["idx"], e["type"], e["dir"]) for e in ev] == [
        (6, "BOS", "up"), (10, "CHoCH", "down")
    ]


def test_market_structure_is_causal_no_lookahead():
    # truncating the series at the BOS bar must not change earlier events
    full = structure.market_structure(_bars(ROWS), left=1, right=1)
    upto7 = structure.market_structure(_bars(ROWS[:8]), left=1, right=1)
    assert upto7 == [e for e in full if e["idx"] <= 7]


def test_swings_window_must_be_positive():
    with pytest.raises(ValueError):
        structure.swings(_bars(ROWS), 0, 1)


# --- legs + force metrics + 背驰 (divergence) -------------------------------

# impulse up (100->120), pullback (->110), weaker impulse to a HIGHER high
# (->125) but smaller amplitude -> 背驰 by the amplitude metric.
DIV_ROWS = [
    (106, 108), (100, 104), (102, 110), (105, 120),
    (112, 116), (110, 114), (115, 122), (118, 125), (116, 120),
]


def _div_bars(rows):
    return [{"open": (low + h) / 2, "high": h, "low": low, "close": (low + h) / 2,
             "volume": 1.0, "bucket_ts": i * 60_000_000}
            for i, (low, h) in enumerate(rows)]


def test_legs_force_metrics():
    lg = structure.legs(_div_bars(DIV_ROWS), left=1, right=1)
    assert [x["dir"] for x in lg] == ["up", "down", "up"]
    up0, _, up2 = lg
    assert up0["amp"] == pytest.approx(20.0)   # 100 -> 120
    assert up2["amp"] == pytest.approx(15.0)   # 110 -> 125
    assert up0["slope"] > 0 and "macd_area" in up0 and "vol" in up0


def test_divergence_detects_weaker_new_high():
    ev = structure.divergence(_div_bars(DIV_ROWS), left=1, right=1, macd_algo="amp")
    assert len(ev) == 1
    d = ev[0]
    assert d["dir"] == "up" and d["new_extreme"] is True and d["is_divergence"] is True
    assert d["metric_ratio"] == pytest.approx((15 / 110) / (20 / 100), rel=1e-6)  # ≈0.682


def test_no_divergence_when_second_leg_stronger():
    rows = DIV_ROWS[:7] + [(118, 150), (116, 120)]   # higher high AND bigger amplitude
    ev = structure.divergence(_div_bars(rows), left=1, right=1, macd_algo="amp")
    assert ev and ev[-1]["new_extreme"] is True and ev[-1]["is_divergence"] is False


# --- climax / exhaustion (Leledc-style 一致→加速→衰竭) -----------------------

def _ohlc_bars(ohlc):
    return [{"open": o, "high": h, "low": low, "close": c, "volume": 1.0,
             "bucket_ts": i * 60_000_000} for i, (o, h, low, c) in enumerate(ohlc)]


def test_exhaustion_top_after_up_run():
    rows = [(c - 0.3, c + 0.5, c - 0.5, c) for c in (100.0 + i for i in range(14))]  # rising green run
    rows.append((114.0, 116.0, 112.0, 113.0))   # climax bar: NEW high but closes RED
    ev = structure.exhaustion(_ohlc_bars(rows), core=4, qual=6, length=12)
    tops = [e for e in ev if e["kind"] == "top"]
    assert tops and tops[0]["idx"] == 14 and tops[0]["run"] > 6


def test_exhaustion_bottom_after_down_run():
    rows = [(c + 0.3, c + 0.5, c - 0.5, c) for c in (120.0 - i for i in range(14))]  # falling red run
    rows.append((106.0, 108.0, 104.0, 107.0))   # climax bar: NEW low but closes GREEN
    ev = structure.exhaustion(_ohlc_bars(rows), core=4, qual=6, length=12)
    bottoms = [e for e in ev if e["kind"] == "bottom"]
    assert bottoms and bottoms[0]["idx"] == 14


def test_exhaustion_quiet_chop_is_empty():
    rows = [(100, 100.5, 99.5, 100)] * 30        # flat: no run, no new extreme
    assert structure.exhaustion(_ohlc_bars(rows), core=4, qual=6, length=12) == []
