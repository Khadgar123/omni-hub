"""Local interactive "TradingView" — TradingView **Lightweight Charts** (Apache-2.0,
the same open-source engine behind tradingview.com) wired to OUR bars + backtest trades.

Open the emitted HTML in any browser and FREELY ANALYZE (this is the answer to "I
don't want a screenshot, I want to explore"):
  * pan / zoom smoothly across all history (TradingView-grade);
  * switch timeframe live (e.g. 15m / 1h / 4h / 1d) — same trades re-anchored;
  * entry ▲ / exit ▼ markers (exit red=stop / blue=signal);
  * hover a trade → read WHY it fired (the StrategyIntent rationale the engine now
    records per trade) + pnl / exit reason;
  * click a row in the trades table → the chart jumps & zooms to that trade.

Self-contained single file; the chart library loads from CDN. Complements the Plotly
report (which owns regime bands + the worst-first bad-case panel) — this is the
free-exploration surface a static snapshot can't be.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

_CDN = "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"


def _sec(us) -> int:
    return int(int(us) // 1_000_000)


def _iso(sec: int) -> str:
    return datetime.fromtimestamp(sec, UTC).strftime("%Y-%m-%d %H:%M")


def _tf_seconds(tf: str) -> int:
    from quant.market_store import freq_to_seconds
    return freq_to_seconds(tf)


def _candles(bars):
    return [{"time": _sec(b["bucket_ts"]), "open": float(b["open"]), "high": float(b["high"]),
             "low": float(b["low"]), "close": float(b["close"])} for b in bars]


def _vols(bars):
    out = []
    for b in bars:
        up = float(b["close"]) >= float(b["open"])
        out.append({"time": _sec(b["bucket_ts"]), "value": float(b.get("volume", 0.0)),
                    "color": "rgba(38,166,154,0.4)" if up else "rgba(239,83,80,0.4)"})
    return out


def _markers(trades, tf_sec, lo=None, hi=None):
    def _in(t):
        return lo is None or lo <= t <= hi
    out = []
    for t in trades:
        e0 = (_sec(t.entry_ts) // tf_sec) * tf_sec
        e1 = (_sec(t.exit_ts) // tf_sec) * tf_sec
        if _in(e0):
            out.append({"time": e0, "position": "belowBar", "color": "#00c853", "shape": "arrowUp", "text": "B"})
        if _in(e1):
            col = "#d50000" if t.exit_reason == "stop" else "#1565c0"
            out.append({"time": e1, "position": "aboveBar", "color": col, "shape": "arrowDown", "text": "S"})
    out.sort(key=lambda m: m["time"])
    return out


def _trades_js(trades):
    return [{"i": k, "e0": _sec(t.entry_ts), "e1": _sec(t.exit_ts),
             "e0iso": _iso(_sec(t.entry_ts)), "e1iso": _iso(_sec(t.exit_ts)),
             "entry": round(float(t.entry), 2), "exit": round(float(t.exit), 2),
             "pnl": round(float(t.pnl), 2), "ret": float(t.return_pct),
             "bars": int(t.bars_held), "reason": t.exit_reason,
             "rationale": getattr(t, "entry_rationale", "") or ""} for k, t in enumerate(trades)]


_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><title>__TITLE__</title>
<script src="__CDN__"></script>
<style>body{font-family:system-ui,sans-serif;margin:14px;color:#222}
h3{margin:0 0 6px} #bar{font-size:13px;color:#555;margin:6px 0}
#bar button{font:inherit;margin-right:4px;padding:3px 10px;cursor:pointer;border:1px solid #bbb;background:#f7f7f7;border-radius:4px}
#info{margin-left:8px}
table{border-collapse:collapse;font-size:12px;margin-top:8px} td,th{border:1px solid #ddd;padding:2px 8px}
#ttab th{position:sticky;top:0;background:#f5f5f5} #ttab td{text-align:right} #ttab td:nth-child(2),#ttab td:nth-child(3),#ttab td:last-child{text-align:left}
#ttab tbody tr{cursor:pointer} #ttab tbody tr:hover{outline:2px solid #5e35b1}
tr.L{background:#fdecea} tr.W{background:#e8f5e9} .wrap{max-height:300px;overflow:auto;border:1px solid #eee;margin-top:6px}</style>
</head><body>
<h3>__TITLE__</h3>
<div id="bar">timeframe: __TFBTNS__ <span id="info">— hover a marker / click a trade row to inspect</span></div>
<div id="chart" style="height:560px"></div>
<div class="wrap"><table id="ttab"><thead><tr>
<th>#</th><th>entry (UTC)</th><th>exit (UTC)</th><th>bars</th><th>ret %</th><th>pnl</th><th>exit</th><th>trigger reason (背驰/support/…)</th>
</tr></thead><tbody id="tbody"></tbody></table></div>
<script>
const DATA = __DATA__, TRADES = __TRADES__;
const chart = LightweightCharts.createChart(document.getElementById('chart'), {
  autoSize: true, layout:{background:{color:'#fff'},textColor:'#333'},
  grid:{vertLines:{color:'#f0f0f0'},horzLines:{color:'#f0f0f0'}},
  timeScale:{timeVisible:true, secondsVisible:false, borderColor:'#ccc'},
  rightPriceScale:{borderColor:'#ccc'}, crosshair:{mode:0}});
const candle = chart.addCandlestickSeries({upColor:'#26a69a',downColor:'#ef5350',borderVisible:false,
  wickUpColor:'#26a69a',wickDownColor:'#ef5350'});
const vol = chart.addHistogramSeries({priceFormat:{type:'volume'}, priceScaleId:''});
vol.priceScale().applyOptions({scaleMargins:{top:0.84, bottom:0}});
let curTf=null, tmap={};
function fmt(t){ return ` · <b>#${t.i}</b> ${t.e0iso} → ${t.e1iso} · pnl ${t.pnl.toFixed(2)} (${(t.ret*100).toFixed(2)}%) · exit:${t.reason} · <i>${t.rationale}</i>`; }
function setTf(tf){
  curTf=tf;
  candle.setData(DATA.candle[tf]); vol.setData(DATA.vol[tf]); candle.setMarkers(DATA.markers[tf]);
  const s=DATA.tfsec[tf]; tmap={}; TRADES.forEach(t=>{ tmap[Math.floor(t.e0/s)*s]=t; });
  chart.timeScale().fitContent();
  document.querySelectorAll('#bar button').forEach(b=>b.style.fontWeight=(b.dataset.tf===tf?'700':'400'));
}
chart.subscribeCrosshairMove(p=>{ if(p && p.time && tmap[p.time]) document.getElementById('info').innerHTML = fmt(tmap[p.time]); });
function jump(i){ const t=TRADES[i], s=DATA.tfsec[curTf], pad=Math.max(s*8,(t.e1-t.e0)*2);
  chart.timeScale().setVisibleRange({from:t.e0-pad, to:t.e1+pad}); document.getElementById('info').innerHTML=fmt(t); }
document.getElementById('tbody').innerHTML = TRADES.map(t=>
  `<tr class="${t.pnl<0?'L':'W'}" onclick="jump(${t.i})"><td>${t.i}</td><td>${t.e0iso}</td><td>${t.e1iso}</td><td>${t.bars}</td><td>${(t.ret*100).toFixed(2)}</td><td>${t.pnl.toFixed(2)}</td><td>${t.reason}</td><td>${t.rationale}</td></tr>`).join('');
setTf('__DEFTF__');
</script></body></html>"""


def build_chart_html(result, bars_by_tf, *, title=None, default_tf=None, max_bars=15000):
    tfs = [tf for tf in bars_by_tf if bars_by_tf[tf]]
    if not tfs:
        raise ValueError("no bars in any timeframe")
    default_tf = default_tf if default_tf in tfs else tfs[0]
    trades = list(result.trades)
    data = {"candle": {}, "vol": {}, "markers": {}, "tfsec": {}}
    for tf in tfs:
        bars = bars_by_tf[tf]
        if max_bars and len(bars) > max_bars:   # keep the file usable (1m over years = millions)
            bars = bars[-max_bars:]
        sec = _tf_seconds(tf)
        lo, hi = _sec(bars[0]["bucket_ts"]), _sec(bars[-1]["bucket_ts"])
        data["candle"][tf] = _candles(bars)
        data["vol"][tf] = _vols(bars)
        data["markers"][tf] = _markers(trades, sec, lo, hi)   # only markers within the window
        data["tfsec"][tf] = sec
    btns = " ".join(f'<button data-tf="{tf}" onclick="setTf(\'{tf}\')">{tf}</button>' for tf in tfs)
    ttl = title or f"{getattr(result, 'strategy_id', '?')} · {getattr(result, 'symbol', '?')}"
    return (_TEMPLATE
            .replace("__TITLE__", ttl)
            .replace("__CDN__", _CDN)
            .replace("__TFBTNS__", btns)
            .replace("__DEFTF__", default_tf)
            .replace("__DATA__", json.dumps(data, default=str, ensure_ascii=False))
            .replace("__TRADES__", json.dumps(_trades_js(trades), default=str, ensure_ascii=False)))


def write_chart(result, bars_by_tf, path, **kwargs):
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_chart_html(result, bars_by_tf, **kwargs), encoding="utf-8")
    return p


def main(argv=None):
    import argparse

    from quant import resample
    from quant.backtest import harness

    from quant import market_store
    p = argparse.ArgumentParser(prog="quant.backtest.chart", description="interactive TradingView-style chart")
    p.add_argument("--strategy", required=True)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--from", dest="start", default=None)
    p.add_argument("--to", dest="end", default=None)
    p.add_argument("--tfs", default="1m,15m,30m,1h,2h,4h,1d", help="comma-separated timeframes to embed")
    p.add_argument("--htf", default="1d")
    p.add_argument("--confirm", default="4h")
    p.add_argument("--root", default=None)
    p.add_argument("--max-bars", dest="max_bars", type=int, default=15000,
                   help="cap embedded bars per timeframe (keeps 1m over years from bloating)")
    p.add_argument("--out", default="~/quant/reports/interactive.html")
    a = p.parse_args(argv)
    root = Path(a.root).expanduser() if a.root else market_store.DEFAULT_ROOT
    res, m = harness.run(a.strategy, a.symbol, root=root, start=a.start, end=a.end,
                         htf=a.htf, confirm=a.confirm)
    if res is None:
        print(json.dumps(m)); return 1
    tfs = [t.strip() for t in a.tfs.split(",") if t.strip()]
    # bound each TF's resample to ~max_bars*tf so fine TFs (1m) don't pull millions of rows
    start_us = market_store.parse_ts(a.start) if a.start else None
    end_us = market_store.parse_ts(a.end, end_of_day=True) if a.end else None
    if end_us is None:
        d1 = resample.resample(a.symbol, "1d", root=root, source_interval="1s", start=a.start, end=a.end)
        end_us = int(d1[-1]["bucket_ts"]) if d1 else None
    bars_by_tf = {}
    for tf in tfs:
        sec = market_store.freq_to_seconds(tf)
        tf_start = start_us
        if end_us is not None:
            cand = end_us - int(a.max_bars * sec * 1_000_000 * 1.1)
            tf_start = cand if (start_us is None or cand > start_us) else start_us
        bars_by_tf[tf] = resample.resample(a.symbol, tf, root=root, source_interval="1s",
                                           start=tf_start, end=a.end)
    out = write_chart(res, bars_by_tf, Path(a.out).expanduser(), max_bars=a.max_bars,
                      title=f"{a.strategy} · {a.symbol}", default_tf=m.get("timeframe", "1h"))
    print("interactive chart:", out, "| trades:", len(res.trades), "| tfs:", ",".join(tfs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
