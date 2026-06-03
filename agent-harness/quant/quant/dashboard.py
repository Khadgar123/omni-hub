"""Localhost monitoring dashboard for the paper-trading loop.

A tiny stdlib ``http.server`` bound to 127.0.0.1 ONLY (never exposed). A background
thread refreshes a shared ``STATE`` every ``refresh`` seconds — it pulls live prices +
microstructure, runs the directionless baseline basket, paper-marks the virtual account,
and builds BOTH BTC execution scenarios (long & short, with entries + stops) — then the
page (auto-refresh) and ``/api/state`` (JSON) serve it. Read-only: shows decisions, never
places an order; the real balance is 0 and fills are simulated.

Run:  python -m quant.dashboard --port 8799 --refresh 90
then open  http://127.0.0.1:8799
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE: dict = {"ready": False, "error": None, "ts": 0}
_CACHE: dict = {}                # slow daily-basket prices cached ~5min (the bulk of the latency)
_LOCK = threading.Lock()
_BOOK_LOCK = threading.Lock()    # serialize trade-book read/advance/save vs /modify edits


def tf_analysis(symbol, tfs=("1m", "5m", "30m", "4h", "1d"), *, venue="binance", opener=None, timeout=10.0):
    """Per-timeframe TREND (regime label) + 3-tier S/R (nearest 3 supports below / 3
    resistances above current price, from scored_levels). Returns (dict_by_tf, bars_4h)."""
    from quant import levels, live, regime
    from quant.features import atr as _atr

    out, bars_4h = {}, None
    for tf in tfs:
        try:
            bars = live.fetch_candles(symbol, tf, venue=venue, opener=opener, timeout=timeout)
        except Exception:
            continue
        if tf == "4h":
            bars_4h = bars
        a = next((x for x in reversed(_atr(bars, 14)) if x), None) or 0.0
        ref = float(bars[-1]["close"])
        try:
            trend = regime.classify(bars).label
        except Exception:
            trend = "?"
        scored = levels.scored_levels(bars, atr=a) if len(bars) > 20 else []
        sup = sorted((L["price"] for L in scored if L["price"] < ref), reverse=True)[:3]
        res = sorted(L["price"] for L in scored if L["price"] > ref)[:3]
        out[tf] = {"trend": trend, "ref": round(ref, 2), "atr": round(a, 2),
                   "supports": [round(x, 2) for x in sup], "resistances": [round(x, 2) for x in res]}
    return out, bars_4h


def mtf_alignment(levels: dict) -> dict:
    """Summarize multi-timeframe trend agreement. Each TF votes +1 (up) / −1 (down) / 0
    (range); higher TFs weigh more. Returns ``{score, direction, agree, n, label}`` —
    |score|≈1 means all levels point the same way (a clear regime, not a trade signal)."""
    weights = {"1d": 2.0, "4h": 1.5, "30m": 1.0, "5m": 0.6, "1m": 0.4}
    votes, total_w, agree, n = 0.0, 0.0, 0, 0
    for tf, w in weights.items():
        t = (levels.get(tf) or {}).get("trend", "")
        if not t or t == "?":
            continue
        v = 1 if "up" in t else -1 if "down" in t else 0
        votes += v * w
        total_w += w
        n += 1
    if not n:
        return {"score": 0.0, "direction": "?", "agree": 0, "n": 0, "label": "无数据"}
    score = votes / total_w if total_w else 0.0
    direction = "多" if score > 0.15 else "空" if score < -0.15 else "中性"
    for tf in weights:
        t = (levels.get(tf) or {}).get("trend", "")
        if t and t != "?" and ((("up" in t) and score > 0) or (("down" in t) and score < 0)):
            agree += 1
    label = "全级别一致" if agree == n and abs(score) > 0.6 else "多数一致" if abs(score) > 0.4 else "分歧"
    return {"score": round(score, 2), "direction": direction, "agree": agree, "n": n, "label": label}


def compute_state(paper_path: str, equity0: float = 10000.0, cfg=None, book_path=None) -> dict:
    """Pull live data, advance the paper basket, assemble the full board. Each block is
    independently guarded so one venue hiccup can't blank the whole page."""
    from quant import baseline, exdata, execution, live, papertrade

    cfg = cfg or baseline.BaselineConfig()
    out: dict = {"ts": time.time(), "ready": True, "error": None}
    try:
        st = papertrade.load_state(paper_path, inception_equity=equity0)
        now = time.time()
        if _CACHE.get("prices") is None or now - _CACHE.get("prices_ts", 0) > 300:
            _CACHE["prices"] = baseline.load_live(venue="binance")   # 14 daily series, refetch ~5min
            _CACHE["prices_ts"] = now
        prices = _CACHE["prices"]
        dec = papertrade.tick_baseline(st, prices, cfg, ts=int(now))
        papertrade.save_state(st, paper_path)
        out["paper"] = st.to_dict()
        out["decision"] = dec.to_dict()
    except Exception as e:  # noqa: BLE001
        out["error"] = f"baseline/paper: {e}"
    out["symbols"] = {}
    for sym in ("BTCUSDC", "ETHUSDC"):                # BTC + ETH, each: levels + alignment + board + plans
        try:
            tf, bars4h = tf_analysis(sym)
            entry = {"levels": tf, "alignment": mtf_alignment(tf)}
            if bars4h:
                entry["plan_long"] = execution.build_order_plan(sym, "long", 0.55, bars4h, rr=5.0).to_dict()
                entry["plan_short"] = execution.build_order_plan(sym, "short", 0.55, bars4h, rr=5.0).to_dict()
            try:
                entry["board"] = exdata.dashboard(sym, timeout=10.0)
            except Exception as e:  # noqa: BLE001
                entry["board"] = {"error": str(e)}
            out["symbols"][sym] = entry
        except Exception as e:  # noqa: BLE001
            out["symbols"][sym] = {"error": str(e)}
    out["trades"] = []                               # discretionary trades (statefully advanced)
    try:
        peek = papertrade.load_book(book_path) if book_path else []
        bc = {}                                      # fetch bars OUTSIDE the lock (no network under lock)
        for tr in peek:
            key = (tr["symbol"], tr["tf"])
            if key not in bc:
                bc[key] = live.fetch_candles(tr["symbol"], tr["tf"], venue="binance", timeout=10.0)
        with _BOOK_LOCK:                             # advance + persist; serialized vs /modify
            book = papertrade.load_book(book_path) if book_path else []
            for tr in book:
                bars = bc.get((tr["symbol"], tr["tf"]))
                if not bars:
                    continue
                st = papertrade.advance_trade(tr, bars)
                bd = [{"price": e["price"], "size_frac": e["size_frac"], "label": e["label"],
                       "filled": i in st.get("filled", [])} for i, e in enumerate(tr["plan"]["entries"])]
                out["trades"].append({"trade": tr, "state": st, "breakdown": bd, "mark": st.get("mark")})
            if book_path and book:
                papertrade._save_book(book_path, book)
    except Exception as e:  # noqa: BLE001
        out["trades_error"] = str(e)
    return out


def _refresher(paper_path, equity0, refresh, book_path):
    while True:
        try:
            s = compute_state(paper_path, equity0, book_path=book_path)
        except Exception as e:  # noqa: BLE001
            s = {"ready": False, "error": str(e), "ts": time.time()}
        with _LOCK:
            STATE.clear()
            STATE.update(s)
        time.sleep(refresh)


def _row(cells, tag="td"):
    return "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"


def render_panels(state: dict) -> str:
    """The text panels (cards) — injected into the shell by JS polling, so a refresh
    never reloads the page / resets the chart zoom."""
    if not state.get("ready"):
        return f"<p class=load>启动中…首轮拉取实时数据中（{state.get('error') or ''}）</p>"
    parts = []

    # MTF trend-alignment summary (top)
    al = []
    for sym, data in (state.get("symbols") or {}).items():
        a = (data or {}).get("alignment")
        if a and a.get("n"):
            cls = "pos" if a["direction"] == "多" else "neg" if a["direction"] == "空" else ""
            al.append(f"<b>{sym.replace('USDC','')}</b>: <span class={cls}>{a['label']}·偏{a['direction']}</span>"
                      f" ({a['agree']}/{a['n']}级别一致, 分{a['score']:+.2f})")
    if al:
        parts.append("<div class=card><h2>⚡ MTF 趋势对齐（顶部汇总）</h2><p>" + " &nbsp;|&nbsp; ".join(al)
                     + "</p><p class=note>|分|越接近 1 = 各级别越一致(regime 清晰)。这是'方向有多明确'的背景,"
                     "不是买卖信号——方向仍是你的宏观判断,且裸空 crypto 难。</p></div>")

    # paper account
    pa = state.get("paper")
    if pa:
        sign = "pos" if pa["pnl_pct"] >= 0 else "neg"
        parts.append(f"<div class=card><h2>📒 Paper 账户（虚拟,余额0,模拟成交）</h2>"
                     f"<p class=big>净值 ${pa['equity']:,.0f} "
                     f"<span class={sign}>({pa['pnl_pct']:+.2f}%)</span></p>"
                     f"<p>已实现 ${pa['realized_pnl']:,.1f} · 浮动 ${pa['unrealized']:,.1f} · "
                     f"总敞口 ${pa['gross_exposure']:,.0f}</p>")
        if pa["positions"]:
            rows = "".join(_row([s, f"{p['qty']:+.4f}", f"{p['avg']:,.2f}",
                                 f"{pa['marks'].get(s, p['avg']):,.2f}",
                                 f"{p['qty']*(pa['marks'].get(s,p['avg'])-p['avg']):+,.1f}"])
                            for s, p in pa["positions"].items())
            parts.append("<table><tr><th>币</th><th>数量</th><th>均价</th><th>现价</th><th>浮盈</th></tr>"
                         + rows + "</table>")
        parts.append("</div>")

    # baseline basket
    dec = state.get("decision")
    if dec and dec.get("longs"):
        L = "".join(_row([s, f"{100*v:+.1f}%"]) for s, v in dec["longs"])
        S = "".join(_row([s, f"{100*v:+.1f}%"]) for s, v in dec["shorts"])
        parts.append(f"<div class=card><h2>🧭 无方向 baseline 篮子（自动·β≈0）</h2>"
                     f"<p>仓位倍数 ×{dec['gross_scale']:.2f} · regime {dec['regime']}</p>"
                     f"<div class=cols><div><b class=pos>做多(最强)</b><table>{L}</table></div>"
                     f"<div><b class=neg>做空(最弱)</b><table>{S}</table></div></div>"
                     f"<p class=note>赌'强者继续强于弱者',不赌大盘涨跌。这就是 paper 在跑的策略。</p></div>")

    # discretionary trades — stateful: 持仓/委托 split, auto-breakeven stop, quick TP/SL edit
    _st = {"active": "🟢持仓中", "stopped": "⛔已止损", "target": "✅已止盈",
           "disaster": "❌硬止损", "closed": "⬛已平"}
    for t in (state.get("trades") or []):
        tr, st, bd = t["trade"], t.get("state", {}), t.get("breakdown", [])
        plan = tr["plan"]
        tid = tr.get("id", "")
        side = "空" if plan["direction"] == "short" else "多"
        status = st.get("status", "active")
        r = st.get("total_r", 0) or 0.0
        held = [e for e in bd if e["filled"]]
        pend = [e for e in bd if not e["filled"]]
        hrows = "".join(_row([f"{e['size_frac']*100:.0f}%", f"{e['price']:,.2f}", e["label"]]) for e in held) \
            or "<tr><td colspan=3>—</td></tr>"
        prows = "".join(_row([f"{e['size_frac']*100:.0f}%", f"{e['price']:,.2f}", e["label"]]) for e in pend) \
            or "<tr><td colspan=3>无</td></tr>"
        avg = st.get("avg")
        avg_s = f"{avg:,.2f}" if avg else "—"
        astop = st.get("active_stop", plan["stop"])
        be = " 🔒已保本" if st.get("be_done") else ""
        mark_s = f"{t['mark']:,.2f}" if t.get("mark") else "—"
        btns = ("" if status != "active" else
                f"<div class=note>快速改: <button onclick=\"mod('{tid}','breakeven')\">止损→保本</button>"
                f" <input id='s_{tid}' size=8 placeholder='新止损价'>"
                f"<button onclick=\"modstop('{tid}')\">改止损</button>"
                f" <button onclick=\"mod('{tid}','close')\">立即平仓</button></div>")
        parts.append(
            f"<div class=card><h2>📝 {tr['symbol']} 做{side} [{tr.get('note','')}] · "
            f"<span class={'pos' if r > 0 else 'neg'}>{_st.get(status, status)} {r:+.2f}R</span> · 现价 {mark_s}</h2>"
            f"<div class=cols><div><b class=pos>持仓(已成交)</b> 均价 {avg_s}"
            f"<table><tr><th>仓</th><th>成交价</th><th></th></tr>{hrows}</table></div>"
            f"<div><b>委托(挂单待成交)</b><table><tr><th>仓</th><th>挂单价</th><th></th></tr>{prows}</table></div></div>"
            f"<p>⛔ 当前止损 <b>{astop:,.2f}</b>{be} · 硬止损 {plan.get('disaster_stop',0):,.2f}  "
            f"🎯 目标 {plan['final_target']:,.2f}</p>{btns}"
            f"<p class=note>止损=保护:到 +{tr.get('breakeven_at_r',1)}R 自动移到保本(防赚钱变亏),硬止损防亏太多;"
            f"止盈跟大级别,随时手动改。低级别形态低edge,paper检验。图上黄=持仓 灰虚=委托 红=止损 绿=目标。</p></div>")

    # per-symbol: alignment badge + multi-level trend + 3-tier S/R + auxiliary board + both scenarios
    for sym, data in (state.get("symbols") or {}).items():
        if data.get("error"):
            parts.append(f"<div class=card><h2>{sym}</h2><p class=neg>{data['error']}</p></div>")
            continue
        lv = data.get("levels", {})
        ref = ((lv.get("4h") or lv.get("1d") or {}).get("ref"))
        a = data.get("alignment") or {}
        badge = ""
        if a.get("n"):
            bcls = "pos" if a["direction"] == "多" else "neg" if a["direction"] == "空" else ""
            badge = f" · <span class={bcls}>{a['label']}偏{a['direction']} {a['agree']}/{a['n']}</span>"
        head = sym + (f" · 现价 {ref:,.0f}" if ref else "")
        block = [f"<div class=card><h2>📊 {head}{badge}</h2>", _levels_table(lv),
                 _board_html(data.get("board"))]
        pl, ps = data.get("plan_long"), data.get("plan_short")
        if pl and ps:
            block.append("<div class=cols>" + _plan_col("若做多", pl, "pos")
                         + _plan_col("若做空", ps, "neg") + "</div>"
                         "<p class=note>系统不替你判断多空(0.59墙);给你两个方向的精确执行位。"
                         "止损=收盘确认+硬顶,不插针。埋单已收紧(最近档≈现价附近)。</p>")
        block.append("</div>")
        parts.append("".join(block))
    if state.get("error"):
        parts.append(f"<div class=card><p class=neg>部分数据失败: {state['error']}</p></div>")
    return "".join(parts)


def _board_html(b):
    """Auxiliary microstructure block (walls/funding/OI/sentiment) for one symbol."""
    if not b or b.get("error"):
        return ""
    w = b.get("order_walls") or {}
    bw = " · ".join(f"{x['price']:,.0f}({x['qty']:.0f})" for x in (w.get("bid_walls") or [])[:3])
    aw = " · ".join(f"{x['price']:,.0f}({x['qty']:.0f})" for x in (w.get("ask_walls") or [])[:3])
    f = b.get("funding") or {}
    oi = b.get("open_interest") or {}
    oid = f" Δ{100*oi['delta_pct']:+.1f}%" if oi.get("delta_pct") is not None else ""
    flush = " ⚠强平洗盘" if oi.get("flush") else ""
    oinow = f"{oi['now']:,.0f}" if oi.get("now") is not None else "—"
    notes = "".join(f"<li>{n}</li>" for n in b.get("notes", []))
    return (f"<p class=note>📡 买墙 {bw or '—'} | 卖墙 {aw or '—'}<br>"
            f"资金费 年化{100*f.get('annualized',0):+.0f}% · OI {oinow}{oid}{flush} · "
            f"多空比 {b.get('long_short_ratio')} · 主动买卖 {b.get('taker_buy_sell')}</p>"
            f"<ul class=note>{notes}</ul>")


def _levels_table(lv):
    rows = ""
    for tf in ("1d", "4h", "30m", "5m", "1m"):
        d = lv.get(tf)
        if not d:
            continue
        t = d["trend"]
        tcls = "pos" if "up" in t else "neg" if "down" in t else ""
        sup = " · ".join(f"{x:,.0f}" for x in d["supports"]) or "—"
        res = " · ".join(f"{x:,.0f}" for x in d["resistances"]) or "—"
        rows += _row([tf, f"<span class={tcls}>{t}</span>", f"<span class=pos>{sup}</span>",
                      f"<span class=neg>{res}</span>"])
    return ("<table><tr><th>级别</th><th>趋势</th><th>支撑(近→远)</th><th>压力(近→远)</th></tr>"
            + rows + "</table>")


def _plan_col(title, p, cls):
    entries = "".join(_row([f"{e['size_frac']*100:.0f}%", f"{e['price']:,.0f}",
                            f"{(e['price']/p['ref_price']-1)*100:+.1f}%"]) for e in p["entries"])
    tgts = "".join(_row([f"{t['size_frac']*100:.0f}%", f"{t['price']:,.0f}", t["label"]]) for t in p["targets"])
    return (f"<div><b class={cls}>{title}</b> [{p['quality']}/{p['manage_style']}]"
            f"<p>现价 {p['ref_price']:,.0f} · ATR {p['atr']:,.0f}</p>"
            f"<table><tr><th>仓</th><th>埋单</th><th>距</th></tr>{entries}</table>"
            f"<p class={cls}>⛔ 止损 {p['stop']:,.0f} ({p['risk_dist']/p['atr']:.1f}ATR) · 硬顶 {p['disaster_stop']:,.0f}</p>"
            f"<table><tr><th>仓</th><th>止盈</th><th></th></tr>{tgts}</table></div>")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse

        u = urlparse(self.path)
        ctype = "application/json"
        if u.path == "/api/bars":
            q = parse_qs(u.query)
            sym = q.get("symbol", ["BTCUSDC"])[0]
            tf = q.get("tf", ["5m"])[0]
            try:
                from quant import live as _live
                bars = _live.fetch_candles(sym, tf, venue="binance", timeout=10.0)
                seen, out = set(), []
                for b in bars:
                    tsec = int(b["bucket_ts"] // 1_000_000)
                    if tsec in seen:
                        continue
                    seen.add(tsec)
                    out.append({"time": tsec, "open": float(b["open"]), "high": float(b["high"]),
                                "low": float(b["low"]), "close": float(b["close"])})
                body = json.dumps({"bars": out}).encode()
            except Exception as e:  # noqa: BLE001
                body = json.dumps({"bars": [], "error": str(e)}).encode()
        elif u.path == "/modify":
            q = parse_qs(u.query)
            tid, action, val = q.get("id", [""])[0], q.get("action", [""])[0], q.get("value", [None])[0]
            ok, msg = False, ""
            try:
                from quant import papertrade as _pt
                bp = getattr(self.server, "book_path", None)
                with _BOOK_LOCK:
                    if action == "close":
                        _pt.modify_trade(bp, tid, close=True)
                    elif action == "stop" and val:
                        _pt.modify_trade(bp, tid, stop=float(val))
                    elif action == "breakeven":
                        tr = next((x for x in _pt.load_book(bp) if x.get("id") == tid), None)
                        avg = (tr or {}).get("state", {}).get("avg")
                        if avg:
                            _pt.modify_trade(bp, tid, stop=avg)
                ok = True
            except Exception as e:  # noqa: BLE001
                msg = str(e)
            body = json.dumps({"ok": ok, "msg": msg}).encode()
        elif u.path == "/panels":
            with _LOCK:
                snap = dict(STATE)
            body = render_panels(snap).encode()
            ctype = "text/html; charset=utf-8"
        elif u.path.startswith("/api/state"):
            with _LOCK:
                snap = dict(STATE)
            body = json.dumps(snap, ensure_ascii=False, default=str).encode()
        else:
            body = render_shell(self.server.refresh).encode()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_CDN = "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"

# A persistent SPA shell: chart + an empty #panels div. JS polls /panels (HTML) + /api/state
# (levels) + /api/bars (candles) every REFRESH s, so nothing reloads and the chart zoom/range
# survives both the auto-refresh and a timeframe switch (the visible time window is restored).
_SHELL = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<title>quant paper 监控</title>
<script src="__CDN__"></script>
<style>
body{background:#0d1117;color:#c9d1d9;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:16px}
h1{font-size:18px;margin:0 0 8px} h2{font-size:15px;margin:0 0 8px;color:#58a6ff}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin:10px 0}
.big{font-size:24px;font-weight:600;margin:4px 0}
table{border-collapse:collapse;width:100%;margin:6px 0} th,td{text-align:right;padding:3px 8px;border-bottom:1px solid #21262d}
th:first-child,td:first-child{text-align:left}
.cols{display:flex;gap:18px;flex-wrap:wrap} .cols>div{flex:1;min-width:240px}
.pos{color:#3fb950} .neg{color:#f85149} .note{color:#8b949e;font-size:12.5px}
.load{color:#8b949e;padding:40px;text-align:center} ul{margin:6px 0;padding-left:18px}
#ctrls button{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:5px;padding:3px 11px;margin:2px;cursor:pointer}
#ctrls button.on{background:#1f6feb;border-color:#1f6feb;color:#fff} #chart{height:440px}
</style></head><body>
<h1>📈 quant paper 监控 <span id=meta class=note></span></h1>
<div class=card>
 <div id=ctrls>标的: <button data-sym=BTCUSDC class=on>BTC</button> <button data-sym=ETHUSDC>ETH</button>
  &nbsp;&nbsp;级别: <button data-tf=1m>1m</button> <button data-tf=5m class=on>5m</button>
  <button data-tf=30m>30m</button> <button data-tf=4h>4h</button> <button data-tf=1d>1d</button></div>
 <div id=chart></div><div id=legend class=note></div>
</div>
<div id=panels><p class=load>加载中…</p></div>
<p class=note>机械指标/流数据状态分析,非投资建议、非涨跌预测。低级别形态低edge。方向与审批由你定。</p>
<script>
const chart=LightweightCharts.createChart(document.getElementById('chart'),{height:440,
 layout:{background:{color:'#161b22'},textColor:'#c9d1d9'},grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},
 timeScale:{timeVisible:true,secondsVisible:false,borderColor:'#30363d'},rightPriceScale:{borderColor:'#30363d'},crosshair:{mode:0}});
const candle=chart.addCandlestickSeries({upColor:'#26a69a',downColor:'#ef5350',borderVisible:false,wickUpColor:'#26a69a',wickDownColor:'#ef5350'});
let sym='BTCUSDC',tf='5m',lines=[],lastState={},inited=false;
function clearLines(){lines.forEach(l=>candle.removePriceLine(l));lines=[];}
function drawLevels(){clearLines();const leg=[];
 (lastState.trades||[]).filter(t=>t.trade.symbol===sym).forEach(t=>{const p=t.trade.plan;
  (t.breakdown||[]).forEach(e=>lines.push(candle.createPriceLine({price:e.price,color:e.filled?'#d29922':'#8b949e',lineWidth:1,lineStyle:e.filled?0:2,axisLabelVisible:true,title:(e.filled?'持仓':'委托')+Math.round(e.size_frac*100)+'%'})));
  lines.push(candle.createPriceLine({price:p.stop,color:'#f85149',lineWidth:2,axisLabelVisible:true,title:'止损'}));
  if(p.disaster_stop)lines.push(candle.createPriceLine({price:p.disaster_stop,color:'#a01a13',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'硬止损'}));
  (p.targets||[]).forEach((g,i)=>lines.push(candle.createPriceLine({price:g.price,color:'#3fb950',lineWidth:1,axisLabelVisible:true,title:'目标'+(i+1)})));
  leg.push((p.direction==='short'?'空':'多')+' 止损'+p.stop.toLocaleString()+' 目标'+p.final_target.toLocaleString());});
 document.getElementById('legend').textContent=leg.join('   |   ')||(sym.replace('USDC','')+' 当前无自选持仓单');}
function setActive(a,v){document.querySelectorAll('[data-'+a+']').forEach(b=>b.classList.toggle('on',b.dataset[a]===v));}
function loadBars(keep){const r=(keep&&inited)?chart.timeScale().getVisibleRange():null;
 return fetch('/api/bars?symbol='+sym+'&tf='+tf).then(x=>x.json()).then(d=>{candle.setData(d.bars||[]);
  if(r){try{chart.timeScale().setVisibleRange(r);}catch(e){}}else{chart.timeScale().fitContent();}inited=true;drawLevels();});}
document.querySelectorAll('[data-sym]').forEach(b=>b.onclick=()=>{sym=b.dataset.sym;setActive('sym',sym);inited=false;loadBars(false);});
document.querySelectorAll('[data-tf]').forEach(b=>b.onclick=()=>{const r=inited?chart.timeScale().getVisibleRange():null;tf=b.dataset.tf;setActive('tf',tf);
 fetch('/api/bars?symbol='+sym+'&tf='+tf).then(x=>x.json()).then(d=>{candle.setData(d.bars||[]);if(r){try{chart.timeScale().setVisibleRange(r);}catch(e){}}drawLevels();});});
function tick(keep){fetch('/api/state').then(x=>x.json()).then(s=>{lastState=s;drawLevels();
  if(s.ts)document.getElementById('meta').textContent='· '+new Date(s.ts*1000).toLocaleTimeString()+' · 自动__REFRESH__s · 余额0/模拟/不下实盘';});
 loadBars(keep);fetch('/panels').then(x=>x.text()).then(h=>{document.getElementById('panels').innerHTML=h;});}
function mod(id,a){fetch('/modify?id='+encodeURIComponent(id)+'&action='+a).then(()=>setTimeout(()=>tick(true),200));}
function modstop(id){var v=document.getElementById('s_'+id).value;if(v)fetch('/modify?id='+encodeURIComponent(id)+'&action=stop&value='+encodeURIComponent(v)).then(()=>setTimeout(()=>tick(true),200));}
tick(false);setInterval(()=>tick(true),__REFRESH__*1000);
</script></body></html>"""


def render_shell(refresh: int = 10) -> str:
    return _SHELL.replace("__CDN__", _CDN).replace("__REFRESH__", str(refresh))


def serve(port=8799, refresh=10, paper_path="~/quant/paper_state.json", equity0=10000.0,
          book_path="~/quant/paper_trades.json"):
    t = threading.Thread(target=_refresher, args=(paper_path, equity0, refresh, book_path), daemon=True)
    t.start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    httpd.refresh = refresh
    httpd.book_path = book_path
    print(f"dashboard → http://127.0.0.1:{port}  (refresh {refresh}s, Ctrl-C to stop)", flush=True)
    httpd.serve_forever()


def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(prog="quant.dashboard", description=__doc__)
    p.add_argument("--port", type=int, default=8799)
    p.add_argument("--refresh", type=int, default=10)
    p.add_argument("--paper", default="~/quant/paper_state.json")
    p.add_argument("--book", default="~/quant/paper_trades.json", help="discretionary trade book")
    p.add_argument("--equity", type=float, default=10000.0)
    args = p.parse_args(argv)
    serve(port=args.port, refresh=args.refresh, paper_path=args.paper, equity0=args.equity,
          book_path=args.book)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
