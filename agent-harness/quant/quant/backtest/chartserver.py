"""Dynamic, server-backed interactive chart — bars loaded ON DEMAND.

The static chart (quant.backtest.chart) must pre-embed every bar, so it caps each
timeframe and can't show 1m over years. This serves bars from the 1s store on
demand: a tiny stdlib HTTP server exposes ``/api/bars``, and the page (TradingView
Lightweight Charts) fetches only the visible window, lazy-loads history as you
scroll, and re-fetches a trade's window (incl. 1m for OLD trades) when you click it.
Any timeframe, any date, no file bloat, fully free analysis.

Run:
    python -m quant.backtest.chartserver --strategy structure_reversal_v2 \
        --from 2024-06 --to 2026-04 [--symbol BTCUSDT --port 8787]
then open http://127.0.0.1:8787  (Ctrl-C to stop)
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from quant.backtest import chart as _chart

_TFS = ["1m", "15m", "30m", "1h", "2h", "4h", "1d"]


def bars_payload(symbol, tf, from_us, to_us, *, root, max_bars=6000):
    """{candle, vol, from, to} for [from_us, to_us] at ``tf``, resampled from the
    store on demand and capped to the most recent ``max_bars``."""
    from quant import resample
    bars = resample.resample(symbol, tf, root=root, source_interval="1s",
                             start=int(from_us), end=int(to_us))
    if len(bars) > max_bars:
        bars = bars[-max_bars:]
    return {"candle": _chart._candles(bars), "vol": _chart._vols(bars),
            "from": _chart._sec(bars[0]["bucket_ts"]) if bars else None,
            "to": _chart._sec(bars[-1]["bucket_ts"]) if bars else None}


_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>__TITLE__</title>
<script src="__CDN__"></script>
<style>body{font-family:system-ui,sans-serif;margin:14px;color:#222}
h3{margin:0 0 6px} #bar{font-size:13px;color:#555;margin:6px 0}
#bar button{font:inherit;margin-right:4px;padding:3px 10px;cursor:pointer;border:1px solid #bbb;background:#f7f7f7;border-radius:4px}
table{border-collapse:collapse;font-size:12px;margin-top:8px} td,th{border:1px solid #ddd;padding:2px 8px}
#ttab th{position:sticky;top:0;background:#f5f5f5} #ttab td{text-align:right} #ttab td:nth-child(2),#ttab td:nth-child(3),#ttab td:last-child{text-align:left}
#ttab tbody tr{cursor:pointer} #ttab tbody tr:hover{outline:2px solid #5e35b1}
tr.L{background:#fdecea} tr.W{background:#e8f5e9} .wrap{max-height:300px;overflow:auto;border:1px solid #eee;margin-top:6px}</style>
</head><body>
<h3>__TITLE__ <span style="font-size:12px;color:#2e7d32">· dynamic (bars load on demand — any TF, any date)</span></h3>
<div id="bar">timeframe: __TFBTNS__ <span id="span" style="color:#1565c0;font-weight:600"></span><span id="info"> — scroll left to lazy-load history; click a trade row to fetch its window</span></div>
<div id="chart" style="height:560px"></div>
<div class="wrap"><table id="ttab"><thead><tr>
<th>#</th><th>entry (UTC)</th><th>exit (UTC)</th><th>bars</th><th>ret %</th><th>pnl</th><th>exit</th><th>trigger reason</th>
</tr></thead><tbody id="tbody"></tbody></table></div>
<script>
const META = __META__;
const _p = n => String(n).padStart(2,'0');
function fmtDate(t){ const d=new Date(t*1000); return d.getUTCFullYear()+'-'+_p(d.getUTCMonth()+1)+'-'+_p(d.getUTCDate()); }
function fmtFull(t){ const d=new Date(t*1000); return fmtDate(t)+' '+_p(d.getUTCHours())+':'+_p(d.getUTCMinutes())+' UTC'; }
const chart = LightweightCharts.createChart(document.getElementById('chart'), {
  autoSize:true, layout:{background:{color:'#fff'},textColor:'#333'},
  localization:{locale:'en-US', timeFormatter:fmtFull},
  grid:{vertLines:{color:'#f0f0f0'},horzLines:{color:'#f0f0f0'}},
  timeScale:{timeVisible:true, secondsVisible:false, borderColor:'#ccc', tickMarkFormatter:(t)=>fmtDate(t)},
  rightPriceScale:{borderColor:'#ccc'}, crosshair:{mode:0}});
const candle = chart.addCandlestickSeries({upColor:'#26a69a',downColor:'#ef5350',borderVisible:false,wickUpColor:'#26a69a',wickDownColor:'#ef5350'});
const vol = chart.addHistogramSeries({priceFormat:{type:'volume'}, priceScaleId:''});
vol.priceScale().applyOptions({scaleMargins:{top:0.84, bottom:0}});
let curTf=null, cData=[], vData=[], loading=false, tmap={};
async function api(tf, from, to){ const r=await fetch(`/api/bars?symbol=${META.symbol}&tf=${tf}&from=${Math.floor(from)}&to=${Math.ceil(to)}`); return r.json(); }
function fmt(t){ return ` · <b>#${t.i}</b> ${t.e0iso} → ${t.e1iso} · pnl ${t.pnl.toFixed(2)} (${(t.ret*100).toFixed(2)}%) · exit:${t.reason} · <i>${t.rationale}</i>`; }
function refresh(){
  candle.setData(cData); vol.setData(vData);
  const s=META.tfsec[curTf]; tmap={}; const lo=cData.length?cData[0].time:0, hi=cData.length?cData[cData.length-1].time:0;
  const mk=[]; META.trades.forEach(t=>{ const e0=Math.floor(t.e0/s)*s, e1=Math.floor(t.e1/s)*s;
    if(e0>=lo&&e0<=hi){ mk.push({time:e0,position:'belowBar',color:'#00c853',shape:'arrowUp',text:'B'}); tmap[e0]=t; }
    if(e1>=lo&&e1<=hi){ mk.push({time:e1,position:'aboveBar',color:(t.reason==='stop'?'#d50000':'#1565c0'),shape:'arrowDown',text:'S'}); } });
  mk.sort((a,b)=>a.time-b.time); candle.setMarkers(mk);
  if(cData.length) document.getElementById('span').textContent = curTf+': '+fmtDate(lo)+' → '+fmtDate(hi);
}
async function setTf(tf){
  const vr = curTf ? chart.timeScale().getVisibleRange() : null;
  curTf=tf; const s=META.tfsec[tf];
  let from, to;
  if(vr && vr.from!=null){ from=vr.from; to=vr.to; } else { to=META.range[1]; from=to-1500*s; }
  const d=await api(tf, Math.max(from, META.range[0]-s), to);
  cData=d.candle; vData=d.vol; refresh();
  if(vr && vr.from!=null && cData.length){ chart.timeScale().setVisibleRange({from:Math.max(vr.from,cData[0].time), to:Math.min(vr.to,cData[cData.length-1].time)}); }
  else chart.timeScale().fitContent();
  document.querySelectorAll('#bar button').forEach(b=>b.style.fontWeight=(b.dataset.tf===tf?'700':'400'));
}
chart.timeScale().subscribeVisibleLogicalRangeChange(async lr=>{
  if(!lr || loading || !cData.length) return;
  if(lr.from < 8){                                  // near the left edge -> load older history
    const s=META.tfsec[curTf], oldest=cData[0].time; if(oldest <= META.range[0]) return;
    loading=true;
    const d=await api(curTf, oldest-1500*s, oldest-s);
    if(d.candle && d.candle.length){ cData=d.candle.concat(cData); vData=d.vol.concat(vData); refresh(); }
    loading=false;
  }
});
chart.subscribeCrosshairMove(p=>{ if(p && p.time && tmap[p.time]) document.getElementById('info').innerHTML = fmt(tmap[p.time]); });
async function jump(i){                              // fetch the trade's window at the current TF (works for ANY date, incl 1m of old trades)
  const t=META.trades[i], s=META.tfsec[curTf], pad=Math.max(s*25,(t.e1-t.e0)*3);
  const d=await api(curTf, t.e0-pad, t.e1+pad);
  cData=d.candle; vData=d.vol; refresh();
  if(cData.length) chart.timeScale().setVisibleRange({from:cData[0].time, to:cData[cData.length-1].time});
  document.getElementById('info').innerHTML = fmt(t);
}
document.getElementById('tbody').innerHTML = META.trades.map(t=>
  `<tr class="${t.pnl<0?'L':'W'}" onclick="jump(${t.i})"><td>${t.i}</td><td>${t.e0iso}</td><td>${t.e1iso}</td><td>${t.bars}</td><td>${(t.ret*100).toFixed(2)}</td><td>${t.pnl.toFixed(2)}</td><td>${t.reason}</td><td>${t.rationale}</td></tr>`).join('');
setTf('__DEFTF__');
</script></body></html>"""


def build_page_html(meta):
    btns = " ".join(f'<button data-tf="{tf}" onclick="setTf(\'{tf}\')">{tf}</button>' for tf in meta["tfs"])
    return (_PAGE
            .replace("__TITLE__", meta.get("title", meta["symbol"]))
            .replace("__CDN__", _chart._CDN)
            .replace("__TFBTNS__", btns)
            .replace("__DEFTF__", meta.get("default_tf", meta["tfs"][0]))
            .replace("__META__", json.dumps(meta, ensure_ascii=False, default=str)))


def serve(strategy, symbol="BTCUSDT", *, start=None, end=None, root=None, port=8787,
          htf="1d", confirm="4h", tfs=None):
    import http.server
    import socketserver

    from quant import market_store
    from quant.backtest import harness

    root = root if root is not None else market_store.DEFAULT_ROOT
    tfs = tfs or _TFS
    res, m = harness.run(strategy, symbol, root=root, start=start, end=end, htf=htf, confirm=confirm)
    if res is None:
        raise SystemExit(f"no data: {m}")
    start_sec = _chart._sec(market_store.parse_ts(start)) if start else _chart._sec(res.equity_curve[0][0])
    end_sec = _chart._sec(market_store.parse_ts(end, end_of_day=True)) if end else _chart._sec(res.equity_curve[-1][0])
    meta = {"symbol": symbol, "title": f"{strategy} · {symbol}", "tfs": tfs,
            "tfsec": {tf: market_store.freq_to_seconds(tf) for tf in tfs},
            "range": [start_sec, end_sec], "default_tf": m.get("timeframe", "1h"),
            "trades": _chart._trades_js(res.trades)}
    page = build_page_html(meta).encode("utf-8")

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                return self._send(page, "text/html; charset=utf-8")
            if u.path == "/api/bars":
                q = parse_qs(u.query)
                tf = q.get("tf", ["1h"])[0]
                fr = int(float(q.get("from", ["0"])[0])) * 1_000_000
                to = int(float(q.get("to", ["0"])[0])) * 1_000_000
                payload = bars_payload(q.get("symbol", [symbol])[0], tf, fr, to, root=root)
                return self._send(json.dumps(payload).encode("utf-8"), "application/json")
            self.send_response(404)
            self.end_headers()

    with socketserver.ThreadingTCPServer(("127.0.0.1", port), H) as srv:
        print(f"interactive chart: http://127.0.0.1:{port}   ({len(res.trades)} trades, {strategy})  — Ctrl-C to stop")
        srv.serve_forever()


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="quant.backtest.chartserver", description="dynamic on-demand chart server")
    p.add_argument("--strategy", required=True)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--from", dest="start", default=None)
    p.add_argument("--to", dest="end", default=None)
    p.add_argument("--htf", default="1d")
    p.add_argument("--confirm", default="4h")
    p.add_argument("--root", default=None)
    p.add_argument("--port", type=int, default=8787)
    a = p.parse_args(argv)
    from pathlib import Path
    serve(a.strategy, a.symbol, start=a.start, end=a.end,
          root=Path(a.root).expanduser() if a.root else None, port=a.port, htf=a.htf, confirm=a.confirm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
