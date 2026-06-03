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

    out, bars_4h, by_tf, ref = {}, None, {}, 0.0
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
        by_tf[tf] = scored
        sup = sorted((L["price"] for L in scored if L["price"] < ref), reverse=True)[:3]
        res = sorted(L["price"] for L in scored if L["price"] > ref)[:3]
        out[tf] = {"trend": trend, "ref": round(ref, 2), "atr": round(a, 2),
                   "supports": [round(x, 2) for x in sup], "resistances": [round(x, 2) for x in res]}
    # cross-TF confluence: merge levels that OVERLAP across timeframes -> one line, scored by
    # Σ tf_weight·strength. n_tf≥2 = a key level (multiple levels agree); keep the few strongest
    # near price so the chart isn't dense.
    conf = []
    if by_tf and ref:
        tfw = {"1m": 0.4, "5m": 0.6, "30m": 1.0, "4h": 1.5, "1d": 2.0}
        zones = levels.confluence(by_tf, tf_weight=tfw, merge_pct=0.0035)
        near = [z for z in zones if abs(z["price"] / ref - 1) <= 0.045 and abs(z["price"] - ref) > 1e-6]
        for z in sorted(near, key=lambda x: -x["confluence_score"])[:6]:
            conf.append({"price": round(z["price"], 2), "n_tf": z["n_tf"], "tfs": z["tfs"],
                         "score": round(z["confluence_score"], 2),
                         "side": "res" if z["price"] > ref else "sup", "key": z["n_tf"] >= 2})
    return out, bars_4h, conf


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


def compute_state(paper_path: str, equity0: float = 10000.0, cfg=None, book_path=None, intents_path=None) -> dict:
    """Pull live data, advance the paper basket, assemble the full board. Each block is
    independently guarded so one venue hiccup can't blank the whole page."""
    from quant import baseline, exdata, execution, live, papertrade

    cfg = cfg or baseline.BaselineConfig()
    out: dict = {"ts": time.time(), "ready": True, "error": None}
    out["account"] = None                                # real account (directional) — only if a key is set
    try:
        import os as _os
        if _os.environ.get("BINANCE_KEY"):
            from quant import broker as _bk
            net = _os.environ.get("BROKER_NET", "testnet")
            bal = _bk.read_balance(market="futures", net=net)
            out["account"] = {"equity": bal.get("equity"), "available": bal.get("available"),
                              "asset": bal.get("asset"), "net": net}
    except Exception as e:  # noqa: BLE001
        out["account"] = {"error": str(e)}
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
            tf, bars4h, conf = tf_analysis(sym)
            entry = {"levels": tf, "alignment": mtf_alignment(tf), "key_levels": conf}
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
    out["intents"] = []                              # pending order intents awaiting approval
    try:
        now = time.time()
        for it in (papertrade.load_intents(intents_path) if intents_path else []):
            if it.get("status") != "pending":
                continue
            out["intents"].append({"intent": it,
                                   "remaining_sec": int(it.get("ttl_sec", 600) - (now - it.get("created_ts", now)))})
    except Exception as e:  # noqa: BLE001
        out["intents_error"] = str(e)
    return out


def _refresher(paper_path, equity0, refresh, book_path, intents_path):
    while True:
        try:
            s = compute_state(paper_path, equity0, book_path=book_path, intents_path=intents_path)
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
    acc = state.get("account")
    if acc and acc.get("equity") is not None:
        parts.append(f"<div class=card style='border:1px solid #d29922'><h2>💰 真实账户({acc.get('net')}) · 方向交易用</h2>"
                     f"<p class=big>余额 ${acc['equity']:,.2f} {acc.get('asset','')} · 可用 ${acc.get('available',0):,.2f}</p>"
                     f"<p class=note>实时拉取(只读key)。baseline 用 paper;方向单按这个余额定仓位($金额)。</p></div>")
    elif acc and acc.get("error"):
        parts.append(f"<div class=card><p class=note>真实账户未接(设 BINANCE_KEY/BINANCE_SECRET + "
                     f"BROKER_NET=testnet 即显示真实余额): {str(acc['error'])[:90]}</p></div>")
    else:
        parts.append("<div class=card style='border:1px dashed #6e7681'><h2>💰 真实账户 · 未连接</h2>"
                     "<p class=note>当前用 paper $10,000 模拟。连真实余额(只读即可):<br>"
                     "<code>export BINANCE_KEY=… BINANCE_SECRET=… BROKER_NET=testnet</code> 后重启本面板,"
                     "余额就显示在这里,方向单按真实余额算 $。</p></div>")
    parts.append(_positions_html(state))                 # OPEN positions first (right under the chart)

    # 待批准 pending intents (top, right under the chart) — drawn on the chart too
    for t in (state.get("intents") or []):
        it = t["intent"]
        plan = it["plan"]
        rem = t.get("remaining_sec", 0)
        side = "空" if plan["direction"] == "short" else "多"
        ent = " · ".join(f"{'⚡基础' if e.get('follow') else '埋伏'}{e['size_frac']*100:.0f}%@{e['price']:,.0f}"
                         for e in plan["entries"])
        tgt = " · ".join(f"{g['price']:,.0f}" for g in plan["targets"])
        exp = f"{rem}s 后失效" if rem > 0 else "已过期(不可批)"
        btns = (f"<button onclick=\"approve('{it['id']}')\">✅ 批准(paper跟踪)</button> "
                f"<button onclick=\"execCmd('{it['id']}')\" style='background:#7a1f1f'>🔴 实盘下单命令</button> "
                f"<button onclick=\"editIntent('{it['id']}')\">✎ 改</button> "
                f"<button onclick=\"reject('{it['id']}')\">✕ 拒绝</button>") if rem > 0 else \
               f"<button onclick=\"reject('{it['id']}')\">清除</button>"
        tlist = plan.get("targets", [])
        t1v = f"{tlist[0]['price']:.0f}" if tlist else ""
        t2v = f"{tlist[-1]['price']:.0f}" if len(tlist) > 1 else ""
        edit = (f"<div class=note style='margin:4px 0'>改数值→批: 止损<input id=e_stop_{it['id']} size=7 "
                f"value='{plan['stop']:.0f}'> T1<input id=e_t1_{it['id']} size=7 value='{t1v}'> "
                f"T2<input id=e_t2_{it['id']} size=7 value='{t2v}'> 仓位×<input id=e_size_{it['id']} size=4 "
                f"value='{plan.get('size_cap_frac',0.15)}'> <button onclick=\"updateIntent('{it['id']}')\">更新</button></div>")
        parts.append(
            f"<div class=card style='border:1px solid #a371f7'><h2>⏳ 待批准 · {it['symbol']} 做{side} "
            f"[{it.get('note','')}]</h2>"
            f"<p>入场(图上紫虚线): {ent} &nbsp; 成交: "
            f"{'maker跟随(没成交自动跟价,免taker)' if plan.get('follow') else '限价埋伏'}"
            f"<br>⛔ 止损 {plan['stop']:,.0f} · 硬 {plan.get('disaster_stop',0):,.0f} "
            f"&nbsp; 🎯 目标 {tgt} &nbsp; 仓位 {plan.get('size_cap_frac','?')}× &nbsp; R:R {plan.get('rr','?')}</p>"
            f"{edit}<p class=note>{exp} · ✅批准=paper跟踪 · 🔴实盘=生成命令,你在终端按回车才下单(系统永不自动下实盘)</p>"
            f"{btns}<div id=execbox_{it['id']} style='display:none;margin-top:8px'></div></div>")

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

    # (open positions are rendered first by _positions_html, above)

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
        kl = data.get("key_levels", [])
        kl_html = ""
        if kl:
            items = " · ".join(
                f"<b class={'pos' if z['side'] == 'sup' else 'neg'}>{z['price']:,.0f}</b>"
                f"({'撑' if z['side'] == 'sup' else '压'}×{z['n_tf']})" for z in kl)
            kl_html = (f"<p class=note>🔑 关键位(多级别共振): {items} "
                       f"<span>（×N=N个级别在此重叠,N≥2=关键;只画最强几条,不密集）</span></p>")
        block = [f"<div class=card><h2>📊 {head}{badge}</h2>", _levels_table(lv), kl_html,
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


def _positions_html(state):
    """Binance-style OPEN positions panel (placed right under the chart). Side/size/avg/mark/uPnL,
    the SHARED stop+targets (加仓 = one combined position), inline DYNAMIC TP/SL edit + close. Closed
    positions drop off (their chart lines are already gone)."""
    rows = []
    equity = (state.get("account") or {}).get("equity") or (state.get("paper") or {}).get("inception_equity", 10000.0)
    for t in (state.get("trades") or []):
        tr, st = t["trade"], t.get("state", {})
        if st.get("status", "active") != "active":
            continue
        plan, tid, bd = tr["plan"], tr.get("id", ""), t.get("breakdown", [])
        side = "空" if plan["direction"] == "short" else "多"
        col = "#f85149" if side == "空" else "#3fb950"
        avg = st.get("avg") or 0
        mark = st.get("mark") or t.get("mark") or avg
        r = st.get("total_r", 0) or 0.0
        pos = st.get("position", 0) or 0
        held = [e for e in bd if e["filled"]]
        pend = [e for e in bd if not e["filled"]]
        tlist = plan.get("targets", [])
        t1v = f"{tlist[0]['price']:.0f}" if tlist else ""
        t2v = f"{tlist[-1]['price']:.0f}" if len(tlist) > 1 else ""
        heldn = " · ".join(f"{e['size_frac']*100:.0f}%@{e['price']:,.0f}" for e in held) or "—"
        pendn = " · ".join(f"{e['size_frac']*100:.0f}%@{e['price']:,.0f}" for e in pend) or "无"
        tgts = " · ".join(f"{g['price']:,.0f}" for g in tlist) or "—"
        astop = plan["stop"]                             # effective stop (breakeven + manual edits write here)
        notional = (plan.get("size_cap_frac", 0) or 0) * equity      # full intended $ at this size
        filled_usd = notional * pos                                  # $ actually filled so far
        qty = filled_usd / avg if avg else 0
        rows.append(
            f"<div class=card style='border-left:4px solid {col}'>"
            f"<h2>📈 {tr['symbol']} <b style='color:{col}'>{side} {pos*100:.0f}%仓</b> · "
            f"<span id=pnl_{tid} class={'pos' if r >= 0 else 'neg'}>浮盈 {r:+.2f}R</span></h2>"
            f"<p>开仓均价 <b>{avg:,.2f}</b> · 标记价 {mark:,.2f} · 仓位 {plan.get('size_cap_frac','?')}×权益"
            f" ≈ <b>${notional:,.0f}</b>名义(已成交 ${filled_usd:,.0f}≈{qty:.4f}币)"
            f" · 当前止损 <b>{astop:,.2f}</b>{' 🔒保本' if st.get('be_done') else ''} · 目标 {tgts}</p>"
            f"<p class=note>持仓(已成交): {heldn} &nbsp;|&nbsp; 委托(挂单): {pendn}</p>"
            f"<div class=note>动态改止盈止损: 止损<input id=m_stop_{tid} size=8 value='{astop:.0f}'> "
            f"T1<input id=m_t1_{tid} size=8 value='{t1v}'> T2<input id=m_t2_{tid} size=8 value='{t2v}'> "
            f"<button onclick=\"modtp('{tid}')\">更新止盈止损</button> "
            f"<button onclick=\"mod('{tid}','breakeven')\">止损→保本</button> "
            f"<button onclick=\"mod('{tid}','close')\">立即平仓</button></div></div>")
    return "".join(rows)


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


def _live_overlay(snap, book_path, intents_path):
    """Re-read the fast-changing intents/trade files so approve / reject / create / modify reflect
    IMMEDIATELY — the cached STATE only recomputes every ~refresh+compute seconds (~20s), which is why
    a click looked like 'no change'. The slow data (symbols / baseline / board) stays cached."""
    from quant import papertrade as _pt

    snap = dict(snap)
    now = time.time()
    if intents_path:
        try:
            snap["intents"] = [{"intent": it,
                                "remaining_sec": int(it.get("ttl_sec", 600) - (now - it.get("created_ts", now)))}
                               for it in _pt.load_intents(intents_path) if it.get("status") == "pending"]
        except Exception:  # noqa: BLE001
            pass
    if book_path:
        try:
            trades = []
            for bt in _pt.load_book(book_path):
                st = bt.get("state", {})
                bd = [{"price": e["price"], "size_frac": e["size_frac"], "label": e["label"],
                       "filled": i in st.get("filled", [])} for i, e in enumerate(bt["plan"]["entries"])]
                trades.append({"trade": bt, "state": st, "breakdown": bd, "mark": st.get("mark")})
            snap["trades"] = trades
        except Exception:  # noqa: BLE001
            pass
    return snap


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
        elif u.path == "/modtp":                              # edit an OPEN position's stop + targets (dynamic)
            q = parse_qs(u.query)
            tid = q.get("id", [""])[0]
            try:
                from quant import papertrade as _pt
                kw = {}
                if q.get("stop", [""])[0]:
                    kw["stop"] = float(q["stop"][0])
                tg = [float(x) for x in (q.get("t1", [""])[0], q.get("t2", [""])[0]) if x]
                if tg:
                    kw["targets"] = [{"price": round(p, 2), "size_frac": round(1 / len(tg), 3),
                                      "role": "final" if i == len(tg) - 1 else "scale_out", "label": f"T{i+1}"}
                                     for i, p in enumerate(tg)]
                with _BOOK_LOCK:
                    _pt.modify_trade(getattr(self.server, "book_path", None), tid, **kw)
                body = json.dumps({"ok": True}).encode()
            except Exception as e:  # noqa: BLE001
                body = json.dumps({"ok": False, "msg": str(e)}).encode()
        elif u.path == "/approve":
            q = parse_qs(u.query)
            iid = q.get("id", [""])[0]
            ok, msg = False, ""
            try:
                from quant import live as _live, papertrade as _pt
                ip = getattr(self.server, "intents_path", None)
                bp = getattr(self.server, "book_path", None)
                tgt = next((x for x in _pt.load_intents(ip) if x.get("id") == iid), None)
                if tgt:
                    bars = _live.fetch_candles(tgt["symbol"], tgt["tf"], venue="binance", timeout=10.0)
                    since = int(bars[-1]["bucket_ts"]) if bars else 0
                    with _BOOK_LOCK:
                        _pt.approve_intent(ip, bp, iid, since_ts=since)
                ok = True
            except Exception as e:  # noqa: BLE001
                msg = str(e)
            body = json.dumps({"ok": ok, "msg": msg}).encode()
        elif u.path == "/reject":
            q = parse_qs(u.query)
            try:
                from quant import papertrade as _pt
                with _BOOK_LOCK:
                    _pt.reject_intent(getattr(self.server, "intents_path", None), q.get("id", [""])[0])
                body = json.dumps({"ok": True}).encode()
            except Exception as e:  # noqa: BLE001
                body = json.dumps({"ok": False, "msg": str(e)}).encode()
        elif u.path == "/exec_cmd":
            # Generate the broker command for a pending intent — the dashboard NEVER fires; the human runs
            # it in their terminal (that keystroke is the gate). Writes the intent to a file broker reads.
            q = parse_qs(u.query)
            iid = q.get("id", [""])[0]
            net = q.get("net", ["testnet"])[0]
            ok, msg, prev, ex, path = False, "", "", "", ""
            try:
                import os
                from quant import papertrade as _pt
                ip = getattr(self.server, "intents_path", None)
                tgt = next((x for x in _pt.load_intents(ip) if x.get("id") == iid), None)
                if not tgt:
                    msg = "找不到该委托(可能已过期/已处理)"
                else:
                    outdir = os.path.dirname(ip) if ip else "/tmp"
                    path = os.path.join(outdir, f"broker_intent_{iid}.json")
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(tgt, f, ensure_ascii=False, indent=2)
                    eq = 10000.0
                    try:
                        if os.environ.get("BINANCE_KEY"):           # size preview by real balance if a key is set
                            from quant import broker as _bk
                            eq = _bk.read_balance(net=net).get("equity", eq) or eq
                    except Exception:  # noqa: BLE001
                        pass
                    prev = f"python -m quant.broker preview --intent {path} --equity {eq:.0f}"
                    ex = f"python -m quant.broker execute --intent {path} --net {net} --yes"
                    ok = True
            except Exception as e:  # noqa: BLE001
                msg = str(e)
            body = json.dumps({"ok": ok, "msg": msg, "preview": prev, "execute": ex, "path": path}).encode()
        elif u.path == "/create_intent":
            q = parse_qs(u.query)
            ok, msg = False, ""
            try:
                from quant import execution as _ex, papertrade as _pt
                sym = q.get("symbol", ["BTCUSDC"])[0]
                plan = _ex.manual_plan(sym, q.get("dir", ["short"])[0], q.get("entry", ["0"])[0],
                                       q.get("stop", ["0"])[0],
                                       [q.get("t1", [""])[0], q.get("t2", [""])[0]],
                                       size=q.get("size", ["0.15"])[0])
                with _BOOK_LOCK:
                    _pt.emit_pending(getattr(self.server, "intents_path", None), plan.to_dict(),
                                     symbol=sym, tf="manual", created_ts=int(time.time()),
                                     note="手填", ttl_sec=3600)
                ok = True
            except Exception as e:  # noqa: BLE001
                msg = str(e)
            body = json.dumps({"ok": ok, "msg": msg}).encode()
        elif u.path == "/auto_intent":
            q = parse_qs(u.query)
            ok, msg = False, ""
            try:
                from quant import execution as _ex, live as _live, papertrade as _pt
                sym = q.get("symbol", ["BTCUSDC"])[0]
                tf = q.get("tf", ["5m"])[0]
                dirn = q.get("dir", ["auto"])[0]
                bars = _live.fetch_candles(sym, tf, venue="binance", timeout=10.0)
                plan = _ex.auto_plan(sym, tf, bars, direction=(None if dirn == "auto" else dirn))
                if plan.direction == "flat":
                    msg = f"{tf} 级别 regime 中性,没自动给方向(请手选 多/空)"
                else:
                    with _BOOK_LOCK:
                        _pt.emit_pending(getattr(self.server, "intents_path", None), plan.to_dict(),
                                         symbol=sym, tf=tf, created_ts=int(time.time()),
                                         note=f"{tf}自动{plan.direction}", ttl_sec=3600)
                    ok = True
            except Exception as e:  # noqa: BLE001
                msg = str(e)
            body = json.dumps({"ok": ok, "msg": msg}).encode()
        elif u.path == "/edit_intent":
            q = parse_qs(u.query)
            iid = q.get("id", [""])[0]
            ok, msg = False, ""
            try:
                from quant import papertrade as _pt
                ip = getattr(self.server, "intents_path", None)
                with _BOOK_LOCK:
                    ints = _pt.load_book(ip)
                    tgt = next((x for x in ints if x.get("id") == iid), None)
                    if tgt:
                        plan = tgt["plan"]
                        sign = 1 if plan["direction"] == "long" else -1
                        wsum = max(sum(e["size_frac"] for e in plan["entries"]), 1e-9)
                        avg = sum(e["price"] * e["size_frac"] for e in plan["entries"]) / wsum
                        if q.get("stop", [""])[0]:
                            plan["stop"] = float(q["stop"][0])
                        risk = abs(avg - plan["stop"]) or plan.get("risk_dist", 1.0)
                        plan["risk_dist"] = round(risk, 2)
                        plan["disaster_stop"] = round(avg - sign * 1.8 * risk, 2)
                        tg = [float(x) for x in (q.get("t1", [""])[0], q.get("t2", [""])[0]) if x]
                        if tg:
                            plan["targets"] = [{"price": round(p, 2), "size_frac": round(1 / len(tg), 3),
                                                "role": "final" if i == len(tg) - 1 else "scale_out",
                                                "label": f"T{i+1}"} for i, p in enumerate(tg)]
                            plan["final_target"] = round(tg[-1], 2)
                            plan["rr"] = round(abs(tg[-1] - avg) / risk, 2)
                        if q.get("size", [""])[0]:
                            plan["size_cap_frac"] = float(q["size"][0])
                        _pt._save_book(ip, ints)
                        ok = True
            except Exception as e:  # noqa: BLE001
                msg = str(e)
            body = json.dumps({"ok": ok, "msg": msg}).encode()
        elif u.path == "/panels":
            with _LOCK:
                snap = dict(STATE)
            snap = _live_overlay(snap, getattr(self.server, "book_path", None),
                                 getattr(self.server, "intents_path", None))
            body = render_panels(snap).encode()
            ctype = "text/html; charset=utf-8"
        elif u.path.startswith("/api/state"):
            with _LOCK:
                snap = dict(STATE)
            snap = _live_overlay(snap, getattr(self.server, "book_path", None),
                                 getattr(self.server, "intents_path", None))
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
  <button data-tf=30m>30m</button> <button data-tf=4h>4h</button> <button data-tf=1d>1d</button>
  &nbsp;&nbsp;<label><input type=checkbox id=cb_sr onchange=drawLevels()> S/R线</label></div>
 <div id=chart></div><div id=legend class=note></div>
 <div id=orderform class=note style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;align-items:center">
  <b>按级别自动:</b><select id=of_tf><option>1m</option><option selected>5m</option><option>30m</option><option>4h</option><option>1d</option></select>
  <select id=of_autodir><option value=auto>自动方向</option><option value=short>空</option><option value=long>多</option></select>
  <button onclick=autoIntent()>自动设计挂单</button> &nbsp;|&nbsp;
  手填: 方向<select id=of_dir><option value=short>空</option><option value=long>多</option></select>
  入场<input id=of_entry size=8> 止损<input id=of_stop size=8> 目标1<input id=of_t1 size=8>
  目标2<input id=of_t2 size=8> 仓位×<input id=of_size size=4 value=0.15>
  <button onclick=createIntent()>创建待批挂单</button> <span id=of_msg></span></div>
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
 const sd=(lastState.symbols||{})[sym];
 if(sd&&sd.key_levels&&document.getElementById('cb_sr').checked){sd.key_levels.filter(z=>z.key).slice(0,4).forEach(z=>{
  lines.push(candle.createPriceLine({price:z.price,color:z.side==='sup'?'#2a9d8f':'#e9a23b',
   lineWidth:2,lineStyle:0,axisLabelVisible:true,title:(z.side==='sup'?'撑':'压')+'×'+z.n_tf}));});}
 (lastState.trades||[]).filter(t=>t.trade.symbol===sym&&(!t.state||t.state.status==='active')).forEach(t=>{const p=t.trade.plan;
  (t.breakdown||[]).forEach(e=>lines.push(candle.createPriceLine({price:e.price,color:e.filled?'#d29922':'#8b949e',lineWidth:1,lineStyle:e.filled?0:2,axisLabelVisible:true,title:(e.filled?'持仓':'委托')+Math.round(e.size_frac*100)+'%'})));
  lines.push(candle.createPriceLine({price:p.stop,color:'#f85149',lineWidth:2,axisLabelVisible:true,title:'止损'}));
  if(p.disaster_stop)lines.push(candle.createPriceLine({price:p.disaster_stop,color:'#a01a13',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'硬止损'}));
  (p.targets||[]).forEach((g,i)=>lines.push(candle.createPriceLine({price:g.price,color:'#3fb950',lineWidth:1,axisLabelVisible:true,title:'目标'+(i+1)})));
  leg.push((p.direction==='short'?'空':'多')+' 止损'+p.stop.toLocaleString()+' 目标'+p.final_target.toLocaleString());});
 (lastState.intents||[]).filter(t=>t.intent.symbol===sym).forEach(t=>{const p=t.intent.plan;
  (p.entries||[]).forEach(e=>lines.push(candle.createPriceLine({price:e.price,color:'#a371f7',lineWidth:1,lineStyle:1,axisLabelVisible:true,title:'待批入场'})));
  lines.push(candle.createPriceLine({price:p.stop,color:'#a371f7',lineWidth:1,lineStyle:1,axisLabelVisible:true,title:'待批止损'}));
  (p.targets||[]).forEach(g=>lines.push(candle.createPriceLine({price:g.price,color:'#a371f7',lineWidth:1,lineStyle:1,axisLabelVisible:true,title:'待批目标'})));
  leg.push('⏳待批'+(p.direction==='short'?'空':'多'));});
 document.getElementById('legend').textContent=leg.join('   |   ')||(sym.replace('USDC','')+' 当前无单');}
function setActive(a,v){document.querySelectorAll('[data-'+a+']').forEach(b=>b.classList.toggle('on',b.dataset[a]===v));}
function loadBars(keep){const r=(keep&&inited)?chart.timeScale().getVisibleRange():null;
 return fetch('/api/bars?symbol='+sym+'&tf='+tf).then(x=>x.json()).then(d=>{var b=d.bars||[];candle.setData(b);
  if(b.length)window._lastClose=b[b.length-1].close;
  if(r){try{chart.timeScale().setVisibleRange(r);}catch(e){}}else{chart.timeScale().fitContent();}inited=true;drawLevels();livePnl();});}
function updateLast(){return fetch('/api/bars?symbol='+sym+'&tf='+tf).then(x=>x.json()).then(d=>{var b=d.bars||[];if(b.length&&inited){candle.update(b[b.length-1]);window._lastClose=b[b.length-1].close;livePnl();}});}
document.querySelectorAll('[data-sym]').forEach(b=>b.onclick=()=>{sym=b.dataset.sym;setActive('sym',sym);inited=false;loadBars(false);});
document.querySelectorAll('[data-tf]').forEach(b=>b.onclick=()=>{const r=inited?chart.timeScale().getVisibleRange():null;tf=b.dataset.tf;setActive('tf',tf);
 fetch('/api/bars?symbol='+sym+'&tf='+tf).then(x=>x.json()).then(d=>{candle.setData(d.bars||[]);if(r){try{chart.timeScale().setVisibleRange(r);}catch(e){}}drawLevels();});});
function tick(keep){fetch('/api/state').then(x=>x.json()).then(s=>{lastState=s;drawLevels();
  if(s.ts)document.getElementById('meta').textContent='· '+new Date(s.ts*1000).toLocaleTimeString()+' · 自动__REFRESH__s · 余额0/模拟/不下实盘';});
 fetch('/panels').then(x=>x.text()).then(h=>{document.getElementById('panels').innerHTML=h;});}
function approve(id){fetch('/approve?id='+encodeURIComponent(id)).then(()=>setTimeout(()=>tick(true),300));}
function execCmd(id){fetch('/exec_cmd?id='+encodeURIComponent(id)).then(r=>r.json()).then(d=>{
  var box=g('execbox_'+id); if(!box)return;
  if(!d.ok){box.style.display='block';box.innerHTML='<span class=neg>✗ '+(d.msg||'失败')+'</span>';return;}
  box.style.display='block';
  box.innerHTML='<div class=note>① 先预览(安全·不下单·不需要key):</div>'+
    '<textarea readonly rows=2 style="width:99%;font-family:monospace" onclick="this.select()">'+d.preview+'</textarea>'+
    '<div class=note style="color:#f85149">② 确认无误→实盘下单(测试网·复制到你终端按回车=你的最终确认):</div>'+
    '<textarea readonly rows=2 style="width:99%;font-family:monospace" onclick="this.select()">'+d.execute+'</textarea>'+
    '<div class=note>需先 export BINANCE_KEY/BINANCE_SECRET(交易权限)。系统只生成命令,永不替你按回车。</div>';});}
function reject(id){fetch('/reject?id='+encodeURIComponent(id)).then(()=>setTimeout(()=>tick(true),300));}
function g(id){return document.getElementById(id);}
function createIntent(){var p=new URLSearchParams({symbol:sym,dir:g('of_dir').value,entry:g('of_entry').value,stop:g('of_stop').value,t1:g('of_t1').value,t2:g('of_t2').value,size:g('of_size').value});
 fetch('/create_intent?'+p.toString()).then(r=>r.json()).then(d=>{g('of_msg').textContent=d.ok?'✓已创建,见下方待批准':('✗'+(d.msg||''));setTimeout(()=>tick(true),300);});}
function autoIntent(){var p=new URLSearchParams({symbol:sym,tf:g('of_tf').value,dir:g('of_autodir').value});
 fetch('/auto_intent?'+p.toString()).then(r=>r.json()).then(d=>{g('of_msg').textContent=d.ok?('✓ '+g('of_tf').value+'级别已自动设计,见下方待批准'):('✗'+(d.msg||''));setTimeout(()=>tick(true),300);});}
function updateIntent(id){var p=new URLSearchParams({id:id,stop:g('e_stop_'+id).value,t1:g('e_t1_'+id).value,t2:g('e_t2_'+id).value,size:g('e_size_'+id).value});
 fetch('/edit_intent?'+p.toString()).then(()=>setTimeout(()=>tick(true),300));}
function editIntent(id){var t=(lastState.intents||[]).find(x=>x.intent.id===id);if(!t)return;var p=t.intent.plan;
 g('of_dir').value=p.direction;g('of_entry').value=p.entries[0].price;g('of_stop').value=p.stop;
 g('of_t1').value=(p.targets[0]||{}).price||'';g('of_t2').value=(p.targets[1]||{}).price||'';g('of_size').value=p.size_cap_frac;
 window.scrollTo(0,0);g('of_msg').textContent='已载入到表单,改完点「创建待批挂单」(旧的可拒绝)';}
function mod(id,a){fetch('/modify?id='+encodeURIComponent(id)+'&action='+a).then(()=>setTimeout(()=>tick(true),200));}
function modstop(id){var v=document.getElementById('s_'+id).value;if(v)fetch('/modify?id='+encodeURIComponent(id)+'&action=stop&value='+encodeURIComponent(v)).then(()=>setTimeout(()=>tick(true),200));}
function modtp(id){var p=new URLSearchParams({id:id,stop:g('m_stop_'+id).value,t1:g('m_t1_'+id).value,t2:g('m_t2_'+id).value});fetch('/modtp?'+p.toString()).then(()=>setTimeout(()=>tick(true),300));}
function livePnl(){var sd=(lastState.symbols||{}); for(var s in sd){} (lastState.trades||[]).forEach(function(t){if(t.trade.symbol!==sym)return;var st=t.state||{};if(st.status!=='active'||!st.avg||!st.position)return;var sign=t.trade.plan.direction==='short'?-1:1;var px=window._lastClose||st.mark||st.avg;var r=sign*(px-st.avg)/(t.trade.plan.risk_dist||1)*st.position;var pct=(t.trade.plan.size_cap_frac||0)*sign*(px-st.avg)/st.avg*100;var el=document.getElementById('pnl_'+t.trade.id);if(el)el.innerHTML='浮盈 <b>'+r.toFixed(2)+'R</b> ('+(pct>=0?'+':'')+pct.toFixed(2)+'%权益) 现价'+px.toLocaleString();});}
loadBars(false);tick(false);setInterval(()=>tick(true),__REFRESH__*1000);
setInterval(updateLast,1000);   // 1s: only candle.update(last bar) — never touches zoom/pan
</script></body></html>"""


def render_shell(refresh: int = 10) -> str:
    return _SHELL.replace("__CDN__", _CDN).replace("__REFRESH__", str(refresh))


def serve(port=8799, refresh=10, paper_path="~/quant/paper_state.json", equity0=10000.0,
          book_path="~/quant/paper_trades.json", intents_path="~/quant/paper_intents.json"):
    t = threading.Thread(target=_refresher, args=(paper_path, equity0, refresh, book_path, intents_path),
                         daemon=True)
    t.start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    httpd.refresh = refresh
    httpd.book_path = book_path
    httpd.intents_path = intents_path
    print(f"dashboard → http://127.0.0.1:{port}  (refresh {refresh}s, Ctrl-C to stop)", flush=True)
    httpd.serve_forever()


def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(prog="quant.dashboard", description=__doc__)
    p.add_argument("--port", type=int, default=8799)
    p.add_argument("--refresh", type=int, default=10)
    p.add_argument("--paper", default="~/quant/paper_state.json")
    p.add_argument("--book", default="~/quant/paper_trades.json", help="discretionary trade book")
    p.add_argument("--intents", default="~/quant/paper_intents.json", help="pending order intents")
    p.add_argument("--equity", type=float, default=10000.0)
    args = p.parse_args(argv)
    serve(port=args.port, refresh=args.refresh, paper_path=args.paper, equity0=args.equity,
          book_path=args.book, intents_path=args.intents)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
