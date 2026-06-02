"""Unified asset analysis framework — a STRUCTURED, multi-layer LIVE state read.

Composes the angles that actually carry information (react-not-predict; no LLM; no orders):

  regime    — vol+trend committee (``quant.regime``) across MTF live klines + composite bias
  carry     — funding (rate / annualized / 30d percentile / 7d trend), basis, open interest
  orderflow — REAL taker-delta / CVD (``quant.orderflow``): who is aggressing right now
  macro     — best-effort risk-on/off dashboard (NASDAQ / VIX / VIX-term / DXY / HYG)
  synthesis — the EDGE AUDIT: marginal counterparty, fragility flags, and the triggers to watch

Output is a plain dict (the cross-plane read contract). Every layer is mechanical/flow data;
NOTHING here predicts price or places an order. HTTP getter is injectable for tests.

CLI:  python -m quant.framework --symbol BTCUSDT [--venue binance] [--no-macro]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from quant import orderflow, regime
from quant import live as live_mod
from quant.market_state import _compose_bias

_FAPI = "https://fapi.binance.com/fapi/v1"
_UA = {"User-Agent": "omni-hub-quant-framework/0.1"}
_TFS = ("1d", "4h", "1h", "15m")


def _getj(url, *, opener=None, timeout=15.0):
    opener = opener or urllib.request.urlopen
    with opener(urllib.request.Request(url, headers=_UA), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def carry(symbol: str = "BTCUSDT", *, opener=None) -> dict:
    """Funding (rate / annualized / 30d-percentile / 7d-trend), basis (mark vs index), OI."""
    pi = _getj(f"{_FAPI}/premiumIndex?symbol={symbol}", opener=opener)
    mark, idx, fr = float(pi["markPrice"]), float(pi["indexPrice"]), float(pi["lastFundingRate"])
    frs = [float(x["fundingRate"]) for x in _getj(f"{_FAPI}/fundingRate?symbol={symbol}&limit=90", opener=opener)]
    pct = 100.0 * sum(1 for x in frs if x < fr) / len(frs) if frs else float("nan")
    last7 = frs[-21:] or frs
    tr7 = (sum(last7) / max(len(last7), 1)) * 3 * 365 * 100
    oi = float(_getj(f"{_FAPI}/openInterest?symbol={symbol}", opener=opener)["openInterest"])
    return {"mark": mark, "basis_pct": round((mark - idx) / idx * 100, 3),
            "funding_8h_pct": round(fr * 100, 4), "funding_ann_pct": round(fr * 3 * 365 * 100, 1),
            "funding_pctile_30d": round(pct, 0), "funding_trend7_ann_pct": round(tr7, 1),
            "open_interest": oi,
            "crowd": "long" if pct >= 80 else ("short" if pct <= 20 else "neutral")}


def regime_mtf(symbol: str = "BTCUSDT", venue: str = "binance", tfs=_TFS, *, opener=None) -> dict:
    """Per-TF regime + the top-down composite bias (1d sole bias, 4h may veto to flat)."""
    bars_by = {tf: live_mod.fetch_candles(symbol, tf, venue=venue, opener=opener) for tf in tfs}
    per_tf = {tf: regime.classify(bars_by[tf]).to_dict() for tf in tfs}
    htf, conf = tfs[0], (tfs[1] if len(tfs) > 1 else tfs[0])
    composite = _compose_bias(regime.classify(bars_by[htf]), regime.classify(bars_by[conf]))
    return {"per_tf": per_tf, "composite_bias": composite, "_bars": bars_by}


def macro(*, opener=None) -> dict:
    """Best-effort risk-on/off dashboard (Yahoo). Returns {} on any failure (never raises)."""
    def yh(sym):
        for h in ("query1", "query2"):
            try:
                d = _getj(f"https://{h}.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=2mo", opener=opener)
                return [x for x in d["chart"]["result"][0]["indicators"]["quote"][0]["close"] if x is not None]
            except Exception:
                continue
        return None

    out: dict = {}
    try:
        for sym, nm in [("^IXIC", "nasdaq"), ("^VIX", "vix"), ("^VIX3M", "vix3m"), ("DX-Y.NYB", "dxy"), ("HYG", "hyg")]:
            cl = yh(sym)
            if cl:
                out[nm] = {"last": round(cl[-1], 1),
                           "chg10d_pct": round((cl[-1] / cl[-11] - 1) * 100, 1) if len(cl) > 11 else None}
        if out.get("vix") and out.get("vix3m") and out["vix3m"]["last"]:
            r = out["vix"]["last"] / out["vix3m"]["last"]
            out["vix_term"] = round(r, 2)
            out["vol_state"] = "stress(backwardation)" if r > 1 else "calm(contango)"
        nq = (out.get("nasdaq") or {}).get("chg10d_pct") or 0
        vx = (out.get("vix") or {}).get("last") or 99
        out["risk"] = "on" if (nq > 0 and vx < 20) else "off/mixed"
    except Exception:
        pass
    return out


def synthesize(reg: dict, car: dict, ofl: dict, mac: dict, levels: dict) -> dict:
    """The edge audit (rules-based, NO prediction): who's the marginal counterparty, what's
    fragile, and which mechanical triggers flip the read. Returns a structured dict."""
    flags: list[str] = []
    crowd, comp = car.get("crowd"), reg.get("composite_bias")
    if crowd == "long":
        cp = f"杠杆多头拥挤(funding {car.get('funding_pctile_30d'):.0f} 分位)"
        if comp in ("short", "flat"):
            flags.append("拥挤多头撞着非多头 regime → 多头踩踏风险")
    elif crowd == "short":
        cp = "杠杆空头拥挤 / capitulation → 潜在轧空(偏底部上下文)"
    else:
        cp = "杠杆持仓中性"
    if car.get("basis_pct", 0) < 0:
        flags.append("basis 微负(永续≤现货,持仓偏空)")
    if ofl.get("flow") == "sell":
        flags.append("订单流:主动卖占优(taker-delta<0)" + ("" if ofl.get("real") else " [代理]"))
    if ofl.get("divergence"):
        flags.append("订单流背离:" + ofl["divergence"])
    if any(reg["per_tf"][tf].get("stand_down") for tf in reg["per_tf"]):
        flags.append("有级别 stand_down(变点/波动扩张)")
    if mac.get("risk") == "on" and comp in ("short", "flat"):
        flags.append("宏观 risk-on 但本币弱 → 特质性走弱/脱钩")

    triggers = []
    if levels.get("res"):
        triggers.append(f"收回 {levels['res']:,.0f}(4h 前高)= 转多确认")
    if levels.get("sup"):
        triggers.append(f"丢 {levels['sup']:,.0f}(4h 前低)= 转空确认")
    triggers += ["ETF 流由负转正 / funding 转负 = BTC 特质拐点信号",
                 "VIX 跳 / 信用利差走阔 = 宏观 risk-off 扳机"]

    return {"counterparty": cp, "lean_mechanical": comp, "fragility": flags, "watch": triggers,
            "note": "lean 是机械偏向不是预测;edge 只在你对对手盘有真实信息/解读优势处",
            "disclaimer": "机械指标/流数据的状态读数,非投资建议、非涨跌预测"}


def read(symbol: str = "BTCUSDT", venue: str = "binance", *, opener=None, with_macro: bool = True) -> dict:
    """The full framework read for ``symbol`` as one structured dict."""
    car = carry(symbol, opener=opener)
    reg = regime_mtf(symbol, venue, opener=opener)
    op_bars = reg["_bars"].get("15m") or next(iter(reg["_bars"].values()))
    ofl = orderflow.read(op_bars)
    mac = macro(opener=opener) if with_macro else {}
    levels = {}
    b4 = reg["_bars"].get("4h")
    if b4 and len(b4) > 21:
        levels = {"res": max(x["high"] for x in b4[-21:-1]), "sup": min(x["low"] for x in b4[-21:-1])}
    syn = synthesize(reg, car, ofl, mac, levels)
    reg.pop("_bars", None)
    return {"symbol": symbol, "carry": car, "regime": reg, "orderflow": ofl, "macro": mac,
            "levels": levels, "synthesis": syn}


def _print(r: dict) -> None:
    c, reg, of, m, s = r["carry"], r["regime"], r["orderflow"], r["macro"], r["synthesis"]
    print(f"=== {r['symbol']} framework read ===")
    print(f"① CARRY    mark={c['mark']:,.0f} basis={c['basis_pct']:+.2f}% "
          f"funding={c['funding_8h_pct']:+.4f}%/8h(~{c['funding_ann_pct']:+.0f}%/yr) "
          f"{c['funding_pctile_30d']:.0f}分位 crowd={c['crowd']} OI={c['open_interest']:,.0f}")
    print(f"⑥ REGIME   composite={reg['composite_bias']}  " +
          " ".join(f"{tf}:{reg['per_tf'][tf]['label']}({reg['per_tf'][tf]['direction']})" for tf in reg['per_tf']))
    print(f"② ORDERFLOW flow={of['flow']} delta_recent={of['delta_recent']:+,.0f} "
          f"real={of['real']} div={of.get('divergence')}")
    if m:
        print(f"⑥ MACRO    risk={m.get('risk')} vix={m.get('vix',{}).get('last')} "
              f"term={m.get('vix_term')}({m.get('vol_state')}) nasdaq10d={m.get('nasdaq',{}).get('chg10d_pct')}%")
    print(f"→ 对手盘: {s['counterparty']}")
    print(f"→ 机械偏向: {s['lean_mechanical']}")
    print("→ 脆弱点: " + ("; ".join(s["fragility"]) or "无"))
    print("→ 盯: " + " | ".join(s["watch"]))
    print(f"※ {s['disclaimer']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="quant.framework", description=__doc__)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--venue", default="binance", choices=["binance", "coinbase", "kraken"])
    p.add_argument("--no-macro", action="store_true")
    p.add_argument("--json", action="store_true", help="emit the raw dict as JSON")
    a = p.parse_args(argv)
    r = read(a.symbol, a.venue, with_macro=not a.no_macro)
    if a.json:
        json.dump(r, sys.stdout, ensure_ascii=False, default=str, indent=2)
        sys.stdout.write("\n")
    else:
        _print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
