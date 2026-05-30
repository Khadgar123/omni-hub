"""HTML backtest report (badcase visualizer) — structure + file write."""

from quant.backtest import engine, report
from quant.backtest.costs import ZERO_COST
from quant.strategy.trend_donchian import TrendDonchian

_H = 3_600_000_000
_BASE = 1_704_067_200_000_000


def _uptrend(n=140):
    bars, prev = [], 100.0
    for i in range(n):
        c = 100.0 * (1.01 ** i)
        bars.append({"bucket_ts": _BASE + i * _H, "open": prev, "high": max(prev, c) * 1.003,
                     "low": min(prev, c) * 0.997, "close": c, "volume": 1.0})
        prev = c
    return bars


def test_report_html_has_chart_markers_and_metrics():
    bars = _uptrend()
    res = engine.run_backtest(TrendDonchian(), bars, cost=ZERO_COST)
    track = [{"as_of": b["bucket_ts"], "label": "up"} for b in bars]
    html = report.backtest_report_html(res, bars, regime_track=track, title="t1")
    assert "Plotly.newPlot" in html and "candlestick" in html
    assert "cdn.plot.ly" in html
    assert "t1" in html and "metrics" in html
    assert '"type": "rect"' in html        # regime band shape present
    assert "triangle-up" in html           # entry markers


def test_write_report_file(tmp_path):
    bars = _uptrend()
    res = engine.run_backtest(TrendDonchian(), bars, cost=ZERO_COST)
    p = report.write_report(res, bars, tmp_path / "r.html",
                            regime_track=[{"as_of": b["bucket_ts"], "label": "up"} for b in bars])
    assert p.exists()
    assert p.read_text(encoding="utf-8").startswith("<!doctype html")


def test_report_thins_large_series():
    bars = _uptrend(500)
    res = engine.run_backtest(TrendDonchian(), bars, cost=ZERO_COST)
    html = report.backtest_report_html(res, bars, max_candles=100)
    assert "Plotly.newPlot" in html  # still renders with downsampled candles


def test_report_badcase_panel_and_zoom():
    bars = _uptrend()
    res = engine.run_backtest(TrendDonchian(), bars, cost=ZERO_COST)
    html = report.backtest_report_html(res, bars, title="bc")
    assert "bad cases" in html                              # the panel header
    assert "function zoom" in html and "Plotly.relayout" in html   # click-a-row-to-zoom
    assert "sortBy('pnl')" in html                          # worst-first default sort
    assert "const TR = " in html                            # trades inlined for the table
    assert "__" not in html.split("<script>")[0]            # no unsubstituted placeholders left


def test_report_handles_zero_trades():
    # a flat series that never triggers the breakout -> no trades, still renders
    flat = [{"bucket_ts": _BASE + i * _H, "open": 100.0, "high": 100.5,
             "low": 99.5, "close": 100.0, "volume": 1.0} for i in range(60)]
    res = engine.run_backtest(TrendDonchian(), flat, cost=ZERO_COST)
    html = report.backtest_report_html(res, flat)
    assert "no trades" in html and "const TR = []" in html
