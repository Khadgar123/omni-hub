"""Paper-trading broker — a virtual account that fills the baseline basket at LIVE
prices and marks to market. NO real money, NO credentials, NO real orders (autonomy
L0): paper uses only public market data and simulates fills locally, so it runs with a
real balance of 0. This is the "跑通 loop" surface before any capital is committed.

Accounting is standard signed-position avg-price PnL: a long is qty>0, a short qty<0;
reducing/flipping a position realizes PnL on the closed portion; equity = inception +
realized + unrealized(mark). ``tick_baseline`` turns a ``baseline.daily_decision`` into
signed target notionals (equity × gross_scale per leg, dollar-neutral) and rebalances.

State persists as one JSON so a launchd/cron tick can resume it. NEVER places an order
on a venue — when you later want real fills, that goes through ``Proposal(order_intent)``
+ your broker CLId post-approval, not this module.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class PaperState:
    inception_equity: float = 10000.0
    realized_pnl: float = 0.0
    fee_bps: float = 0.0                       # USDC 0 maker by default
    positions: dict = field(default_factory=dict)   # symbol -> {"qty": float, "avg": float}
    marks: dict = field(default_factory=dict)       # symbol -> last price
    fills: list = field(default_factory=list)       # recent fills (bounded)
    updated_ts: int = 0

    def unrealized(self) -> float:
        return sum(p["qty"] * (self.marks.get(s, p["avg"]) - p["avg"])
                   for s, p in self.positions.items())

    def equity(self) -> float:
        return self.inception_equity + self.realized_pnl + self.unrealized()

    def gross_exposure(self) -> float:
        return sum(abs(p["qty"]) * self.marks.get(s, p["avg"]) for s, p in self.positions.items())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["unrealized"] = round(self.unrealized(), 2)
        d["equity"] = round(self.equity(), 2)
        d["pnl_pct"] = round(100 * (self.equity() / self.inception_equity - 1), 3) if self.inception_equity else 0.0
        d["gross_exposure"] = round(self.gross_exposure(), 2)
        return d


def _adjust(pos: dict, tgt_qty: float, price: float) -> float:
    """Move a single position to ``tgt_qty`` at ``price``; return realized PnL on any
    closed portion (avg-price accounting; handles open/add/reduce/flip)."""
    qty, avg = pos["qty"], pos["avg"]
    realized = 0.0
    if qty == 0 or (qty > 0 and tgt_qty >= qty) or (qty < 0 and tgt_qty <= qty):
        # opening or adding on the same side -> weighted-average the entry
        if tgt_qty != 0:
            avg = (qty * avg + (tgt_qty - qty) * price) / tgt_qty
    elif qty * tgt_qty < 0:
        # flip: close the whole old position, reopen the remainder at price
        realized = qty * (price - avg)
        avg = price
    else:
        # reduce toward zero (same side, smaller magnitude): realize the closed part
        realized = (qty - tgt_qty) * (price - avg)
    pos["qty"], pos["avg"] = tgt_qty, avg
    return realized


def set_target(state: PaperState, targets_notional: dict, prices: dict, *, ts: int | None = None) -> PaperState:
    """Rebalance to signed target notionals ``{symbol: notional}`` at ``prices`` (positive
    = long, negative = short). Positions absent from the target are closed. Updates
    realized PnL, positions, fills, marks."""
    targets = dict(targets_notional)
    for s, p in list(state.positions.items()):
        if s not in targets and p["qty"] != 0:
            targets[s] = 0.0
    for s, notion in targets.items():
        price = prices.get(s)
        if not price or price <= 0:
            continue
        tgt_qty = notion / price
        pos = state.positions.setdefault(s, {"qty": 0.0, "avg": price})
        prev = pos["qty"]
        realized = _adjust(pos, tgt_qty, price)
        dqty = tgt_qty - prev
        if abs(dqty) > 1e-12:
            fee = abs(dqty) * price * state.fee_bps / 10000.0
            state.realized_pnl += realized - fee
            state.fills.append({"ts": ts or 0, "symbol": s, "side": "buy" if dqty > 0 else "sell",
                                "dqty": round(dqty, 6), "price": round(price, 2),
                                "realized": round(realized, 2)})
        if abs(pos["qty"]) < 1e-12:
            state.positions.pop(s, None)
    state.marks.update({s: prices[s] for s in prices})
    state.fills = state.fills[-50:]
    state.updated_ts = ts or state.updated_ts
    return state


def mark(state: PaperState, prices: dict) -> float:
    """Update marks from live prices and return equity (no trading)."""
    state.marks.update(prices)
    return state.equity()


def tick_baseline(state: PaperState, prices: dict, cfg=None, *, ts: int | None = None):
    """One paper step: run the baseline basket decision, turn it into dollar-neutral
    signed target notionals (equity × gross_scale per leg), and rebalance. Returns the
    BasketDecision so a dashboard can show it. No-op if the basket is empty."""
    from quant import baseline as bl

    cfg = cfg or bl.BaselineConfig()
    dec = bl.daily_decision(prices, cfg)
    spot = {s: prices[s][max(prices[s])] for s in prices if prices[s]}   # latest close per symbol
    n = len(dec.longs) + len(dec.shorts)
    if n and dec.gross_scale > 0:
        per = state.equity() * dec.gross_scale / n
        targets = {s: per for s, _ in dec.longs}
        targets.update({s: -per for s, _ in dec.shorts})
        set_target(state, targets, spot, ts=ts)
    else:
        mark(state, spot)
    return dec


def save_state(state: PaperState, path) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def load_state(path, *, inception_equity: float = 10000.0, fee_bps: float = 0.0) -> PaperState:
    p = Path(path).expanduser()
    if not p.exists():
        return PaperState(inception_equity=inception_equity, fee_bps=fee_bps)
    d = json.loads(p.read_text(encoding="utf-8"))
    return PaperState(**{k: d[k] for k in d if k in PaperState.__slots__})


# ---------------------------------------------------- discretionary trade book
# Human directional trades (the Layer-1 overlay) live in a SEPARATE book from the
# auto-rebalanced baseline, so a baseline tick never closes them. Each is an OrderPlan
# + the bar timestamp it was opened on; status is recomputed each tick by replaying
# simulate_plan over the bars since open (single source of truth, no drift).
def load_book(book_path) -> list:
    p = Path(book_path).expanduser()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _save_book(book_path, book) -> None:
    p = Path(book_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")


def record_trade(book_path, plan_dict: dict, *, symbol: str, tf: str, since_ts: int, note: str = "",
                 breakeven_at_r: float = 1.0) -> str:
    book = load_book(book_path)
    tid = f"{symbol}-{tf}-{int(since_ts)}"
    book.append({"id": tid, "symbol": symbol, "tf": tf, "since_ts": int(since_ts), "note": note,
                 "plan": plan_dict, "breakeven_at_r": breakeven_at_r, "overrides": {}})
    _save_book(book_path, book)
    return tid


def advance_trade(trade: dict, bars) -> dict:
    """STATEFUL step of a managed trade (big-TF thesis, small-TF entry): fill pending entries,
    auto-move the stop to BREAKEVEN once profit ≥ ``breakeven_at_r`` or a target prints (防止赚钱变亏钱),
    enforce the disaster stop intrabar (防止亏太多), take scale-out/final targets, close-confirm the stop.
    Honors ``trade['overrides']`` (your quick TP/SL edits, applied forward). Idempotent — re-running over
    the same bars (incl. the still-forming last bar) can't double-count (fills/targets are index-tracked,
    a closed status short-circuits). Mutates + returns ``trade['state']``."""
    plan = trade["plan"]
    sign = 1 if plan["direction"] == "long" else -1
    risk = plan.get("risk_dist") or 1.0
    ov = trade.setdefault("overrides", {})
    stop = ov.get("stop", plan["stop"])
    disaster = ov.get("disaster_stop", plan.get("disaster_stop", 0.0))
    targets = ov.get("targets", plan.get("targets", []))
    be_at_r = trade.get("breakeven_at_r", 1.0)
    st = trade.setdefault("state", {})
    for _k, _v in (("position", 0.0), ("avg", 0.0), ("realized_r", 0.0), ("status", "active"),
                   ("be_done", False), ("filled", []), ("hit", [])):
        st.setdefault(_k, _v)
    fut = [b for b in bars if int(b.get("bucket_ts", 0)) >= trade["since_ts"]]
    if st["status"] == "active":
        for b in fut:
            hi, lo, cl = float(b["high"]), float(b["low"]), float(b["close"])
            for i, e in enumerate(plan["entries"]):                  # fills (idempotent)
                if i in st["filled"]:
                    continue
                if (lo <= e["price"]) if sign > 0 else (hi >= e["price"]):
                    npos = st["position"] + e["size_frac"]
                    st["avg"] = (st["avg"] * st["position"] + e["price"] * e["size_frac"]) / npos
                    st["position"] = npos
                    st["filled"].append(i)
            if st["position"] <= 1e-9:
                continue
            if not st["be_done"] and (sign * (cl - st["avg"]) / risk >= be_at_r or st["hit"]):
                stop = st["avg"]                                     # breakeven (don't give back the win)
                ov["stop"] = stop
                st["be_done"] = True
            if disaster and ((lo <= disaster) if sign > 0 else (hi >= disaster)):
                st["realized_r"] += st["position"] * sign * (disaster - st["avg"]) / risk
                st["position"] = 0.0
                st["status"] = "disaster"
                break
            for j, t in enumerate(targets):                          # scale-out / final (idempotent)
                if j in st["hit"]:
                    continue
                if (hi >= t["price"]) if sign > 0 else (lo <= t["price"]):
                    take = min(t["size_frac"], st["position"])
                    st["realized_r"] += take * sign * (t["price"] - st["avg"]) / risk
                    st["position"] -= take
                    st["hit"].append(j)
            if st["position"] <= 1e-9:
                st["status"] = "target"
                break
            if (cl <= stop) if sign > 0 else (cl >= stop):           # close-confirmed stop
                st["realized_r"] += st["position"] * sign * (stop - st["avg"]) / risk
                st["position"] = 0.0
                st["status"] = "stopped"
                break
    mark = float(fut[-1]["close"]) if fut else st["avg"]
    st["unreal_r"] = round(st["position"] * sign * (mark - st["avg"]) / risk, 3) if st["position"] else 0.0
    st["total_r"] = round(st["realized_r"] + st["unreal_r"], 3)
    st["active_stop"] = round(stop, 2)
    st["mark"] = round(mark, 2)
    return st


def modify_trade(book_path, trade_id, *, stop=None, disaster_stop=None, targets=None,
                 breakeven_at_r=None, close=False) -> dict:
    """Quick TP/SL edit on an open trade (applied FORWARD). Move the stop (e.g. trail it to a big-TF
    level), retarget, change the breakeven trigger, or flatten now. Returns the updated trade."""
    book = load_book(book_path)
    for tr in book:
        if tr.get("id") == trade_id:
            ov = tr.setdefault("overrides", {})
            if stop is not None:
                ov["stop"] = float(stop)
                tr.setdefault("state", {})["be_done"] = True         # manual stop wins over auto-breakeven
            if disaster_stop is not None:
                ov["disaster_stop"] = float(disaster_stop)
            if targets is not None:
                ov["targets"] = targets
            if breakeven_at_r is not None:
                tr["breakeven_at_r"] = float(breakeven_at_r)
            if close:
                tr.setdefault("state", {})["status"] = "closed"
            _save_book(book_path, book)
            return tr
    raise KeyError(trade_id)


def evaluate_trade(trade: dict, bars) -> dict:
    """Recompute a managed trade's live status by replaying simulate_plan over the bars
    since it was opened. Returns the lifecycle dict (filled/exit_reason/realized_r/...)."""
    from quant.execution import plan_from_dict, simulate_plan

    fut = [b for b in bars if int(b.get("bucket_ts", 0)) >= trade["since_ts"]]
    if not fut:
        return {"filled": 0, "exit_reason": "pending", "realized_r": 0.0, "avg_entry": None, "bars_held": 0}
    plan = plan_from_dict(trade["plan"])
    return simulate_plan(plan, fut, fill_window=len(fut), max_hold=len(fut), stop_on_close=True,
                         reverse_choch_exit=False)


def entry_status(trade: dict, bars) -> list:
    """Per-entry FILLED/PENDING flags since open — a limit fills once price has touched it
    (long: a low ≤ price; short: a high ≥ price). Splits 持仓(filled) from 委托(pending)."""
    plan = trade["plan"]
    sign = 1 if plan["direction"] == "long" else -1
    fut = [b for b in bars if int(b.get("bucket_ts", 0)) >= trade["since_ts"]]
    out = []
    for e in plan.get("entries", []):
        filled = any((float(b["low"]) <= e["price"]) if sign > 0 else (float(b["high"]) >= e["price"])
                     for b in fut)
        out.append({"price": e["price"], "size_frac": e["size_frac"], "label": e["label"], "filled": filled})
    return out
