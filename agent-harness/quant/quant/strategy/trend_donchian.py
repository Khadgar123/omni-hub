"""trend_donchian_v1 — long-only Donchian breakout trend-follower (Phase-1).

Fires only in trend regimes (the runner gates this). Enter long on a breakout
above the prior ``entry_lookback`` highs; exit on a break below the prior
``exit_lookback`` lows (faster channel: let winners run, cut quickly). The
protective stop is ATR-based. Exits also come from the backtester/live stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant import features
from quant.strategy.base import FLAT, LONG, StrategyIntent


@dataclass(slots=True)
class TrendDonchian:
    id: str = "trend_donchian_v1"
    timeframe: str = "1h"
    entry_lookback: int = 20
    exit_lookback: int = 10
    atr_len: int = 14
    atr_mult: float = 2.0
    eligible_regimes: frozenset = field(default_factory=lambda: frozenset({"up", "strong_up"}))
    requires_bias: str | None = LONG

    def evaluate(self, bars, state, position_qty):
        need = max(self.entry_lookback, self.exit_lookback, self.atr_len) + 1
        if len(bars) < need:
            return None
        closes = features.closes(bars)
        highs = features.highs(bars)
        lows = features.lows(bars)
        atr = features.last_valid(features.atr(bars, self.atr_len))
        c = closes[-1]
        ts = int(bars[-1].get("bucket_ts", 0))

        if position_qty <= 0:
            upper = max(highs[-self.entry_lookback - 1:-1])  # prior N highs, excl. current
            if atr and c > upper:
                conv = 0.9 if state.regime_label == "strong_up" else 0.6
                return StrategyIntent(
                    self.id, state.symbol, self.timeframe, ts, LONG, conv,
                    c, c - self.atr_mult * atr, state.regime_label,
                    f"donchian breakout: close {c:.2f} > {self.entry_lookback}-high {upper:.2f}",
                    {"atr": atr, "upper": upper},
                )
            return None

        lower = min(lows[-self.exit_lookback - 1:-1])
        if c < lower:
            return StrategyIntent(
                self.id, state.symbol, self.timeframe, ts, FLAT, 1.0,
                c, 0.0, state.regime_label,
                f"donchian exit: close {c:.2f} < {self.exit_lookback}-low {lower:.2f}", {},
            )
        return None
