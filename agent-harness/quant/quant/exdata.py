"""Exchange microstructure + positioning data — the inputs the candle stream can't
see, for (a) PRECISE levels (real resting liquidity) and (b) the human's auxiliary
direction dashboard.

What the public Binance API gives us (probed, no auth needed):
  * **order-book depth** → the actual bid/ask WALLS (precise S/R = where size rests,
    not a guessed round number);
  * **open interest** (+ history) → leverage building vs unwinding; an OI collapse on
    a wick = a liquidation flush / stop-hunt (不要被骗), not a real break;
  * **funding** → the carry leg + long-crowding gauge;
  * **long/short account ratio** + **taker buy/sell ratio** → positioning sentiment +
    the real aggressor flow.

Direction is still the HUMAN's call — this module only assembles the evidence
(``dashboard``). Pure-stdlib ``urllib``; the HTTP getter is injectable so every
mapper/aggregator is unit-testable with NO network. NEVER places an order.
"""

from __future__ import annotations

import json
import urllib.request

_SPOT = "https://api.binance.com/api/v3"
_FAPI = "https://fapi.binance.com"
_UA = "omni-hub-quant-exdata/0.1"


def _get(url, *, opener=None, timeout=12.0):
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with opener(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _perp(symbol: str) -> str:
    """Spot USDC symbol -> the USDT perp that carries OI/funding/ratio data."""
    s = symbol.upper()
    return s.replace("USDC", "USDT") if s.endswith("USDC") else s


# ---------------------------------------------------------------- order book
def fetch_depth(symbol, *, limit=500, opener=None, timeout=12.0) -> dict:
    raw = _get(f"{_SPOT}/depth?symbol={symbol.upper()}&limit={limit}", opener=opener, timeout=timeout)
    bids = [(float(p), float(q)) for p, q in raw.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in raw.get("asks", [])]
    return {"bids": bids, "asks": asks}


def order_walls(depth: dict, *, top=4, merge_pct=0.0008) -> dict:
    """Largest resting-size price levels = the real S/R walls. Nearby levels merge
    (size-weighted) so one fat wall isn't double-counted. Returns
    ``{bid_walls, ask_walls}`` each ``[{price, qty}]`` sorted by descending qty."""
    def walls(side):
        side = sorted(side, key=lambda x: x[0])
        clusters: list[list[tuple]] = []
        for p, q in side:
            if clusters and abs(p - clusters[-1][-1][0]) <= merge_pct * p:
                clusters[-1].append((p, q))
            else:
                clusters.append([(p, q)])
        out = []
        for cl in clusters:
            tot = sum(q for _, q in cl)
            price = sum(p * q for p, q in cl) / tot if tot else cl[0][0]
            out.append({"price": round(price, 2), "qty": round(tot, 3)})
        out.sort(key=lambda w: -w["qty"])
        return out[:top]
    return {"bid_walls": walls(depth.get("bids", [])), "ask_walls": walls(depth.get("asks", []))}


# ---------------------------------------------------------- futures positioning
# spot fees in bps (maker, taker). USDC pairs: 0 MAKER (Binance promo) but TAKER is standard ~0.1%
# (the promo zeroes maker only) — and a STOP exits as taker (market), so taker is the real cost.
# Override via maker_taker=(mk,tk) with your actual VIP rate (e.g. 7.5 with BNB discount).
SPOT_FEES = {"BTCUSDC": (0.0, 10.0), "ETHUSDC": (0.0, 10.0), "SOLUSDC": (0.0, 10.0),
             "BTCUSDT": (10.0, 10.0), "ETHUSDT": (10.0, 10.0)}


def round_trip_cost(symbol, *, opener=None, timeout=12.0, maker_taker=None) -> dict:
    """PRECISE live round-trip cost of one trade. Maker/spread is ~0 on 0-fee USDC, so the real
    cost is the TAKER fee (a stop exits as a market/taker order). One taker leg = ``taker × notional``;
    a trade pays at least one (stop exit), often two (market in + market out). Reads the live
    top-of-book. Returns ``{mid, spread, maker_bps, taker_bps, taker_leg_usd, cost_maker_in_taker_out,
    cost_taker_both, cost_bps_taker_both}``. Use ``cost_taker_both`` (or _maker_in_taker_out for a
    limit-in/stop-out trade) as the MIN ambush spacing — a tranche must beat it to be worth a fill.
    ``cost_bps`` is symbol-agnostic so BTC and ETH share one rule, $-spacing scales by price."""
    d = fetch_depth(symbol, limit=5, opener=opener, timeout=timeout)
    if not d["bids"] or not d["asks"]:
        return {"mid": None, "spread": None, "taker_bps": None, "cost_taker_both": None}
    bid, ask = d["bids"][0][0], d["asks"][0][0]
    mid = (bid + ask) / 2
    spread = ask - bid
    mk, tk = maker_taker or SPOT_FEES.get(symbol.upper(), (10.0, 10.0))
    taker_leg = tk / 1e4 * mid                                   # $ per unit, one taker fill
    mito = 0.5 * spread + taker_leg                              # limit in (~free), stop市价 out
    tboth = spread + 2 * taker_leg                               # 市价 in + 市价 out
    return {"mid": round(mid, 2), "spread": round(spread, 2), "spread_bps": round(spread / mid * 1e4, 3),
            "maker_bps": mk, "taker_bps": tk, "taker_leg_usd": round(taker_leg, 2),
            "cost_maker_in_taker_out": round(mito, 2), "cost_taker_both": round(tboth, 2),
            "cost_bps_taker_both": round(tboth / mid * 1e4, 3)}


def deep_walls(symbol, *, top=4, limit=1000, opener=None, timeout=12.0) -> dict:
    """Order-book walls from the DEEP sibling book. Gap-2 fix: BTCUSDC's own book is
    thin (~45 BTC), so we read walls from BTCUSDT (deep liquidity) — prices transfer
    1:1 — while the order itself rests on BTCUSDC (0 maker). Returns ``order_walls``."""
    perp = symbol.upper().replace("USDC", "USDT")
    return order_walls(fetch_depth(perp, limit=limit, opener=opener, timeout=timeout), top=top)


def fetch_open_interest(symbol, *, opener=None, timeout=12.0) -> float:
    r = _get(f"{_FAPI}/fapi/v1/openInterest?symbol={_perp(symbol)}", opener=opener, timeout=timeout)
    return float(r.get("openInterest", 0.0))


def fetch_oi_hist(symbol, *, period="4h", limit=8, opener=None, timeout=12.0) -> list[dict]:
    r = _get(f"{_FAPI}/futures/data/openInterestHist?symbol={_perp(symbol)}&period={period}&limit={limit}",
             opener=opener, timeout=timeout)
    return [{"ts": int(x["timestamp"]), "oi": float(x["sumOpenInterest"])} for x in r] if isinstance(r, list) else []


def fetch_funding(symbol, *, opener=None, timeout=12.0) -> dict:
    r = _get(f"{_FAPI}/fapi/v1/premiumIndex?symbol={_perp(symbol)}", opener=opener, timeout=timeout)
    last = float(r.get("lastFundingRate", 0.0))
    return {"last": last, "annualized": last * 3 * 365, "mark": float(r.get("markPrice", 0.0))}


def fetch_long_short(symbol, *, period="4h", opener=None, timeout=12.0) -> float:
    r = _get(f"{_FAPI}/futures/data/globalLongShortAccountRatio?symbol={_perp(symbol)}&period={period}&limit=1",
             opener=opener, timeout=timeout)
    return float(r[-1]["longShortRatio"]) if isinstance(r, list) and r else float("nan")


def fetch_taker_ratio(symbol, *, period="4h", opener=None, timeout=12.0) -> float:
    r = _get(f"{_FAPI}/futures/data/takerlongshortRatio?symbol={_perp(symbol)}&period={period}&limit=1",
             opener=opener, timeout=timeout)
    return float(r[-1]["buySellRatio"]) if isinstance(r, list) and r else float("nan")


def oi_flush(oi_hist: list[dict], *, drop_pct=0.03) -> bool:
    """True if OI dropped >= ``drop_pct`` over the last bar — a deleveraging /
    liquidation flush. A wick on an OI flush is a stop-hunt, not a real break."""
    if len(oi_hist) < 2 or oi_hist[-2]["oi"] <= 0:
        return False
    return (oi_hist[-1]["oi"] / oi_hist[-2]["oi"] - 1) <= -abs(drop_pct)


def dashboard(symbol="BTCUSDC", *, opener=None, timeout=12.0, resilient=True) -> dict:
    """Assemble the auxiliary-info dashboard for the HUMAN to judge direction (it does
    NOT decide direction). Each field is fetched independently; with ``resilient`` a
    failed field becomes ``None`` so a single venue hiccup never blanks the board."""
    def safe(fn):
        if not resilient:
            return fn()
        try:
            return fn()
        except Exception:
            return None

    depth = safe(lambda: fetch_depth(symbol, opener=opener, timeout=timeout))
    walls = order_walls(depth) if depth else None
    oih = safe(lambda: fetch_oi_hist(symbol, opener=opener, timeout=timeout)) or []
    funding = safe(lambda: fetch_funding(symbol, opener=opener, timeout=timeout))
    ls = safe(lambda: fetch_long_short(symbol, opener=opener, timeout=timeout))
    taker = safe(lambda: fetch_taker_ratio(symbol, opener=opener, timeout=timeout))
    oi_now = oih[-1]["oi"] if oih else safe(lambda: fetch_open_interest(symbol, opener=opener, timeout=timeout))
    oi_delta = (oih[-1]["oi"] / oih[-2]["oi"] - 1) if len(oih) >= 2 and oih[-2]["oi"] else None

    notes = []
    if funding and funding["annualized"] > 0.30 and (ls is not None and ls > 2.0):
        notes.append("资金费偏高 + 多空账户比高 → 多头拥挤，追多/抄反弹风险高")
    if funding and funding["annualized"] < 0:
        notes.append("资金费为负 → 空头在付费，常见于超卖/恐慌，留意反弹")
    if oi_delta is not None and oi_delta <= -0.03:
        notes.append("OI 骤降 → 去杠杆/强平洗盘；此时的影线多为插针，别被骗")
    if oi_delta is not None and oi_delta >= 0.03:
        notes.append("OI 攀升 → 杠杆在加仓，趋势由杠杆推动（双向放大波动）")
    if taker is not None and taker < 0.9:
        notes.append("主动买/卖 < 1 → 真实成交以主动卖压为主")
    elif taker is not None and taker > 1.1:
        notes.append("主动买/卖 > 1 → 真实成交以主动买盘为主")

    return {
        "symbol": symbol,
        "order_walls": walls,
        "open_interest": {"now": oi_now, "delta_pct": oi_delta, "flush": oi_flush(oih)},
        "funding": funding,
        "long_short_ratio": ls,
        "taker_buy_sell": taker,
        "notes": notes,
        "disclaimer": "辅助信息（真实挂单墙/持仓/资金费/情绪），供人判断方向；机械聚合，非投资建议、非涨跌预测。",
    }


def render_dashboard(d: dict) -> str:
    lines = [f"━━ 辅助仪表盘 {d['symbol']}（帮你定方向，系统不替你定）"]
    w = d.get("order_walls")
    if w:
        bw = " ".join(f"{x['price']:,.0f}({x['qty']:.1f})" for x in w["bid_walls"][:3])
        aw = " ".join(f"{x['price']:,.0f}({x['qty']:.1f})" for x in w["ask_walls"][:3])
        lines.append(f"  买墙(支撑): {bw or '—'}")
        lines.append(f"  卖墙(阻力): {aw or '—'}")
    oi = d.get("open_interest", {})
    if oi.get("now") is not None:
        dp = oi.get("delta_pct")
        lines.append(f"  持仓量 OI: {oi['now']:,.0f}  Δ {'' if dp is None else f'{dp*100:+.1f}%'}"
                     f"{'  ⚠强平洗盘' if oi.get('flush') else ''}")
    f = d.get("funding")
    if f:
        lines.append(f"  资金费: {f['last']*100:+.4f}%/8h (年化 {f['annualized']*100:+.0f}%)")
    if d.get("long_short_ratio") == d.get("long_short_ratio"):  # not NaN
        lines.append(f"  多空账户比: {d.get('long_short_ratio')}   主动买卖比: {d.get('taker_buy_sell')}")
    for n in d.get("notes", []):
        lines.append(f"  • {n}")
    return "\n".join(lines)
