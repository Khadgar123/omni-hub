"""Event-loop backtester with research-to-live PARITY + no-look-ahead.

PARITY: drives the SAME ``gated_evaluate`` + ``sizing.size_qty`` the live/notify
path uses — a backtest describes the live system by construction.

NO LOOK-AHEAD (the freqtrade ``shift(1)`` discipline, reimplemented): an intent
computed from the CLOSE of bar *i* executes at the OPEN of bar *i+1*. You can
never act on a price you used to generate the signal in the same bar. Stops are
checked intrabar (conservative worst-case). Costs (fees + adverse slippage) hit
every fill.

``state_for(i)`` supplies the per-bar MarketState (regime). For unit tests it's
a constant stub; the real-data harness precomputes regime from resampled HTF.
A bounded ``window`` keeps it O(n·window) instead of O(n²).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from quant.backtest.costs import CostModel
from quant.strategy import sizing as sizing_mod
from quant.strategy.base import FLAT, LONG, gated_evaluate

WINDOW_BARS = 400  # enough history for any Phase-1 indicator (EMA50/ADX/BB)


@dataclass(slots=True)
class Trade:
    symbol: str
    entry_ts: int
    exit_ts: int
    entry: float
    exit: float
    qty: float
    fees: float
    pnl: float
    return_pct: float
    bars_held: int
    exit_reason: str
    entry_rationale: str = ""   # the StrategyIntent rationale that triggered the entry
                                # (so the chart can show WHY each trade fired)


@dataclass(slots=True)
class BacktestResult:
    strategy_id: str
    symbol: str
    equity0: float
    final_equity: float
    trades: list
    equity_curve: list  # list of (bucket_ts, equity)


def _always_tradeable(symbol):
    return lambda i: SimpleNamespace(
        symbol=symbol, regime_label="up", composite_bias="long", stand_down=False
    )


def run_backtest(strategy, bars, *, equity0=10000.0, cost=None, state_for=None,
                 edge_estimate=0.5, symbol=None, window=WINDOW_BARS):
    cost = cost or CostModel()
    symbol = symbol or getattr(strategy, "symbol", None) or "BTCUSDT"
    state_for = state_for or _always_tradeable(symbol)

    cash = equity0
    position = 0.0
    entry_fill = 0.0
    entry_fee = 0.0
    entry_ts = 0
    entry_i = 0
    stop = 0.0
    trail = 0.0       # >0 => trailing-stop distance below the running peak
    peak = 0.0        # highest high seen since entry, THROUGH THE PRIOR BAR (causal)
    entry_rationale = ""
    pending = None
    trades: list[Trade] = []
    curve: list[tuple[int, float]] = []

    def _close(exit_fill, ts, i, reason):
        nonlocal cash, position, stop, trail
        fee = cost.fee(position * exit_fill)
        cash += position * exit_fill - fee
        pnl = position * (exit_fill - entry_fill) - entry_fee - fee
        ret = pnl / (position * entry_fill) if entry_fill > 0 and position > 0 else 0.0
        trades.append(Trade(symbol, entry_ts, ts, entry_fill, exit_fill, position,
                            entry_fee + fee, pnl, ret, i - entry_i, reason, entry_rationale))
        position = 0.0
        stop = 0.0
        trail = 0.0

    for i, bar in enumerate(bars):
        op = float(bar["open"]); hi = float(bar["high"]); lo = float(bar["low"]); cl = float(bar["close"])
        ts = int(bar.get("bucket_ts", 0))

        # 1) execute the pending intent at THIS bar's OPEN (shift(1) parity)
        if pending is not None:
            if pending.direction == LONG and position <= 0:
                qty = sizing_mod.size_qty(equity=cash, entry=op, stop=pending.stop_price,
                                          conviction=pending.conviction, edge_estimate=edge_estimate)
                if qty > 0:
                    entry_fill = cost.fill_price(op, "buy")
                    entry_fee = cost.fee(qty * entry_fill)
                    cash -= qty * entry_fill + entry_fee
                    position = qty; entry_ts = ts; entry_i = i; stop = pending.stop_price
                    trail = getattr(pending, "trail_distance", 0.0) or 0.0
                    peak = entry_fill
                    entry_rationale = pending.rationale
            elif pending.direction == FLAT and position > 0:
                _close(cost.fill_price(op, "sell"), ts, i, "signal")
            pending = None

        # 2) ratchet the trailing stop UP from the peak through the prior bar (no
        #    look-ahead: this bar's high is folded into `peak` only at step 5).
        if position > 0 and trail > 0:
            stop = max(stop, peak - trail)

        # 3) intrabar stop (conservative): long stopped if the bar trades through it
        if position > 0 and stop > 0 and lo <= stop:
            _close(cost.fill_price(stop, "sell"), ts, i, "stop")

        # 4) compute intent from a bounded trailing window; queue for next bar's open
        w = bars[max(0, i - window + 1): i + 1]
        intent = gated_evaluate(strategy, w, state_for(i), position)
        if intent is not None:
            pending = intent

        # 5) mark-to-market at close; fold this bar's high into the running peak
        curve.append((ts, cash + position * cl))
        if position > 0 and hi > peak:
            peak = hi

    # flush: close any open position at the last close
    if position > 0 and bars:
        cl = float(bars[-1]["close"]); ts = int(bars[-1].get("bucket_ts", 0))
        _close(cost.fill_price(cl, "sell"), ts, len(bars) - 1, "eod")
        curve[-1] = (curve[-1][0], cash)

    return BacktestResult(getattr(strategy, "id", "?"), symbol, equity0, cash, trades, curve)
