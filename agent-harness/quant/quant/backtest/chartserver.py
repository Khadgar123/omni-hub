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
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

_CDN = "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"
_TFS = ["1m", "15m", "30m", "1h", "2h", "4h", "1d"]


def _sec(us) -> int:
    return int(int(us) // 1_000_000)


def _iso(sec: int) -> str:
    return datetime.fromtimestamp(sec, UTC).strftime("%Y-%m-%d %H:%M")


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


def _trades_js(trades):
    out = []
    for k, t in enumerate(trades):
        cost = float(getattr(t, "cost", 0.0))
        notional = abs(float(t.qty)) * float(t.entry)
        costbps = round(cost / notional * 1e4, 1) if notional else 0.0
        out.append({"i": k, "e0": _sec(t.entry_ts), "e1": _sec(t.exit_ts),
                    "e0iso": _iso(_sec(t.entry_ts)), "e1iso": _iso(_sec(t.exit_ts)),
                    "dir": getattr(t, "direction", "long"),
                    "entry": round(float(t.entry), 2), "exit": round(float(t.exit), 2),
                    "pnl": round(float(t.pnl), 2), "ret": float(t.return_pct),
                    "cost": round(cost, 2), "costbps": costbps,
                    "gross": round(float(t.pnl) + cost, 2),   # net + friction = what it'd be at zero cost
                    "bars": int(t.bars_held), "reason": t.exit_reason,
                    "rationale": getattr(t, "entry_rationale", "") or ""})
    return out


def bars_payload(symbol, tf, from_us, to_us, *, root, max_bars=6000):
    """{candle, vol, from, to} for [from_us, to_us] at ``tf``, resampled from the
    store on demand and capped to the most recent ``max_bars``."""
    from quant import resample
    bars = resample.resample(symbol, tf, root=root, source_interval="1s",
                             start=int(from_us), end=int(to_us))
    if len(bars) > max_bars:
        bars = bars[-max_bars:]
    return {"candle": _candles(bars), "vol": _vols(bars),
            "from": _sec(bars[0]["bucket_ts"]) if bars else None,
            "to": _sec(bars[-1]["bucket_ts"]) if bars else None}


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
<th>#</th><th>dir</th><th>entry (UTC)</th><th>exit (UTC)</th><th>bars</th><th>net %</th><th>net pnl</th><th>cost (bps)</th><th>gross</th><th>exit</th><th>trigger reason</th>
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
let curTf=null, cData=[], vData=[], loading=false, focus=null, suppress=false;
const RLO=META.range[0], RHI=META.range[1];
const LOAD_HALF=420, VIS_HALF=140, CHUNK=500;   // bars: load WIDE context, show a comfortable window
function api(tf, from, to){ return fetch(`/api/bars?symbol=${META.symbol}&tf=${tf}&from=${Math.floor(from)}&to=${Math.ceil(to)}`).then(r=>r.json()); }
function fmt(t){ const d=t.dir==='short'?'<span style="color:#ff6d00">SHORT</span>':'<span style="color:#00897b">LONG</span>'; return ` · <b>#${t.i} ${d}</b> ${t.e0iso} → ${t.e1iso} · net ${t.pnl.toFixed(2)} (${(t.ret*100).toFixed(2)}%) · <span style="color:#b71c1c">friction ${t.cost.toFixed(2)} (${t.costbps}bps)</span> · gross ${t.gross.toFixed(2)} · exit:${t.reason} · <i>${t.rationale}</i>`; }
function clamp(t){ return Math.max(RLO, Math.min(RHI, t)); }
function applyData(){
  candle.setData(cData); vol.setData(vData);
  if(!cData.length) return;
  const lo=cData[0].time, hi=cData[cData.length-1].time, s=META.tfsec[curTf];
  const mk=[]; META.trades.forEach(t=>{ const e0=Math.floor(t.e0/s)*s, e1=Math.floor(t.e1/s)*s;
    const isL=t.dir!=='short', xC=(t.reason==='stop'?'#d50000':'#1565c0');
    // long: B(buy,up,below) -> S(sell,down,above).  short: S(sell,down,above) -> B(cover,up,below)
    if(e0>=lo&&e0<=hi) mk.push({time:e0,position:isL?'belowBar':'aboveBar',color:isL?'#00c853':'#ff6d00',shape:isL?'arrowUp':'arrowDown',text:isL?'B':'S'});
    if(e1>=lo&&e1<=hi) mk.push({time:e1,position:isL?'aboveBar':'belowBar',color:xC,shape:isL?'arrowDown':'arrowUp',text:isL?'S':'B'}); });
  mk.sort((a,b)=>a.time-b.time); candle.setMarkers(mk);
  document.getElementById('span').textContent = curTf+': '+fmtDate(lo)+' → '+fmtDate(hi);
}
function mergeInto(d){
  const cm=new Map(cData.map(b=>[b.time,b])); (d.candle||[]).forEach(b=>cm.set(b.time,b));
  cData=Array.from(cm.values()).sort((a,b)=>a.time-b.time);
  const vm=new Map(vData.map(b=>[b.time,b])); (d.vol||[]).forEach(b=>vm.set(b.time,b));
  vData=Array.from(vm.values()).sort((a,b)=>a.time-b.time);
  if(cData.length>20000){ cData=cData.slice(-20000); vData=vData.slice(-20000); }   // bound memory
}
async function loadCentered(tf, center){          // fresh wide window centered on a time (the auto-load base)
  const s=META.tfsec[tf];
  const d=await api(tf, clamp(center-LOAD_HALF*s), clamp(center+LOAD_HALF*s));
  cData=d.candle||[]; vData=d.vol||[]; applyData();
}
async function setTf(tf){                          // keep the SAME time center across timeframes (consistent before/after)
  if(curTf){ const vr=chart.timeScale().getVisibleRange(); if(vr&&vr.from!=null) focus=(vr.from+vr.to)/2; }
  curTf=tf; document.querySelectorAll('#bar button').forEach(b=>b.style.fontWeight=(b.dataset.tf===tf?'700':'400'));
  const s=META.tfsec[tf]; const c = (focus!=null ? focus : RHI - VIS_HALF*s);
  await loadCentered(tf, c);
  suppress=true; chart.timeScale().setVisibleRange({from:clamp(c-VIS_HALF*s), to:clamp(c+VIS_HALF*s)}); suppress=false;
}
async function jump(i){                            // a JUMP within the auto-load base: re-center on the trade with context
  const t=META.trades[i]; focus=(t.e0+t.e1)/2;
  await loadCentered(curTf, focus);
  const s=META.tfsec[curTf], pad=Math.max(t.e1-t.e0, VIS_HALF*s);   // trade visible WITH context, never too short
  suppress=true; chart.timeScale().setVisibleRange({from:clamp(t.e0-pad), to:clamp(t.e1+pad)}); suppress=false;
  document.getElementById('info').innerHTML = fmt(t);
}
chart.timeScale().subscribeVisibleLogicalRangeChange(async lr=>{   // continuous auto-load on pan (both directions)
  if(!lr || loading || suppress || !cData.length) return;
  const s=META.tfsec[curTf];
  if(lr.from < 6 && cData[0].time > RLO){
    loading=true; const oldest=cData[0].time;
    mergeInto(await api(curTf, clamp(oldest-CHUNK*s), oldest-s)); applyData(); loading=false;
  } else if(lr.to > cData.length-6 && cData[cData.length-1].time < RHI){
    loading=true; const newest=cData[cData.length-1].time;
    mergeInto(await api(curTf, newest+s, clamp(newest+CHUNK*s))); applyData(); loading=false;
  }
});
chart.subscribeCrosshairMove(p=>{ if(!p||!p.time) return; const s=META.tfsec[curTf], f=Math.floor(p.time/s)*s;
  const t=META.trades.find(x=>Math.floor(x.e0/s)*s===f || Math.floor(x.e1/s)*s===f); if(t) document.getElementById('info').innerHTML=fmt(t); });
document.getElementById('tbody').innerHTML = META.trades.map(t=>
  `<tr class="${t.pnl<0?'L':'W'}" onclick="jump(${t.i})"><td>${t.i}</td><td style="color:${t.dir==='short'?'#ff6d00':'#00897b'};font-weight:600">${t.dir==='short'?'S':'L'}</td><td>${t.e0iso}</td><td>${t.e1iso}</td><td>${t.bars}</td><td>${(t.ret*100).toFixed(2)}</td><td>${t.pnl.toFixed(2)}</td><td style="color:#b71c1c">${t.cost.toFixed(2)} (${t.costbps})</td><td>${t.gross.toFixed(2)}</td><td>${t.reason}</td><td>${t.rationale}</td></tr>`).join('');
setTf('__DEFTF__');
</script></body></html>"""


def build_page_html(meta):
    btns = " ".join(f'<button data-tf="{tf}" onclick="setTf(\'{tf}\')">{tf}</button>' for tf in meta["tfs"])
    return (_PAGE
            .replace("__TITLE__", meta.get("title", meta["symbol"]))
            .replace("__CDN__", _CDN)
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
    start_sec = _sec(market_store.parse_ts(start)) if start else _sec(res.equity_curve[0][0])
    end_sec = _sec(market_store.parse_ts(end, end_of_day=True)) if end else _sec(res.equity_curve[-1][0])
    meta = {"symbol": symbol, "title": f"{strategy} · {symbol}", "tfs": tfs,
            "tfsec": {tf: market_store.freq_to_seconds(tf) for tf in tfs},
            "range": [start_sec, end_sec], "default_tf": m.get("timeframe", "1h"),
            "trades": _trades_js(res.trades)}
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
