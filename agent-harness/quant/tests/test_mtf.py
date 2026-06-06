"""Multi-level feature aggregation + 区间套 gate. Deterministic windows; the
causal-attachment contract (LTF activity rolled into the enclosing HTF bar)."""

from quant import mtf


def _b(c, ts, *, hi=None, lo=None, v=2.0):
    return {"open": c, "high": hi if hi is not None else c + 0.5,
            "low": lo if lo is not None else c - 0.5, "close": c,
            "volume": v, "bucket_ts": ts}


_H = 3_600_000_000   # 1h in µs
_M = 60_000_000      # 1m in µs


# --- CVD proxy --------------------------------------------------------------

def test_cvd_proxy_signs_by_direction():
    bars = [_b(100, 0), _b(101, _M), _b(99, 2 * _M), _b(99, 3 * _M)]  # up, down, flat
    assert mtf.cvd_proxy(bars) == [0.0, 2.0, 0.0, 0.0]                 # +2, -2, 0


# --- aggregate (windowing + flow) -------------------------------------------

def test_aggregate_windows_cvd_and_return():
    htf = [_b(100, 0), _b(100, _H)]
    ltf = [_b(100, 0), _b(101, 10 * _M), _b(102, 20 * _M),          # window0: rising
           _b(105, _H), _b(104, _H + 10 * _M), _b(103, _H + 20 * _M)]  # window1: falling
    agg = mtf.aggregate(htf, ltf, left=1, right=1)
    assert len(agg) == 2
    assert agg[0]["n_ltf"] == 3 and agg[1]["n_ltf"] == 3
    assert agg[0]["cvd_delta"] > 0 and agg[0]["ltf_ret"] > 0        # accumulation
    assert agg[1]["cvd_delta"] < 0 and agg[1]["ltf_ret"] < 0        # distribution
    assert "n_bos" in agg[0] and "bos_net" in agg[0] and "n_swings" in agg[0]


def test_aggregate_empty_htf():
    assert mtf.aggregate([], [_b(100, 0)]) == []


# --- 区间套 nested-divergence AND gate --------------------------------------

# up divergence: impulse(100->120), pullback(->110), weaker higher-high(->125)
_UP = [(106, 108), (100, 104), (102, 110), (105, 120),
       (112, 116), (110, 114), (115, 122), (118, 125), (116, 120)]


def _div_bars(rows, step=_H):
    return [{"open": (low + h) / 2, "high": h, "low": low, "close": (low + h) / 2,
             "volume": 1.0, "bucket_ts": i * step} for i, (low, h) in enumerate(rows)]


def test_nested_divergence_confirms_same_direction():
    htf = _div_bars(_UP, step=_H)
    ltf = _div_bars(_UP, step=_H)         # same up-divergence at the same ts
    ev = mtf.nested_divergence(htf, ltf, left=1, right=1, window_us=2 * _H)
    assert len(ev) == 1 and ev[0]["dir"] == "up"
    assert ev[0]["htf_ratio"] < 0.9 and ev[0]["ltf_ratio"] < 0.9


def test_nested_divergence_rejects_opposite_ltf():
    htf = _div_bars(_UP, step=_H)                       # HTF up-divergence
    down = [(220 - h, 220 - low) for (low, h) in _UP]   # mirror -> down move, no up-div
    ltf = _div_bars(down, step=_H)
    assert mtf.nested_divergence(htf, ltf, left=1, right=1, window_us=2 * _H) == []
