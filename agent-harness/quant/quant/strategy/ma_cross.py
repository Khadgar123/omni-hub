"""ma_cross_v1 — EMA fast/slow crossover trend-follower (nautilus/hummingbot ref).

Target long while EMA(fast) > EMA(slow); flat on the down-cross. Trend-regime-
gated, long-only spot, ATR stop. A distinct trend mechanism from Donchian
(breakout) and tsmom (rate-of-change).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant import features
from quant.strategy.base import FLAT, LONG, StrategyIntent


@dataclass(slots=True)
class MACross:
    id: str = "ma_cross_v1"
    timeframe: str = "1h"
    fast: int = 50
    slow: int = 200
    atr_len: int = 14
    atr_mult: float = 3.0
    eligible_regimes: frozenset = field(default_factory=lambda: frozenset({"up", "strong_up"}))
    requires_bias: str | None = LONG

    def evaluate(self, bars, state, position_qty):
        if len(bars) < self.slow + 1:
            return None
        closes = features.closes(bars)
        ef = features.last_valid(features.ema(closes, self.fast))
        es = features.last_valid(features.ema(closes, self.slow))
        if ef is None or es is None:
            return None
        c = closes[-1]
        ts = int(bars[-1].get("bucket_ts", 0))
        if position_qty <= 0:
            atr = features.last_valid(features.atr(bars, self.atr_len))
            if atr and ef > es:
                conv = max(0.0, min(1.0, (ef / es - 1.0) / 0.05))
                return StrategyIntent(self.id, state.symbol, self.timeframe, ts, LONG, conv,
                                      c, c - self.atr_mult * atr, state.regime_label,
                                      f"ma cross: ema{self.fast}>ema{self.slow}", {"ef": ef, "es": es})
            return None
        if ef < es:
            return StrategyIntent(self.id, state.symbol, self.timeframe, ts, FLAT, 1.0,
                                  c, 0.0, state.regime_label, f"ma cross exit: ema{self.fast}<ema{self.slow}", {})
        return None
