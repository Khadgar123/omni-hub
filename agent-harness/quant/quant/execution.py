"""Order-plan ENGINE — turn a human's directional call into a concrete batch of
resting maker orders at GOOD structural positions, then simulate the lifecycle.

The division of labour (settled): direction is the HUMAN's (global macro / capital
flow / fundamentals — price can't see them, a ~0.59 wall); execution is the
MACHINE's. Given ``direction`` + ``conviction`` and live bars, the engine computes:

  * **Entries at real structure, not round numbers** — inside the nearest demand /
    supply *order block* (the candle origin of a structure break, where size sat).
    Tranches are weighted DEEPER into the zone (小仓在浅、重仓在深) so we don't
    commit early / catch a falling knife (你不要买的太早). The proximal tranche is
    meant to arm only on a confirmed reaction (the 1min executor does this).
  * **Stop FIRST, then size** (优先止损) — the stop sits just BEYOND the zone's
    distal wick plus a buffer (beyond stop-hunt reach, 不要被骗), triggered on a
    CLOSE not a wick; position size = risk budget ÷ stop distance.
  * **Adaptive management** — low conviction ⇒ defensive (R-based scale-outs +
    reverse-CHoCH exit); high conviction ⇒ let it run (minimal scale-out).
  * **0-fee precision** — defaults to BTCUSDC (0 maker fee, tick 0.01), which lowers
    the break-even accuracy; all prices snap to tick.

NEVER places an order: returns an ``OrderPlan`` to be emitted as
``Proposal(kind="order_intent")`` for human approval; a broker CLI executes after.
Pure stdlib, reuses ``quant.features`` / ``quant.structure`` / ``quant.levels``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

from quant import levels as levels_mod
from quant import structure as structure_mod
from quant.features import atr as atr_series

# venue-default tick size + maker fee (BTCUSDC spot = 0 maker on Binance)
SYMBOL_SPECS = {
    "BTCUSDC": {"tick": 0.01, "maker_bps": 0.0},
    "ETHUSDC": {"tick": 0.01, "maker_bps": 0.0},
    "BTCUSDT": {"tick": 0.1, "maker_bps": 1.0},
    "ETHUSDT": {"tick": 0.01, "maker_bps": 1.0},
}


def _round_tick(p: float, tick: float) -> float:
    return round(round(p / tick) * tick, 8) if tick else round(p, 2)


@dataclass(slots=True)
class OrderLeg:
    price: float
    size_frac: float          # fraction of the full intended position (entries sum ≈ 1.0)
    role: str                 # "entry" | "scale_out" | "final"
    label: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class OrderPlan:
    asof: int
    symbol: str
    direction: str            # "long" | "short" | "flat"
    conviction: float
    ref_price: float
    atr: float
    entries: list[OrderLeg]
    stop: float
    stop_kind: str            # "zone" | "structure" | "atr"
    risk_dist: float          # |avg_entry - stop| (one "R")
    targets: list[OrderLeg]
    final_target: float
    rr: float
    size_cap_frac: float      # equity fraction (vol-target × conviction)
    mandatory_stop_rule: str
    mandatory_tp_rule: str
    rationale: str
    manage_style: str = "adaptive"   # "runner" | "defensive"
    quality: str = ""                # "order_block" | "ladder_fallback"
    entry_zone: dict | None = None
    tick: float = 0.01
    maker_bps: float = 0.0
    disaster_stop: float = 0.0       # hard intrabar cap (further out) — bounds tail when close-confirm lags
    kind: str = "order_intent"
    schema_version: str = "orderplan-v2"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entries"] = [e.to_dict() for e in self.entries]
        d["targets"] = [t.to_dict() for t in self.targets]
        return d


def _levels_one_side(scored: list[dict], ref: float, *, below: bool) -> list[float]:
    side = [L for L in scored if (L["price"] < ref if below else L["price"] > ref)]
    side.sort(key=lambda L: (ref - L["price"]) if below else (L["price"] - ref))
    return [L["price"] for L in side]


def _zone_entries(ref, a, sign, zones, tranches, max_depth_atr, tick):
    """Entries inside the nearest order-block zone on the pullback side, weighted
    DEEPER (don't buy too early). Returns (entries, zone, distal_price, quality)."""
    below = sign > 0
    band = max_depth_atr * a
    want = "demand" if below else "supply"
    cand = []
    for z in zones:
        if z["kind"] != want:
            continue
        prox = z["hi"] if below else z["lo"]          # the edge price first touches
        gap = (ref - prox) if below else (prox - ref)
        if -0.5 * a <= gap <= band:                   # below (long) / above (short), in band
            cand.append((gap, z))
    if not cand:
        return None
    cand.sort(key=lambda x: x[0])
    z = cand[0][1]
    lo, hi = z["lo"], z["hi"]
    if below:
        prox = min(hi, ref - tick)
        dist = lo
    else:
        prox = max(lo, ref + tick)
        dist = hi
    if (prox <= dist) if below else (prox >= dist):   # degenerate zone guard
        prox = dist + sign * max(0.5 * (hi - lo), a)
    prices = [prox, 0.5 * (prox + dist), dist]
    weights = sorted(tranches)                         # ascending: small proximal, big distal
    labels = ["proximal", "mid", "distal"]
    entries = [OrderLeg(price=_round_tick(p, tick), size_frac=w, role="entry",
                        label=f"OB-{z['kind']}-{lbl}")
               for p, w, lbl in zip(prices, weights, labels)]
    return entries, z, dist, "order_block"


def _ladder_entries(ref, a, sign, scored, tranches, max_depth_atr, tick):
    """Fallback when no order block: structural-support ladder, also deeper-weighted."""
    below = sign > 0
    band = max_depth_atr * a
    struct = [p for p in _levels_one_side(scored, ref, below=below)
              if 0.2 * a <= abs(p - ref) <= band]
    rungs: list[tuple[float, str]] = []
    for p in struct:
        if len(rungs) >= len(tranches):
            break
        if all(abs(p - q) > 0.4 * a for q, _ in rungs):
            rungs.append((p, "swing"))
    k = 1
    while len(rungs) < len(tranches):
        p = ref - sign * (0.75 * k) * a
        if all(abs(p - q) > 0.4 * a for q, _ in rungs):
            rungs.append((p, f"atr-{0.75*k:.2f}"))
        k += 1
    rungs.sort(key=lambda r: (ref - r[0]) if below else (r[0] - ref))
    weights = sorted(tranches)
    entries = [OrderLeg(price=_round_tick(p, tick), size_frac=w, role="entry", label=lbl)
               for (p, lbl), w in zip(rungs, weights)]
    deepest = entries[-1].price
    return entries, None, deepest, "ladder_fallback"


def _wall_entries(ref, a, sign, walls, tranches, max_depth_atr, tick):
    """Entries on real order-book WALLS (resting liquidity) when there's no fresh order
    block — strictly better than a guessed swing/round level. Deeper-weighted; backfills
    with ATR rungs if fewer walls than tranches. Returns (entries, None, distal, quality)."""
    below = sign > 0
    side = (walls or {}).get("bid_walls" if below else "ask_walls", [])
    band = max_depth_atr * a
    prices = []
    for w in side:
        gap = (ref - w["price"]) if below else (w["price"] - ref)
        if 0.2 * a <= gap <= band:
            prices.append(float(w["price"]))
    if not prices:
        return None
    prices.sort(key=lambda p: (ref - p) if below else (p - ref))
    prices = prices[:len(tranches)]
    k = 1
    while len(prices) < len(tranches):
        p = ref - sign * (0.75 * k) * a
        if all(abs(p - q) > 0.4 * a for q in prices):
            prices.append(p)
        k += 1
    prices.sort(key=lambda p: (ref - p) if below else (p - ref))
    entries = [OrderLeg(price=_round_tick(p, tick), size_frac=w, role="entry", label="wall")
               for p, w in zip(prices, sorted(tranches))]
    return entries, None, entries[-1].price, "order_wall"


def build_order_plan(symbol: str, direction: str, conviction: float,
                     bars: Sequence[dict], *, walls: dict | None = None,
                     atr_n: int = 14, risk_atr: float = 1.5,
                     rr: float = 4.0, tranches: Sequence[float] = (0.4, 0.35, 0.25),
                     max_depth_atr: float = 3.0, risk_per_trade: float = 0.01,
                     max_lev: float = 3.0, left: int = 3, right: int = 3,
                     fakeout_atr: float = 0.35, runner_conviction: float = 0.6,
                     hard_stop_mult: float = 1.8, tick: float | None = None,
                     maker_bps: float | None = None) -> OrderPlan:
    """Compute a maker-limit order plan for a human-supplied ``direction``.

    Entries rest inside the nearest demand/supply order block (deeper-weighted);
    the stop sits beyond the zone's distal wick + ``fakeout_atr``·ATR buffer
    (priority stop, fakeout-resistant, close-confirmed); size = ``risk_per_trade`` ÷
    stop distance, × conviction, capped at ``max_lev``. Management adapts to
    conviction (≥``runner_conviction`` ⇒ let it run; below ⇒ defensive scale-outs).
    """
    if not bars:
        raise ValueError("no bars")
    spec = SYMBOL_SPECS.get(symbol.upper(), {"tick": 0.01, "maker_bps": 0.0})
    tick = spec["tick"] if tick is None else tick
    maker_bps = spec["maker_bps"] if maker_bps is None else maker_bps
    direction = direction.lower()
    ref = float(bars[-1]["close"])
    asof = int(bars[-1].get("bucket_ts", 0))
    a = next((x for x in reversed(atr_series(bars, atr_n)) if x), None) or (0.01 * ref)
    if direction not in ("long", "short"):
        return OrderPlan(asof=asof, symbol=symbol, direction="flat", conviction=conviction,
                         ref_price=_round_tick(ref, tick), atr=round(a, 2), entries=[], stop=ref,
                         stop_kind="atr", risk_dist=0.0, targets=[], final_target=ref, rr=0.0,
                         size_cap_frac=0.0, mandatory_stop_rule="—", mandatory_tp_rule="—",
                         rationale="flat: no directional call", tick=tick, maker_bps=maker_bps)
    sign = 1 if direction == "long" else -1
    scored = levels_mod.scored_levels(bars, left=left, right=right, atr=a)
    zones = structure_mod.order_blocks(bars, left=left, right=right)

    zres = _zone_entries(ref, a, sign, zones, tranches, max_depth_atr, tick)
    if zres is not None:                                 # 1st choice: a fresh order block
        entries, zone, distal, quality = zres
    else:
        wres = _wall_entries(ref, a, sign, walls, tranches, max_depth_atr, tick) if walls else None
        if wres is not None:                             # 2nd: real order-book walls (resting liquidity)
            entries, zone, distal, quality = wres
        else:                                            # 3rd: swing/VP-node ladder
            entries, zone, distal, quality = _ladder_entries(ref, a, sign, scored, tranches, max_depth_atr, tick)
    wsum = sum(e.size_frac for e in entries) or 1.0
    avg_entry = sum(e.price * e.size_frac for e in entries) / wsum

    # STOP FIRST (优先止损): beyond the distal wick + buffer (no fakeout), or ATR if no zone.
    if zone is not None:
        stop = distal - sign * (fakeout_atr * a)
        stop_kind = "zone"
    else:
        beyond = _levels_one_side(scored, distal, below=(sign > 0))
        struct_stop = (beyond[0] - sign * fakeout_atr * a) if beyond else None
        atr_stop = avg_entry - sign * risk_atr * a
        if struct_stop is not None and (struct_stop < atr_stop if sign > 0 else struct_stop > atr_stop):
            stop, stop_kind = struct_stop, "structure"
        else:
            stop, stop_kind = atr_stop, "atr"
    stop = _round_tick(stop, tick)
    risk_dist = abs(avg_entry - stop)
    if risk_dist <= 0:
        risk_dist = risk_atr * a
        stop = _round_tick(avg_entry - sign * risk_dist, tick)
    disaster_stop = _round_tick(avg_entry - sign * hard_stop_mult * risk_dist, tick)

    final_target = avg_entry + sign * rr * risk_dist
    # adaptive management (A): conviction gates runner vs defensive
    runner = conviction >= runner_conviction
    manage_style = "runner" if runner else "defensive"
    targets: list[OrderLeg] = []
    if runner:                                          # let winners run: one de-risk scale at 3R
        targets.append(OrderLeg(price=_round_tick(avg_entry + sign * 3.0 * risk_dist, tick),
                                size_frac=0.25, role="scale_out", label="3R"))
        held = 0.75
    else:                                               # defensive: structural scale-outs ≥1R
        lo_so, hi_so = (avg_entry + risk_dist, final_target) if sign > 0 else (final_target, avg_entry - risk_dist)
        ahead = [p for p in _levels_one_side(scored, avg_entry, below=(sign < 0)) if lo_so <= p <= hi_so][:2]
        for j, p in enumerate(ahead):
            targets.append(OrderLeg(price=_round_tick(p, tick), size_frac=0.25, role="scale_out",
                                    label=f"level-{j+1}"))
        held = round(1.0 - 0.25 * len(targets), 4)
    targets.append(OrderLeg(price=_round_tick(final_target, tick), size_frac=held, role="final",
                            label=f"{rr:.1f}R"))

    stop_dist_frac = risk_dist / avg_entry if avg_entry else 0.05
    size_cap_frac = round(min(risk_per_trade / stop_dist_frac, max_lev) * max(0.0, min(1.0, conviction)), 4)

    inval = "跌破" if sign > 0 else "升破"
    mand_stop = (f"收盘{inval} {stop:.2f}（结构失效；distal wick 外留 {fakeout_atr:.2f}ATR 缓冲避插针）即全平"
                 f"——按收盘确认不被影线触发；硬止损 {disaster_stop:.2f}（{hard_stop_mult:.1f}R）盘中触及即砍，封住尾部")
    mand_tp = (f"触及 {_round_tick(final_target, tick):.2f}（{rr:.1f}R）｜反向CHoCH（结构反转）｜"
               f"波动衰竭(exhaustion) 任一即了结剩余仓位")
    fee_note = "0fee" if maker_bps == 0 else f"{maker_bps:.0f}bp"
    rationale = (f"{direction}×{conviction:.2f}｜{quality}埋单(均价≈{avg_entry:.0f},深仓重)｜"
                 f"止损{stop:.0f}({stop_kind},{risk_dist/a:.1f}ATR)｜目标{final_target:.0f}({rr:.1f}R)｜"
                 f"{manage_style}管理｜仓{size_cap_frac:.2f}×权益｜maker {fee_note}")
    return OrderPlan(asof=asof, symbol=symbol, direction=direction, conviction=round(conviction, 3),
                     ref_price=_round_tick(ref, tick), atr=round(a, 2), entries=entries,
                     stop=stop, stop_kind=stop_kind, risk_dist=round(risk_dist, 2), targets=targets,
                     final_target=_round_tick(final_target, tick), rr=rr, size_cap_frac=size_cap_frac,
                     mandatory_stop_rule=mand_stop, mandatory_tp_rule=mand_tp, rationale=rationale,
                     manage_style=manage_style, quality=quality, entry_zone=zone, tick=tick,
                     maker_bps=maker_bps, disaster_stop=disaster_stop)


def simulate_plan(plan: OrderPlan, future: Sequence[dict], *, fill_window: int = 6,
                  max_hold: int = 80, reverse_choch_exit=None, stop_on_close: bool = True) -> dict:
    """Walk ``future`` bars and play the plan's lifecycle with no look-ahead: fill
    maker entries when touched (within ``fill_window``); scale-out/target limits fill
    INTRABAR; the stop is CLOSE-confirmed by default (``stop_on_close``) so a wick
    that pierces and recovers does NOT take you out (不要被骗) — the tradeoff is the
    realized exit is the close, which can exceed 1R on a true breakdown. Set
    ``stop_on_close=False`` for an intrabar wick stop (exit AT the stop price).
    ``reverse_choch_exit`` defaults from the plan's ``manage_style`` (defensive only).
    Cost = maker fee on every leg. Returns ``{filled, avg_entry, exit_reason,
    realized_r, ret_frac, bars_held, fills}``."""
    if reverse_choch_exit is None:
        reverse_choch_exit = plan.manage_style != "runner"
    if plan.direction not in ("long", "short") or not future:
        return {"filled": 0, "exit_reason": "no_trade", "realized_r": 0.0, "ret_frac": 0.0,
                "bars_held": 0, "avg_entry": None, "fills": 0}
    sign = 1 if plan.direction == "long" else -1
    fee = plan.maker_bps / 10000.0
    entries = sorted(plan.entries, key=lambda e: -e.price if sign > 0 else e.price)
    pending = list(entries)
    filled: list[tuple[float, float]] = []
    start_exit = 0
    for k in range(min(fill_window, len(future))):
        hi, lo = float(future[k]["high"]), float(future[k]["low"])
        still = []
        for e in pending:
            if (lo <= e.price) if sign > 0 else (hi >= e.price):
                filled.append((e.price, e.size_frac))
            else:
                still.append(e)
        pending = still
        start_exit = k + 1
        if filled and not pending:
            break
    if not filled:
        return {"filled": 0, "exit_reason": "no_fill", "realized_r": 0.0, "ret_frac": 0.0,
                "bars_held": 0, "avg_entry": None, "fills": 0}
    fw = sum(f for _, f in filled)
    avg_entry = sum(p * f for p, f in filled) / fw
    risk = plan.risk_dist or (abs(avg_entry - plan.stop) or 1.0)
    targets = sorted(plan.targets, key=lambda t: t.price if sign > 0 else -t.price)
    remaining, realized, reason = fw, 0.0, "max_hold"
    tleft = list(targets)
    end = min(len(future), start_exit + max_hold)
    held_bars = 0
    for k in range(start_exit, end):
        held_bars = k - start_exit + 1
        hi, lo, cl = float(future[k]["high"]), float(future[k]["low"]), float(future[k]["close"])
        new_t = []                                    # intrabar limit-target fills (scale-out / final)
        for t in tleft:
            if ((hi >= t.price) if sign > 0 else (lo <= t.price)) and remaining > 0:
                take = min(t.size_frac, remaining)
                realized += take * sign * (t.price - avg_entry)
                remaining -= take
            else:
                new_t.append(t)
        tleft = new_t
        if remaining <= 1e-9:
            reason = "target"
            break
        if plan.disaster_stop and ((lo <= plan.disaster_stop) if sign > 0 else (hi >= plan.disaster_stop)):
            realized += remaining * sign * (plan.disaster_stop - avg_entry)   # hard intrabar cap (tail)
            remaining = 0.0
            reason = "disaster"
            break
        if stop_on_close:                             # no-fakeout: confirm on CLOSE, exit at close
            stopped, exitpx = ((cl <= plan.stop) if sign > 0 else (cl >= plan.stop)), cl
        else:                                         # intrabar wick stop, exit at the stop price
            stopped, exitpx = ((lo <= plan.stop) if sign > 0 else (hi >= plan.stop)), plan.stop
        if stopped:
            realized += remaining * sign * (exitpx - avg_entry)
            remaining = 0.0
            reason = "stop"
            break
        if reverse_choch_exit:
            ms = structure_mod.market_structure(future[: k + 1], left=3, right=3)
            if ms and ms[-1]["type"] == "CHoCH" and ms[-1]["dir"] == ("down" if sign > 0 else "up") \
                    and ms[-1]["idx"] >= start_exit:
                realized += remaining * sign * (cl - avg_entry)
                remaining = 0.0
                reason = "reverse_choch"
                break
    if remaining > 1e-9:
        cl = float(future[min(end, len(future)) - 1]["close"])
        realized += remaining * sign * (cl - avg_entry)
    realized -= fw * fee * avg_entry * 2          # maker fee in + out (0 for USDC)
    ret_frac = realized / (avg_entry * fw) if avg_entry else 0.0
    return {"filled": 1, "avg_entry": round(avg_entry, 2), "exit_reason": reason,
            "realized_r": round(realized / (risk * fw), 3) if risk else 0.0,
            "ret_frac": round(ret_frac, 5), "bars_held": held_bars, "fills": len(filled)}


def stream_executor(plan: OrderPlan, bars_1m: Sequence[dict], *, confirm_reclaim: bool = True) -> dict:
    """1min execution layer (D): replay the low-TF stream and produce fill events for
    the plan's maker entries. The PROXIMAL tranche arms only after a reaction — price
    tags the level then CLOSES back through it (a reclaim) — so we don't buy too early
    / catch a knife; the deeper tranches are passive limits that fill on a flush
    (a deeper tag = a better price). Returns ``{fills, filled_frac, avg_entry}``.

    This is decision-on-high-TF, execute-on-low-TF: the plan is the 4h thesis, the
    1min stream only decides HOW/WHEN the resting orders get filled."""
    if plan.direction not in ("long", "short") or not bars_1m:
        return {"fills": [], "filled_frac": 0.0, "avg_entry": None}
    sign = 1 if plan.direction == "long" else -1
    entries = sorted(plan.entries, key=lambda e: -e.price if sign > 0 else e.price)  # proximal first
    proximal = entries[0]
    tagged = False
    fills: list[dict] = []
    done: set[int] = set()
    for i, b in enumerate(bars_1m):
        hi, lo, cl = float(b["high"]), float(b["low"]), float(b["close"])
        ts = int(b.get("bucket_ts", i))
        for e in entries:
            if id(e) in done:
                continue
            touch = (lo <= e.price) if sign > 0 else (hi >= e.price)
            if e is proximal and confirm_reclaim:
                if touch:
                    tagged = True
                if tagged and ((cl > e.price) if sign > 0 else (cl < e.price)):   # reclaim confirmed
                    fills.append({"ts": ts, "price": round(cl, 2), "frac": e.size_frac, "armed": "reclaim"})
                    done.add(id(e))
            elif touch:                                                            # passive limit
                fills.append({"ts": ts, "price": e.price, "frac": e.size_frac, "armed": "limit"})
                done.add(id(e))
    fr = sum(f["frac"] for f in fills)
    avg = sum(f["price"] * f["frac"] for f in fills) / fr if fr else None
    return {"fills": fills, "filled_frac": round(fr, 4), "avg_entry": round(avg, 2) if avg else None}


def emit_intent(plan: OrderPlan, path) -> str:
    """Emit the plan as a Proposal-ready ``order_intent`` JSONL line (C). The stdlib
    seam: omni-hub reads this file and wraps it in ``Proposal(kind="order_intent")``
    for human approval — it does NOT import quant. Writing the line executes NOTHING."""
    import json
    from pathlib import Path

    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"kind": "order_intent", "schema_version": plan.schema_version,
           "symbol": plan.symbol, "asof": plan.asof, "plan": plan.to_dict()}
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return str(p)


def plan_from_live(symbol: str, direction: str, conviction: float, *, venue: str = "binance",
                   tf: str = "4h", opener=None, use_walls: bool = False, **kw) -> OrderPlan:
    """Fetch the latest real candles (via ``quant.live``'s injectable fetcher) and
    build a plan on them. ``use_walls`` also pulls the deep-book walls (gap-2) so a
    no-zone fallback rests on real liquidity. ``opener`` lets tests inject HTTP."""
    from quant import live as live_mod

    bars = live_mod.fetch_candles(symbol, tf, venue=venue, opener=opener)
    walls = None
    if use_walls:
        from quant import exdata
        try:
            walls = exdata.deep_walls(symbol, opener=opener)
        except Exception:
            walls = None
    return build_order_plan(symbol, direction, conviction, bars, walls=walls, **kw)


def render_plan(plan: OrderPlan) -> str:
    """Human-readable review surface (交互友好) — levels with %/distance, the R map,
    sizes, and the mandatory rules. Not a raw dict."""
    d, ref = plan, plan.ref_price
    if plan.direction == "flat":
        return f"[{plan.symbol}] flat — 无方向输入"
    arrow = "🔻做空" if plan.direction == "short" else "🔺做多"
    lines = [f"━━ {plan.symbol} {arrow}  把握{plan.conviction:.2f}  现价 {ref:,.2f}  ATR {plan.atr:,.0f}"
             f"  [{plan.manage_style}/{plan.quality}]"]
    if plan.entry_zone:
        z = plan.entry_zone
        lines.append(f"  入场区(order block {z['kind']}): {z['lo']:,.0f} – {z['hi']:,.0f}")
    lines.append("  挂单 maker 限价（深仓重，别买太早）:")
    for e in plan.entries:
        pct = (e.price / ref - 1) * 100
        lines.append(f"     {e.size_frac*100:>4.0f}%  @ {e.price:>11,.2f}  ({pct:+.1f}%, {e.label})")
    lines.append(f"  ⛔ 止损 {plan.stop:,.2f}  ({plan.stop_kind}, {plan.risk_dist/plan.atr:.1f}ATR, "
                 f"{(plan.stop/ref-1)*100:+.1f}%)  = 1R")
    lines.append("  🎯 止盈（分批）:")
    for t in plan.targets:
        rmult = abs(t.price - ref) / plan.risk_dist if plan.risk_dist else 0
        lines.append(f"     {t.size_frac*100:>4.0f}%  @ {t.price:>11,.2f}  (~{rmult:.1f}R, {t.label})")
    lines.append(f"  仓位上限 {plan.size_cap_frac:.2f}×权益   maker费 {plan.maker_bps:.0f}bp")
    lines.append(f"  必止损: {plan.mandatory_stop_rule}")
    lines.append(f"  必止盈: {plan.mandatory_tp_rule}")
    lines.append("  → Proposal(order_intent) 等审批，引擎不下实盘")
    return "\n".join(lines)


def main(argv=None):
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(prog="quant.execution", description=__doc__)
    p.add_argument("--symbol", default="BTCUSDC")
    p.add_argument("--direction", required=True, choices=["long", "short", "flat"])
    p.add_argument("--conviction", type=float, default=0.55)
    p.add_argument("--venue", default="binance", choices=["coinbase", "kraken", "binance"])
    p.add_argument("--tf", default="4h")
    p.add_argument("--rr", type=float, default=4.0)
    p.add_argument("--json", action="store_true", help="emit raw OrderPlan json instead of rendered")
    args = p.parse_args(argv)
    plan = plan_from_live(args.symbol, args.direction, args.conviction, venue=args.venue,
                          tf=args.tf, rr=args.rr)
    if args.json:
        json.dump(plan.to_dict(), sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_plan(plan) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
