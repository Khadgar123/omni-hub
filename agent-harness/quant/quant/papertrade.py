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
