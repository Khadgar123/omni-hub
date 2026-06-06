"""divergence_reversal_v1 — 缠论 背驰 / momentum-exhaustion reversal, quantified.

The "加速到极致后的反弹": price makes a LOWER LOW while momentum (RSI) makes a
HIGHER LOW => bullish divergence => the down-move is exhausting. This is 缠论's
背驰 translated into a falsifiable, backtestable feature — NOT trusted as
certainty: its conditional edge is measured by the backtest and judged by the
validation moat (DSR/PBO).

Detection (window split into prior/recent halves): compare the price low + RSI
at each half's trough; long when price_low(recent) < price_low(prior) AND
rsi(recent) > rsi(prior) AND price is still near the recent low (fresh). Exit
when RSI recovers above ``exit_rsi`` (reversal played out) or the ATR stop hits.
Eligible in down/range regimes, non-directional (counter-trend), long-only spot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant import features
from quant.strategy.base import FLAT, LONG, StrategyIntent


@dataclass(slots=True)
class DivergenceReversal:
    id: str = "divergence_reversal_v1"
    timeframe: str = "1h"
    window: int = 60             # lookback to find the two troughs
    rsi_len: int = 14
    atr_len: int = 14
    atr_mult: float = 2.0
    near_low_frac: float = 0.01  # "fresh": close within 1% of the recent trough
    exit_rsi: float = 55.0
    eligible_regimes: frozenset = field(
        default_factory=lambda: frozenset({"down", "strong_down", "range"}))
    requires_bias: str | None = None

    def evaluate(self, bars, state, position_qty):
        need = self.window + self.rsi_len + 1
        if len(bars) < need:
            return None
        closes = features.closes(bars)
        rsi_s = features.rsi(closes, self.rsi_len)
        c = closes[-1]
        ts = int(bars[-1].get("bucket_ts", 0))

        if position_qty > 0:
            rnow = features.last_valid(rsi_s)
            if rnow is not None and rnow >= self.exit_rsi:  # reversal recovered -> take it
                return StrategyIntent(self.id, state.symbol, self.timeframe, ts, FLAT, 1.0,
                                      c, 0.0, state.regime_label, f"divergence exit: rsi {rnow:.0f}", {})
            return None

        # find the trough of each half of the trailing window
        w = closes[-self.window:]
        r = rsi_s[-self.window:]
        half = self.window // 2
        pi = min(range(half), key=lambda i: w[i])                       # prior-half low idx
        ri = half + min(range(self.window - half), key=lambda i: w[half + i])  # recent-half low idx
        if r[pi] is None or r[ri] is None:
            return None
        price_lower_low = w[ri] < w[pi]
        rsi_higher_low = r[ri] > r[pi]
        fresh = c <= w[ri] * (1.0 + self.near_low_frac)
        atr = features.last_valid(features.atr(bars, self.atr_len))
        if atr and price_lower_low and rsi_higher_low and fresh:
            conv = max(0.0, min(1.0, (r[ri] - r[pi]) / 20.0))
            return StrategyIntent(self.id, state.symbol, self.timeframe, ts, LONG, conv,
                                  c, c - self.atr_mult * atr, state.regime_label,
                                  f"背驰 bullish divergence: price LL, rsi {r[pi]:.0f}->{r[ri]:.0f}",
                                  {"rsi_prior": r[pi], "rsi_recent": r[ri]})
        return None
