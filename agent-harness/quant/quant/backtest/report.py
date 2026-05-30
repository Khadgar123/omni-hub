"""Self-contained HTML backtest report — SEE the bad cases.

Price candles + entry/exit markers + regime background bands + equity/drawdown
+ a metrics table, rendered client-side via the Plotly CDN (no Python plotting
dependency; the figure is just an inlined JSON spec). This is the "eyeball the
PnL curve and WHERE the trades happened" diagnosis Quant Arb calls essential —
the thing metrics alone can't give you.

Plus a BAD-CASE panel: every trade in a sortable table (worst-first by default),
and clicking a row zooms the price chart to that trade's window — so you can see
*why* a given trade lost, not just that the aggregate Sharpe was bad.
"""

from __future__ import annotations

import json
from collections import Counter
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


# The static page shell: placeholders (__X__) are substituted with JSON/strings,
# so the JS/CSS braces below need no f-string escaping.
_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><title>__TITLE__</title>
<script src="__CDN__"></script>
<style>body{font-family:system-ui,sans-serif;margin:16px;color:#222}
h2{margin:0 0 2px} table{border-collapse:collapse;font-size:13px}
td,th{border:1px solid #ddd;padding:2px 10px} td:first-child{color:#555}
#ttab{margin-top:6px} #ttab th{cursor:pointer;background:#f5f5f5;user-select:none;position:sticky;top:0}
#ttab td{text-align:right} #ttab td:nth-child(2),#ttab td:nth-child(3),#ttab td:last-child{text-align:left}
#ttab tbody tr{cursor:pointer} #ttab tbody tr:hover{outline:2px solid #5e35b1}
tr.L{background:#fdecea} tr.W{background:#e8f5e9}
.bar{display:flex;gap:14px;align-items:center;font-size:12px;color:#555;margin:8px 0}
.bar button{font:inherit;padding:2px 10px;cursor:pointer} .wrap{max-height:340px;overflow:auto;border:1px solid #eee}</style>
</head><body>
<h2>__TITLE__</h2><div style="font-size:12px;color:#777">__NTRADES__ trades __LEGEND__</div>
<div id="chart" style="height:760px"></div>
<h3>metrics</h3><table>__METRICS__</table>
<h3>bad cases — worst first · click a row to zoom the chart</h3>
<div class="bar"><button onclick="resetZoom()">⟲ reset zoom</button><span id="diag">__DIAG__</span></div>
<div class="wrap"><table id="ttab"><thead><tr>
<th onclick="sortBy('i')">#</th><th onclick="sortBy('e0')">entry (UTC)</th><th onclick="sortBy('e1')">exit (UTC)</th>
<th onclick="sortBy('bh')">bars</th><th onclick="sortBy('ret')">ret %</th><th onclick="sortBy('pnl')">pnl</th>
<th onclick="sortBy('r')">exit reason</th></tr></thead><tbody id="tbody"></tbody></table></div>
<script>
const f = __FIG__;
Plotly.newPlot('chart', f.data, f.layout, {responsive:true});
const TR = __TRADES__;
function zoom(e0, e1){
  const a = Date.parse(e0), b = Date.parse(e1);
  const pad = Math.max((b - a) * 1.5, 6 * 3600 * 1000);  // >=6h context each side
  Plotly.relayout('chart', {'xaxis.autorange': false,
    'xaxis.range': [new Date(a - pad).toISOString(), new Date(b + pad).toISOString()]});
}
function resetZoom(){ Plotly.relayout('chart', {'xaxis.autorange': true}); }
function cell(t){
  const cls = t.pnl < 0 ? 'L' : 'W';
  return `<tr class="${cls}" onclick="zoom('${t.e0}','${t.e1}')" title="entry ${t.p0} → exit ${t.p1}">`
    + `<td>${t.i}</td><td>${t.e0.replace('T',' ').slice(0,19)}</td><td>${t.e1.replace('T',' ').slice(0,19)}</td>`
    + `<td>${t.bh}</td><td>${(t.ret*100).toFixed(2)}</td><td>${t.pnl.toFixed(2)}</td><td>${t.r}</td></tr>`;
}
function render(rows){ document.getElementById('tbody').innerHTML = rows.map(cell).join(''); }
let sortKey = 'pnl', asc = true;  // worst (most-negative pnl) first
function sortBy(k){
  if (k === sortKey) asc = !asc; else { sortKey = k; asc = (k === 'pnl' || k === 'ret'); }
  TR.sort((a, b) => (a[k] < b[k] ? -1 : a[k] > b[k] ? 1 : 0) * (asc ? 1 : -1));
  render(TR);
}
sortBy('pnl');
</script>
</body></html>"""


def backtest_report_html(result, bars, *, regime_track=None, metrics_dict=None,
                         title=None, max_candles=8000, live_from_us=None):
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

    annotations = []
    if live_from_us is not None:  # mark where forward (paper / out-of-sample) begins
        x = _iso(live_from_us)
        shapes.append({"type": "line", "xref": "x", "yref": "paper", "x0": x, "x1": x,
                       "y0": 0.0, "y1": 1.0, "line": {"color": "#ff6d00", "width": 2, "dash": "dash"}})
        annotations.append({"x": x, "xref": "x", "yref": "paper", "y": 1.0, "xanchor": "left",
                            "showarrow": False, "text": " ◀ live_from (forward)",
                            "font": {"color": "#ff6d00", "size": 11}})

    ttl = title or f"{result.strategy_id} · {result.symbol}"
    layout = {"title": ttl, "height": 760, "template": "plotly_white", "showlegend": True,
              "xaxis": {"rangeslider": {"visible": False}, "domain": [0, 1]},
              "yaxis": {"domain": [0.32, 1.0], "title": "price"},
              "yaxis2": {"domain": [0.0, 0.24], "title": "equity"},
              "shapes": shapes, "annotations": annotations, "margin": {"t": 50, "l": 60, "r": 20, "b": 30}}

    m = metrics_dict or metrics_mod.summarize(result.equity_curve, trades, equity0=result.equity0)
    metric_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in m.items())

    # bad-case table data + a one-line diagnosis (losers / worst / exit-reason mix)
    trade_js = [{"i": k, "e0": _iso(t.entry_ts), "e1": _iso(t.exit_ts),
                 "p0": round(float(t.entry), 2), "p1": round(float(t.exit), 2),
                 "ret": float(t.return_pct), "pnl": round(float(t.pnl), 2),
                 "bh": int(t.bars_held), "r": t.exit_reason} for k, t in enumerate(trades)]
    if trades:
        losers = [t for t in trades if t.pnl < 0]
        worst = min(trades, key=lambda t: t.pnl)
        hist = ", ".join(f"{k}×{v}" for k, v in Counter(t.exit_reason for t in trades).most_common())
        diag = (f"{len(losers)}/{len(trades)} losers · worst {worst.pnl:.2f} "
                f"({worst.return_pct * 100:.2f}%, held {worst.bars_held} bars, exit={worst.exit_reason}) · exits: {hist}")
    else:
        diag = "no trades"

    legend = (" · regime bands: "
              "<span style='color:#1b5e20'>strong_up</span> "
              "<span style='color:#66bb6a'>up</span> "
              "<span style='color:#9e9e9e'>range</span> "
              "<span style='color:#ef9a9a'>down</span> "
              "<span style='color:#b71c1c'>strong_down</span>")
    fig = json.dumps({"data": [candle, entries, exits, equity], "layout": layout}, default=str)
    return (_TEMPLATE
            .replace("__TITLE__", ttl)
            .replace("__CDN__", _PLOTLY_CDN)
            .replace("__NTRADES__", str(len(trades)))
            .replace("__LEGEND__", legend)
            .replace("__DIAG__", diag)
            .replace("__METRICS__", metric_rows)
            .replace("__FIG__", fig)
            .replace("__TRADES__", json.dumps(trade_js, default=str)))


def write_report(result, bars, path, **kwargs):
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(backtest_report_html(result, bars, **kwargs), encoding="utf-8")
    return p
