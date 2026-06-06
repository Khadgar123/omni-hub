"""range_bb_revert_v1 — long-only Bollinger/RSI mean-reversion (Phase-1).

Fires only in range regimes (the runner gates this). Buy the dip: enter long
when close is below the lower Bollinger band AND RSI is oversold; exit when price
reverts to the mid band (SMA). ATR stop (tighter than the trend strategy —
range trades are wrong fast).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant import features
from quant.strategy.base import FLAT, LONG, StrategyIntent


@dataclass(slots=True)
class RangeBBRevert:
    id: str = "range_bb_revert_v1"
    timeframe: str = "1h"
    bb_len: int = 20
    bb_k: float = 2.0
    rsi_len: int = 14
    rsi_floor: float = 30.0
    atr_len: int = 14
    atr_mult: float = 1.5
    eligible_regimes: frozenset = field(default_factory=lambda: frozenset({"range"}))
    requires_bias: str | None = None  # range trades are non-directional (bias is flat)

    def evaluate(self, bars, state, position_qty):
        need = max(self.bb_len, self.rsi_len, self.atr_len) + 1
        if len(bars) < need:
            return None
        closes = features.closes(bars)
        bb = features.bollinger(closes, self.bb_len, self.bb_k)
        mid = bb["mid"][-1]
        lower = bb["lower"][-1]
        c = closes[-1]
        ts = int(bars[-1].get("bucket_ts", 0))

        if position_qty <= 0:
            rsi = features.last_valid(features.rsi(closes, self.rsi_len))
            atr = features.last_valid(features.atr(bars, self.atr_len))
            if lower is not None and atr and rsi is not None and c < lower and rsi < self.rsi_floor:
                conv = max(0.0, min(1.0, (self.rsi_floor - rsi) / self.rsi_floor))
                return StrategyIntent(
                    self.id, state.symbol, self.timeframe, ts, LONG, conv,
                    c, c - self.atr_mult * atr, state.regime_label,
                    f"bb revert: close {c:.2f} < lower {lower:.2f}, rsi {rsi:.0f}",
                    {"rsi": rsi, "lower": lower},
                )
            return None

        if mid is not None and c >= mid:
            return StrategyIntent(
                self.id, state.symbol, self.timeframe, ts, FLAT, 1.0,
                c, 0.0, state.regime_label,
                f"bb revert exit: close {c:.2f} >= mid {mid:.2f}", {},
            )
        return None
