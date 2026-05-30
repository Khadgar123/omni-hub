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
