"""tsmom_v1 — time-series momentum (Moskowitz/Ooi/Pedersen, crypto-adapted).

Research #1 trend finding for crypto: a SHORT lookback works best. Target long
while the trailing return over ``lookback`` bars is positive; flat when it turns
negative. Vol-scaled conviction. Long-only spot, trend-regime-gated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant import features
from quant.strategy.base import FLAT, LONG, StrategyIntent


@dataclass(slots=True)
class TSMomentum:
    id: str = "tsmom_v1"
    timeframe: str = "1h"
    lookback: int = 240          # ~10 days on 1h (short, per crypto research)
    atr_len: int = 14
    atr_mult: float = 3.0
    eligible_regimes: frozenset = field(default_factory=lambda: frozenset({"up", "strong_up"}))
    requires_bias: str | None = LONG

    def evaluate(self, bars, state, position_qty):
        need = max(self.lookback, self.atr_len) + 1
        if len(bars) < need:
            return None
        closes = features.closes(bars)
        c = closes[-1]
        past = closes[-1 - self.lookback]
        if past <= 0:
            return None
        roc = c / past - 1.0
        atr = features.last_valid(features.atr(bars, self.atr_len))
        ts = int(bars[-1].get("bucket_ts", 0))
        if position_qty <= 0:
            if atr and roc > 0:
                conv = max(0.0, min(1.0, roc / 0.10))  # 10% over lookback -> full conviction
                return StrategyIntent(self.id, state.symbol, self.timeframe, ts, LONG, conv,
                                      c, c - self.atr_mult * atr, state.regime_label,
                                      f"tsmom: {self.lookback}-bar ROC {roc:+.2%}", {"roc": roc})
            return None
        if roc <= 0:  # momentum turned -> exit
            return StrategyIntent(self.id, state.symbol, self.timeframe, ts, FLAT, 1.0,
                                  c, 0.0, state.regime_label, f"tsmom exit: ROC {roc:+.2%}", {})
        return None
