"""Self-contained HTML backtest report — SEE the bad cases.

Price candles + entry/exit markers + regime background bands + equity/drawdown
+ a metrics table, rendered client-side via the Plotly CDN (no Python plotting
dependency; the figure is just an inlined JSON spec). This is the "eyeball the
PnL curve and WHERE the trades happened" diagnosis Quant Arb calls essential —
the thing metrics alone can't give you.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from quant.backtest import metrics as metrics_mod

_REGIME_COLOR = {
    "strong_up": "#1b5e20", "up": "#66bb6a", "range": "#9e9e9e",
    "down": "#ef9a9a", "strong_down": "#b71c1c",
}
_PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"


def _iso(us):
    return datetime.fromtimestamp(int(us) / 1e6, UTC).isoformat()


def _thin(seq, max_n):
    """Keep at most max_n points (every-Nth) so the HTML stays light."""
    n = len(seq)
    if n <= max_n or max_n <= 0:
        return seq
    step = (n + max_n - 1) // max_n
    return seq[::step]


def _regime_segments(track):
    """Merge contiguous same-label regime bars into (start_us, end_us, label)."""
    segs = []
    for r in track:
        lab, ts = r.get("label", "range"), int(r.get("as_of", 0))
        if segs and segs[-1][2] == lab:
            segs[-1][1] = ts
        else:
            segs.append([int(r.get("as_of", 0)), ts, lab])
    return segs


def backtest_report_html(result, bars, *, regime_track=None, metrics_dict=None,
                         title=None, max_candles=8000):
    bars_d = _thin(list(bars), max_candles)
    times = [_iso(b["bucket_ts"]) for b in bars_d]
    candle = {"type": "candlestick", "x": times,
              "open": [float(b["open"]) for b in bars_d], "high": [float(b["high"]) for b in bars_d],
              "low": [float(b["low"]) for b in bars_d], "close": [float(b["close"]) for b in bars_d],
              "name": "price", "yaxis": "y"}

    trades = list(result.trades)
    entries = {"type": "scatter", "mode": "markers", "name": "entry", "yaxis": "y",
               "x": [_iso(t.entry_ts) for t in trades], "y": [t.entry for t in trades],
               "marker": {"symbol": "triangle-up", "color": "#00c853", "size": 9,
                          "line": {"width": 1, "color": "#004d40"}}}
    exits = {"type": "scatter", "mode": "markers", "name": "exit", "yaxis": "y",
             "x": [_iso(t.exit_ts) for t in trades], "y": [t.exit for t in trades],
             "marker": {"symbol": "triangle-down", "size": 9,
                        "color": ["#d50000" if t.exit_reason == "stop" else "#1565c0" for t in trades]},
             "text": [f"{t.exit_reason} pnl={t.pnl:.2f}" for t in trades], "hoverinfo": "text+x+y"}

    curve = _thin(list(result.equity_curve), max_candles)
    equity = {"type": "scatter", "mode": "lines", "name": "equity", "yaxis": "y2",
              "x": [_iso(ts) for ts, _ in curve], "y": [e for _, e in curve],
              "line": {"color": "#5e35b1"}}

    shapes = []
    if regime_track:
        for s, e, lab in _regime_segments(regime_track):
            shapes.append({"type": "rect", "xref": "x", "yref": "paper",
                           "x0": _iso(s), "x1": _iso(e), "y0": 0.32, "y1": 1.0,
                           "fillcolor": _REGIME_COLOR.get(lab, "#9e9e9e"), "opacity": 0.10,
                           "line": {"width": 0}, "layer": "below"})

    ttl = title or f"{result.strategy_id} · {result.symbol}"
    layout = {"title": ttl, "height": 840, "template": "plotly_white", "showlegend": True,
              "xaxis": {"rangeslider": {"visible": False}, "domain": [0, 1]},
              "yaxis": {"domain": [0.32, 1.0], "title": "price"},
              "yaxis2": {"domain": [0.0, 0.24], "title": "equity"},
              "shapes": shapes, "margin": {"t": 50, "l": 60, "r": 20, "b": 30}}

    m = metrics_dict or metrics_mod.summarize(result.equity_curve, trades, equity0=result.equity0)
    rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in m.items())
    fig = json.dumps({"data": [candle, entries, exits, equity], "layout": layout}, default=str)
    legend = (" · regime bands: "
              "<span style='color:#1b5e20'>strong_up</span> "
              "<span style='color:#66bb6a'>up</span> "
              "<span style='color:#9e9e9e'>range</span> "
              "<span style='color:#ef9a9a'>down</span> "
              "<span style='color:#b71c1c'>strong_down</span>")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{ttl}</title>
<script src="{_PLOTLY_CDN}"></script>
<style>body{{font-family:system-ui,sans-serif;margin:16px;color:#222}}
table{{border-collapse:collapse;font-size:13px}} td{{border:1px solid #ddd;padding:2px 10px}}
td:first-child{{color:#555}}</style></head><body>
<h2>{ttl}</h2><div style="font-size:12px;color:#777">{len(trades)} trades{legend}</div>
<div id="chart" style="height:840px"></div>
<h3>metrics</h3><table>{rows}</table>
<script>const f={fig}; Plotly.newPlot('chart', f.data, f.layout, {{responsive:true}});</script>
</body></html>"""


def write_report(result, bars, path, **kwargs):
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(backtest_report_html(result, bars, **kwargs), encoding="utf-8")
    return p
