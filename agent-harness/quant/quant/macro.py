"""Global macro framework — the TradFi sibling of ``quant.framework`` (crypto). Reuses the
asset-agnostic ``quant.regime`` + ``quant.structure`` engines on DAILY bars across global assets,
plus a macro panel (rate curve / credit / vol / commodities) and a cross-asset strength + correlation
matrix, collapsed into a readable macro read.

Free sources reachable from here: yfinance (global prices), akshare (A-share index + US/China bond
yields). Honest scope: DAILY granularity only (no free intraday history for TradFi); the macro econ
series (CPI/PMI/jobs) live in akshare but lag ~months; JGB/Bund + fresh FRED = TODO (OECD/BLS).
NOTHING here predicts; it reports state + cross-asset relationships. No orders.

CLI:  python -m quant.macro [--json]
"""
from __future__ import annotations
import argparse
import json
import math
import sys
import warnings

warnings.filterwarnings("ignore")

from quant import regime, structure

ASSETS = {
    "^GSPC": "美S&P500", "^NDX": "美Nasdaq100", "000300.SS": "A股沪深300", "^N225": "日经225",
    "^KS11": "韩KOSPI", "GC=F": "黄金", "CL=F": "WTI原油", "HG=F": "铜",
    "^TNX": "美10Y收益", "DX-Y.NYB": "美元DXY", "CNY=X": "人民币", "BTC-USD": "BTC",
}
PANEL = {"^VIX": "VIX", "^MOVE": "MOVE", "HYG": "HY", "IEF": "UST7-10", "LQD": "IG"}


def _bars(df):
    out = []
    for ts, row in df.iterrows():
        o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
        if c != c or o != o:
            continue
        out.append({"open": float(o), "high": float(h), "low": float(l), "close": float(c),
                    "volume": float(row.get("Volume", 0) or 0), "bucket_ts": int(ts.timestamp() * 1_000_000)})
    return out


def _structure(bars, *, win=60):
    """Single-TF structure + Donchian S/R on daily bars (the macro analog of framework §②b/§②)."""
    out = {"trend": "?", "event": "—", "divergence": None, "support": None, "resistance": None, "pos": None}
    if len(bars) < 30:
        return out
    ms = structure.market_structure(bars, left=3, right=3)
    dv = structure.divergence(bars, left=3, right=3)
    last, ldv = (ms[-1] if ms else None), (dv[-1] if dv else None)
    if last:
        out["trend"] = last["dir"]
        out["event"] = f"{last['type']} {last['dir']}@{last['level']:,.2f}"
    if ldv and ldv.get("is_divergence"):
        out["divergence"] = f"{ldv['dir']}背驰·力度{ldv['metric_ratio']:.2f}"
    seg = bars[-(win + 1):-1]
    if seg:
        px = float(bars[-1]["close"])
        hi = max(float(b["high"]) for b in seg)
        lo = min(float(b["low"]) for b in seg)
        out.update(support=round(lo, 2), resistance=round(hi, 2),
                   pos=round((px - lo) / (hi - lo), 2) if hi > lo else 0.5)
    return out


def _corr(a, b):
    n = min(len(a), len(b))
    a, b = a[-n:], b[-n:]
    if n < 5:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / ((va * vb) ** 0.5) if va > 0 and vb > 0 else 0.0


def read(*, period="2y") -> dict:
    import yfinance as yf
    raw = yf.download(list(ASSETS) + list(PANEL), period=period, interval="1d", progress=False, group_by="ticker")
    assets, rets = {}, {}
    for tk, name in ASSETS.items():
        try:
            df = raw[tk].dropna()
            bars = _bars(df)
            reg = regime.classify(bars).to_dict()
            cl = df["Close"]
            mo1 = 100 * (cl.iloc[-1] / cl.iloc[-22] - 1) if len(cl) > 22 else float("nan")
            rets[tk] = [math.log(bars[i]["close"] / bars[i - 1]["close"]) for i in range(1, len(bars))]
            assets[tk] = {"name": name, "px": round(float(cl.iloc[-1]), 2), "mo1": round(mo1, 1),
                          "label": reg["label"], "direction": reg["direction"],
                          "vol_bucket": reg["vol_bucket"], "adx": reg.get("adx"),
                          "structure": _structure(bars)}
        except Exception as e:
            assets[tk] = {"name": name, "error": str(e)[:50]}

    panel = {}
    try:
        import akshare as ak
        b = ak.bond_zh_us_rate(start_date="20260401").dropna(subset=["美国国债收益率10年"]).iloc[-1]
        us10, us2, cn10 = float(b["美国国债收益率10年"]), float(b["美国国债收益率2年"]), float(b["中国国债收益率10年"])
        panel["curve"] = {"us2": us2, "us10": us10, "us2s10s": round(us10 - us2, 2),
                          "cn10": cn10, "us_cn_spread": round(us10 - cn10, 2)}
    except Exception as e:
        panel["curve"] = {"error": str(e)[:40]}
    try:
        def last(k): return float(raw[k]["Close"].dropna().iloc[-1])
        def mo(k):
            s = raw[k]["Close"].dropna()
            return round(100 * (s.iloc[-1] / s.iloc[-22] - 1), 1) if len(s) > 22 else None
        cr = (raw["HYG"]["Close"].dropna() / raw["IEF"]["Close"].dropna())
        panel["credit"] = {"hyg_ief": round(float(cr.iloc[-1]), 3), "hyg_ief_mo": round(100 * (cr.iloc[-1] / cr.iloc[-22] - 1), 1)}
        panel["vol"] = {"vix": round(last("^VIX"), 1), "vix_mo": mo("^VIX"), "move": round(last("^MOVE"), 0), "move_mo": mo("^MOVE")}
    except Exception as e:
        panel["credit_vol_error"] = str(e)[:40]
    panel["commodities"] = {"copper_mo": assets.get("HG=F", {}).get("mo1"), "oil_mo": assets.get("CL=F", {}).get("mo1")}
    panel["growth_inflation_note"] = "美ISM~48(收缩)·中CPI~0(通缩)·中PMI~49 — akshare,~2025-08 偏旧"

    # cross-asset: correlation matrix (1y) + leaders/laggards
    keys = [k for k in ASSETS if k in rets and len(rets[k]) > 60]
    n = min(252, min(len(rets[k]) for k in keys))
    corr = {k: {k2: round(_corr(rets[k][-n:], rets[k2][-n:]), 2) for k2 in keys} for k in keys}
    ranked = sorted((k for k in ASSETS if "mo1" in assets[k]), key=lambda k: assets[k]["mo1"], reverse=True)
    out = {"assets": assets, "panel": panel,
           "cross": {"corr_1y": corr, "leaders": ranked[:3], "laggards": ranked[-3:]}}
    out["narrative"] = narrate(out)
    return out


def narrate(r: dict) -> str:
    a, p = r["assets"], r["panel"]
    eq = ["^GSPC", "^NDX", "000300.SS", "^N225", "^KS11"]
    up = [a[k]["name"] for k in eq if a.get(k, {}).get("direction") == "up"]
    btc = a.get("BTC-USD", {})
    vix = p.get("vol", {}).get("vix")
    curve = p.get("curve", {})
    parts = []
    if len(up) >= 4:
        parts.append(f"全球股指普遍走强({'/'.join(n.replace('美','').replace('股','') for n in up[:5])} 全 up)。")
    if btc.get("direction") == "down":
        parts.append(f"唯独 BTC 独自下行({btc.get('mo1')}%/月),商品分化(铜{p.get('commodities',{}).get('copper_mo')}% / 油{p.get('commodities',{}).get('oil_mo')}%)。")
    if vix is not None:
        parts.append(f"金融条件宽松(VIX {vix}、信用 HYG/IEF {p.get('credit',{}).get('hyg_ief')} 无压力、MOVE {p.get('vol',{}).get('move')}),但增长偏软({p.get('growth_inflation_note','').split(' — ')[0]})。")
    if curve.get("us2s10s") is not None:
        parts.append(f"美 2s10s {curve['us2s10s']:+.2f}(正常化)、中美利差 {curve.get('us_cn_spread')}(美紧/中松)。")
    parts.append("综合:流动性 + 反通胀驱动的晚周期股票 melt-up,广度集中、其余资产先弱;state 不预测。")
    parts.append("※ 机械统计/公开数据状态分析,非投资建议、非涨跌预测。")
    return " ".join(parts)


def _print(r: dict) -> None:
    print("=== 全球宏观盘面 · 日线 ===")
    print("%-12s %-16s %-6s %-4s %-7s %s" % ("标的", "趋势(regime)", "vol", "ADX", "1月%", "结构"))
    for tk, name in ASSETS.items():
        x = r["assets"].get(tk, {})
        if "error" in x:
            print("%-12s ERR %s" % (name, x["error"])); continue
        s = x["structure"]
        adx = x.get("adx")
        print("%-12s %-16s %-6s %-4s %+6.1f%% %s" % (
            name, f"{x['label']}({x['direction']})", x["vol_bucket"],
            ("%.0f" % adx) if isinstance(adx, (int, float)) else "—", x["mo1"],
            (s["event"] + ("·" + s["divergence"] if s["divergence"] else "")) if s["trend"] != "?" else ""))
    p = r["panel"]
    c = p.get("curve", {})
    print("\n利率: 美2s10s %s · 中美利差 %s | 信用 HYG/IEF %s(%s) | VIX %s · MOVE %s" % (
        c.get("us2s10s"), c.get("us_cn_spread"), p.get("credit", {}).get("hyg_ief"),
        p.get("credit", {}).get("hyg_ief_mo"), p.get("vol", {}).get("vix"), p.get("vol", {}).get("move")))
    print("强弱: 领涨", [r["assets"][k]["name"] for k in r["cross"]["leaders"]],
          "| 落后", [r["assets"][k]["name"] for k in r["cross"]["laggards"]])
    print("\n" + r["narrative"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="quant.macro", description="Global macro daily dashboard.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--period", default="2y")
    a = ap.parse_args(argv)
    r = read(period=a.period)
    if a.json:
        json.dump(r, sys.stdout, ensure_ascii=False, default=str, indent=2)
        sys.stdout.write("\n")
    else:
        _print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
