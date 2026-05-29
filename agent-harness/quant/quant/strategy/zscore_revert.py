"""zscore_revert_v1 — z-score mean reversion (cleaner than Bollinger+RSI).

Long when price is ``z_entry`` standard deviations below its rolling mean; exit
when it reverts to the mean (z>=0). Range-regime-gated, long-only spot. The
ATR stop is tight (range trades are wrong fast).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from quant import features
from quant.strategy.base import FLAT, LONG, StrategyIntent


@dataclass(slots=True)
class ZScoreRevert:
    id: str = "zscore_revert_v1"
    timeframe: str = "1h"
    lookback: int = 48           # 2 days on 1h
    z_entry: float = -2.0
    atr_len: int = 14
    atr_mult: float = 1.5
    eligible_regimes: frozenset = field(default_factory=lambda: frozenset({"range"}))
    requires_bias: str | None = None

    def evaluate(self, bars, state, position_qty):
        need = max(self.lookback, self.atr_len) + 1
        if len(bars) < need:
            return None
        closes = features.closes(bars)
        window = closes[-self.lookback:]
        mean = statistics.fmean(window)
        sd = statistics.pstdev(window)
        if sd <= 0:
            return None
        c = closes[-1]
        z = (c - mean) / sd
        ts = int(bars[-1].get("bucket_ts", 0))
        if position_qty <= 0:
            atr = features.last_valid(features.atr(bars, self.atr_len))
            if atr and z <= self.z_entry:
                conv = max(0.0, min(1.0, -z / 3.0))
                return StrategyIntent(self.id, state.symbol, self.timeframe, ts, LONG, conv,
                                      c, c - self.atr_mult * atr, state.regime_label,
                                      f"zscore revert: z={z:.2f}", {"z": z, "mean": mean})
            return None
        if z >= 0.0:  # reverted to mean -> exit
            return StrategyIntent(self.id, state.symbol, self.timeframe, ts, FLAT, 1.0,
                                  c, 0.0, state.regime_label, f"zscore exit: z={z:.2f}", {})
        return None
