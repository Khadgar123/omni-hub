"""Pure-logic tests for quant.macro (NO network) — the bars/structure/corr/narrate helpers.
``read()`` fetches live (yfinance/akshare) and is smoke-covered by `omni-hub macro-read`; the
deterministic core is locked here. quant.macro imports yfinance/akshare lazily inside read(), so this
module imports fine without them."""
import pandas as pd

from quant import macro


def _ramp(a, b, n):
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _bars_from(prices):
    return [{"open": float(c), "high": float(c) * 1.002, "low": float(c) * 0.998, "close": float(c),
             "volume": 1000.0, "bucket_ts": (i + 1) * 86_400_000_000} for i, c in enumerate(prices)]


def test_corr_extremes():
    a = [0.01, -0.02, 0.03, -0.01, 0.02, -0.015]
    assert abs(macro._corr(a, a) - 1.0) < 1e-9               # perfectly correlated
    assert abs(macro._corr(a, [-x for x in a]) + 1.0) < 1e-9  # perfectly anti-correlated


def test_bars_from_dataframe():
    idx = pd.date_range("2026-01-01", periods=5, freq="D")
    df = pd.DataFrame({"Open": [1, 2, 3, 4, 5], "High": [1.1, 2.1, 3.1, 4.1, 5.1],
                       "Low": [0.9, 1.9, 2.9, 3.9, 4.9], "Close": [1, 2, 3, 4, 5], "Volume": [10] * 5}, index=idx)
    bars = macro._bars(df)
    assert len(bars) == 5
    assert {"open", "high", "low", "close", "volume", "bucket_ts"} <= set(bars[0])
    assert bars[-1]["close"] == 5.0 and bars[0]["bucket_ts"] < bars[-1]["bucket_ts"]


def test_structure_downtrend_and_sr():
    # a clear multi-leg descending zigzag (lower-highs + lower-lows) so market_structure confirms BOS
    dn, top = [], 100.0
    for _ in range(6):
        dn += _ramp(top, top - 10, 8)[:-1] + _ramp(top - 10, top - 6, 4)[:-1]
        top -= 6
    s = macro._structure(_bars_from(dn))
    assert s["trend"] == "down"                              # market_structure: BOS/CHoCH down
    assert s["event"].startswith("BOS down")
    assert s["support"] is not None and s["resistance"] >= s["support"] and s["pos"] is not None


def test_narrate_is_readable_with_disclaimer():
    up = {"direction": "up"}
    r = {
        "assets": {"^GSPC": {"name": "美S&P500", **up, "mo1": 5.0}, "^NDX": {"name": "美Nasdaq100", **up, "mo1": 11.0},
                   "000300.SS": {"name": "A股沪深300", **up, "mo1": 2.0}, "^N225": {"name": "日经225", **up, "mo1": 11.0},
                   "^KS11": {"name": "韩KOSPI", **up, "mo1": 31.0}, "BTC-USD": {"name": "BTC", "direction": "down", "mo1": -14.0}},
        "panel": {"curve": {"us2s10s": 0.42, "us_cn_spread": 2.77}, "credit": {"hyg_ief": 0.848},
                  "vol": {"vix": 16.2, "move": 73}, "commodities": {"copper_mo": 11.6, "oil_mo": -11.2},
                  "growth_inflation_note": "美ISM~48(收缩) — akshare"},
        "cross": {"leaders": [], "laggards": []},
    }
    n = macro.narrate(r)
    assert "非投资建议" in n                                  # disclaimer baked in (code guarantee)
    assert "BTC" in n and "melt-up" in n and "2s10s" in n
