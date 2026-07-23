"""Analog viewer — a localhost two-panel chart tool (stdlib http.server + lightweight-charts).

TOP panel:  full history at ANY timeframe; click two points to select ANY query window.
BOTTOM panel: the matched analogs (ranked by z-normalized similarity); click one to view that
              historical period at ANY timeframe, with dynamic (pan-left) history loading.

Engine = quant.analog (MASS z-normalized matching). Data = the local Parquet store (deep history).

Run:  python -m quant.analog_viewer --port 8800   then open http://127.0.0.1:8800
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import analog
from . import market_store as ms

_TF_SECS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200,
            "4h": 14400, "1d": 86400}
_SYMS = ("BTCUSDT", "ETHUSDT")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _candles(symbol: str, tf: str, end_us: int | None, limit: int) -> list[dict]:
    """Up to ``limit`` bars ending at/just before ``end_us`` (µs); for the bottom-chart lazy-load."""
    secs = _TF_SECS.get(tf, 3600)
    end_dt = datetime.fromtimestamp(end_us / 1e6, tz=timezone.utc) if end_us else datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(seconds=int(limit * secs * 1.25))
    bars = ms.bars(symbol, tf, _iso(start_dt), _iso(end_dt + timedelta(days=1)))
    if end_us:
        bars = [b for b in bars if b["bucket_ts"] <= end_us]
    return bars[-limit:]


def _lw(bars: list[dict]) -> list[dict]:
    """lightweight-charts candle rows: time in UNIX seconds."""
    return [{"time": int(b["bucket_ts"] // 1_000_000), "open": b["open"], "high": b["high"],
             "low": b["low"], "close": b["close"]} for b in bars]


def _match(symbol: str, tf: str, start_us: int, end_us: int, k: int, mode: str) -> dict:
    """Run analog matching for the query window [start_us, end_us] at ``tf``. Returns ranked analogs."""
    allbars = ms.bars(symbol, tf, "2019-01-01", "2100-01-01")
    q = [b for b in allbars if start_us <= b["bucket_ts"] <= end_us]
    if len(q) < 8:
        return {"error": "window too small (need >=8 bars)", "analogs": []}
    qc = [b["close"] for b in q]
    win = len(qc)
    res = analog.match_level(symbol, tf, win, k=k + 3, query=qc, bars=allbars)
    bars, matches = res["bars"], res["matches"]
    out = []
    for pos, dist in matches:
        s_ts, e_ts = bars[pos]["bucket_ts"], bars[pos + win - 1]["bucket_ts"]
        if e_ts >= start_us and s_ts <= end_us:                 # skip overlap with the query itself
            continue
        out.append({"rank": len(out) + 1, "dist": round(dist, 2),
                    "start_ts": s_ts, "end_ts": e_ts,
                    "start": datetime.fromtimestamp(s_ts / 1e6, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "win": win})
        if len(out) >= k:
            break
    return {"symbol": symbol, "tf": tf, "win": win, "analogs": out}


_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Analog Viewer</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
 body{margin:0;background:#0d1117;color:#c9d1d9;font:13px -apple-system,Segoe UI,Roboto,sans-serif}
 .bar{padding:6px 10px;background:#161b22;border-bottom:1px solid #30363d;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
 button{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:4px;padding:3px 8px;cursor:pointer;font-size:12px}
 button.on{background:#1f6feb;border-color:#1f6feb;color:#fff}
 .panel{position:relative}
 #top{height:42vh}#bottom{height:38vh}
 h4{margin:0 8px 0 0;color:#8b949e;font-weight:600}
 #analogs{display:flex;gap:4px;overflow-x:auto;padding:4px 8px;background:#0d1117;border-bottom:1px solid #30363d}
 .chip{white-space:nowrap;padding:3px 8px;border:1px solid #30363d;border-radius:4px;cursor:pointer;font-size:12px;background:#161b22}
 .chip.on{background:#238636;border-color:#238636;color:#fff}
 .hint{color:#8b949e;font-size:11px}
 select{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:4px;padding:2px 5px}
</style></head><body>

<div class=bar>
 <h4>① 全历史(选窗口)</h4>
 <select id=symT>__SYMS__</select>
 <span id=tfsT></span>
 <button id=btnSel onclick="setSelMode(!selMode)">🔲框选</button>
 <button onclick=clearSel()>清除选择</button>
 <button class=on id=btnMatch onclick=doMatch()>匹配相似 ▶</button>
 <select id=mode><option value=level>单级别</option><option value=cascade>多级别cascade</option></select>
 <span>K=<input id=kk type=number value=8 min=3 max=20 style="width:42px"></span>
 <span class=hint id=selinfo>在上图点两下选起止 → 匹配</span>
</div>
<div class=panel><div id=top></div></div>

<div id=analogs></div>
<div class=bar>
 <h4>② 匹配结果(按相似度,可切级别)</h4>
 <span id=tfsB></span>
 <span class=hint id=binfo>点上面的相似度chip加载</span>
</div>
<div class=panel><div id=bottom></div></div>

<script>
const LWC=LightweightCharts;
function bj(t){const d=new Date((t+8*3600)*1000),p=n=>String(n).padStart(2,'0');
  return {y:d.getUTCFullYear(),mo:p(d.getUTCMonth()+1),da:p(d.getUTCDate()),h:p(d.getUTCHours()),mi:p(d.getUTCMinutes())};}
function mk(id){const c=LWC.createChart(document.getElementById(id),{layout:{background:{color:'#0d1117'},textColor:'#c9d1d9'},
  grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},
  localization:{timeFormatter:t=>{const b=bj(t);return `${b.y}/${b.mo}/${b.da} ${b.h}:${b.mi} 北京`;}},
  timeScale:{timeVisible:true,secondsVisible:false,
    tickMarkFormatter:(t,tt)=>{const b=bj(t);return tt>=3?`${b.h}:${b.mi}`:`${b.y}/${b.mo}/${b.da}`;}},
  rightPriceScale:{borderColor:'#30363d'},crosshair:{mode:0}});
  const s=c.addCandlestickSeries({upColor:'#26a69a',downColor:'#ef5350',borderVisible:false,wickUpColor:'#26a69a',wickDownColor:'#ef5350'});
  return {c,s};}
const TOP=mk('top'), BOT=mk('bottom');
const TFS=['1m','5m','15m','30m','1h','4h','1d'];
let tfT='4h', tfB='4h', symT='BTCUSDT';
let selStart=null, selEnd=null, selMode=false;   // query window (unix sec)
let curAnalog=null;                        // {start_ts,end_ts,win} of selected analog (µs)

function tfButtons(elId, cur, cb){const e=document.getElementById(elId); e.innerHTML='';
  TFS.forEach(t=>{const b=document.createElement('button'); b.textContent=t; if(t===cur)b.className='on';
    b.onclick=()=>{cb(t); [...e.children].forEach(x=>x.className=x.textContent===t?'on':'');}; e.appendChild(b);});}

async function getBars(sym,tf,end,limit){const u=`/api/bars?symbol=${sym}&tf=${tf}&limit=${limit||400}`+(end?`&end=${end}`:'');
  return (await (await fetch(u)).json()).bars;}

let topBars=[];
async function loadTop(){topBars=await getBars(symT,tfT,null,500); TOP.s.setData(topBars);
  TOP.s.setMarkers([]); document.getElementById('selinfo').textContent='在上图点两下选起止 → 匹配';}
let botBars=[];
async function loadBot(end){const e=end|| (curAnalog? Math.floor(curAnalog.end_ts/1e6)+ _spanSec()*40 : null);
  botBars=await getBars(symT,tfB,e? e*1e6:null,400); BOT.s.setData(botBars); highlightAnalog();}
function _spanSec(){const m={'1m':60,'5m':300,'15m':900,'30m':1800,'1h':3600,'4h':14400,'1d':86400};return m[tfB]||3600;}
function highlightAnalog(){if(!curAnalog){BOT.s.setMarkers([]);return;}
  BOT.s.setMarkers([{time:Math.floor(curAnalog.start_ts/1e6),position:'belowBar',color:'#e3b341',shape:'arrowUp',text:'analog起'},
                    {time:Math.floor(curAnalog.end_ts/1e6),position:'aboveBar',color:'#e3b341',shape:'arrowDown',text:'此后→'}]);}

// ===== 选窗口:鼠标横向拖拽框选(十字框选)+ 两点点击备用 =====
const topEl=document.getElementById('top'); topEl.style.position='relative';
const selBox=document.createElement('div');
selBox.style.cssText='position:absolute;top:0;bottom:26px;background:rgba(31,111,235,.18);border-left:2px solid #1f6feb;border-right:2px solid #1f6feb;pointer-events:none;display:none;z-index:5';
topEl.appendChild(selBox);
let dragging=false, x0=0;
function setSelMode(on){selMode=on; TOP.c.applyOptions({handleScroll:!on,handleScale:!on});
  document.getElementById('btnSel').className=on?'on':''; topEl.style.cursor=on?'crosshair':'default';
  document.getElementById('selinfo').textContent=on?'🔲 按住鼠标在上图横向拖动框选窗口':'拖拽框选 或 点两下选起止 → 匹配';}
function redrawSel(){ if(selStart===null||selEnd===null){selBox.style.display='none';return;}
  const a=TOP.c.timeScale().timeToCoordinate(selStart), b=TOP.c.timeScale().timeToCoordinate(selEnd);
  if(a==null||b==null){selBox.style.display='none';return;}
  selBox.style.left=Math.min(a,b)+'px'; selBox.style.width=Math.max(2,Math.abs(b-a))+'px'; selBox.style.display='block';}
topEl.addEventListener('mousedown',e=>{ if(!selMode||e.button!==0)return; dragging=true;
  const r=topEl.getBoundingClientRect(); x0=e.clientX-r.left; selBox.style.left=x0+'px'; selBox.style.width='0px'; selBox.style.display='block'; e.preventDefault();});
window.addEventListener('mousemove',e=>{ if(!dragging)return; const r=topEl.getBoundingClientRect();
  const x1=e.clientX-r.left; selBox.style.left=Math.min(x0,x1)+'px'; selBox.style.width=Math.abs(x1-x0)+'px';});
window.addEventListener('mouseup',e=>{ if(!dragging)return; dragging=false; const r=topEl.getBoundingClientRect();
  const x1=e.clientX-r.left; const t0=TOP.c.timeScale().coordinateToTime(Math.min(x0,x1)), t1=TOP.c.timeScale().coordinateToTime(Math.max(x0,x1));
  if(t0&&t1&&t0!==t1){selStart=t0; selEnd=t1; redrawSel(); document.getElementById('selinfo').textContent='✅ 框选完成 → 点【匹配相似】';}});
TOP.c.subscribeClick(p=>{ if(selMode||!p.time)return;
  if(selStart===null||selEnd!==null){selStart=p.time;selEnd=null;}
  else{selEnd=p.time; if(selEnd<selStart){const t=selStart;selStart=selEnd;selEnd=t;}}
  redrawSel(); document.getElementById('selinfo').textContent=selEnd?'窗口已选 → 点匹配':'已选起点,再点终点';});
TOP.c.timeScale().subscribeVisibleLogicalRangeChange(redrawSel);
function clearSel(){selStart=selEnd=null; selBox.style.display='none'; document.getElementById('selinfo').textContent='拖拽框选 或 点两下 → 匹配';}

async function doMatch(){ if(selStart===null||selEnd===null){alert('先在上图点两下选窗口');return;}
  const k=document.getElementById('kk').value, mode=document.getElementById('mode').value;
  const u=`/api/match?symbol=${symT}&tf=${tfT}&start=${selStart*1e6}&end=${selEnd*1e6}&k=${k}&mode=${mode}`;
  const r=await (await fetch(u)).json(); const box=document.getElementById('analogs'); box.innerHTML='';
  if(r.error){box.innerHTML=`<span class=hint>${r.error}</span>`;return;}
  if(!r.analogs.length){box.innerHTML='<span class=hint>无匹配</span>';return;}
  r.analogs.forEach(a=>{const c=document.createElement('div'); c.className='chip';
    c.textContent=`#${a.rank} ${a.start.slice(0,10)} d=${a.dist}`;
    c.onclick=()=>{[...box.children].forEach(x=>x.className='chip'); c.className='chip on';
      curAnalog={start_ts:a.start_ts,end_ts:a.end_ts,win:a.win}; tfB=tfT;
      tfButtons('tfsB',tfB,t=>{tfB=t;loadBot();}); loadBot();
      document.getElementById('binfo').textContent=`analog ${a.start} | 距离${a.dist} | 黄箭头=该段,右侧=此后走势(可切级别/左拖加载)`;};
    box.appendChild(c);});
  document.getElementById('binfo').textContent=`共${r.analogs.length}个,点一个加载(按相似度排序)`;}

// 底图左拖动态加载更早历史
BOT.c.timeScale().subscribeVisibleLogicalRangeChange(async lr=>{ if(!lr||!botBars.length||lr.from> -2) return;
  const oldest=botBars[0].time; const more=await getBars(symT,tfB,(oldest-1)*1e6,300);
  if(more.length){const seen=new Set(botBars.map(b=>b.time)); botBars=[...more.filter(b=>!seen.has(b.time)),...botBars];
    BOT.s.setData(botBars); highlightAnalog();}});

document.getElementById('symT').onchange=e=>{symT=e.target.value; loadTop();};
tfButtons('tfsT',tfT,t=>{tfT=t;loadTop();});
tfButtons('tfsB',tfB,t=>{tfB=t;loadBot();});
loadTop();
new ResizeObserver(()=>{TOP.c.applyOptions({width:document.getElementById('top').clientWidth});
  BOT.c.applyOptions({width:document.getElementById('bottom').clientWidth});}).observe(document.body);
TOP.c.applyOptions({width:document.getElementById('top').clientWidth});
BOT.c.applyOptions({width:document.getElementById('bottom').clientWidth});
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                self._send(200, _PAGE.replace("__SYMS__", "".join(f"<option>{s}</option>" for s in _SYMS)), "text/html; charset=utf-8")
            elif u.path == "/api/bars":
                end = int(float(q["end"])) if q.get("end") else None
                bars = _candles(q.get("symbol", "BTCUSDT"), q.get("tf", "4h"), end, int(q.get("limit", 400)))
                self._send(200, json.dumps({"bars": _lw(bars)}))
            elif u.path == "/api/match":
                r = _match(q["symbol"], q["tf"], int(float(q["start"])), int(float(q["end"])),
                           int(q.get("k", 8)), q.get("mode", "level"))
                self._send(200, json.dumps(r))
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except Exception as e:  # noqa: BLE001
            self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))


def serve(port: int = 8800):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"analog viewer → http://127.0.0.1:{port}  (Ctrl-C to stop)", flush=True)
    httpd.serve_forever()


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="quant.analog_viewer")
    p.add_argument("--port", type=int, default=8800)
    a = p.parse_args(argv)
    serve(a.port)


if __name__ == "__main__":
    main()
