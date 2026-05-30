"""squeeze_breakout_v1 — volatility-squeeze breakout (缠论 中枢突破 analogue).

A narrow Bollinger-band-width period is a consolidation/中枢 (low realized range);
a break above the recent high out of that squeeze is the breakout. Enter long when
the PRIOR bar was in a width-squeeze (bottom ``squeeze_pctl`` of the trailing
width distribution) AND price breaks the ``breakout_lookback`` high; exit on a
break below the ``exit_lookback`` low. Eligible range/up, non-directional entry
(catches the breakout before the bias fully flips), long-only spot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant import features
from quant.strategy.base import FLAT, LONG, StrategyIntent


@dataclass(slots=True)
class SqueezeBreakout:
    id: str = "squeeze_breakout_v1"
    timeframe: str = "1h"
    bb_len: int = 20
    bb_k: float = 2.0
    width_window: int = 120
    squeeze_pctl: float = 0.25
    breakout_lookback: int = 20
    exit_lookback: int = 10
    atr_len: int = 14
    atr_mult: float = 2.5
    eligible_regimes: frozenset = field(
        default_factory=lambda: frozenset({"range", "up", "strong_up"}))
    requires_bias: str | None = None

    def evaluate(self, bars, state, position_qty):
        need = max(self.bb_len, self.width_window, self.breakout_lookback, self.atr_len) + 2
        if len(bars) < need:
            return None
        closes = features.closes(bars)
        highs = features.highs(bars)
        lows = features.lows(bars)
        bb = features.bollinger(closes, self.bb_len, self.bb_k)
        width = [((u - l) / m) if (u is not None and l is not None and m) else None
                 for u, l, m in zip(bb["upper"], bb["lower"], bb["mid"])]
        c = closes[-1]
        ts = int(bars[-1].get("bucket_ts", 0))

        if position_qty <= 0:
            atr = features.last_valid(features.atr(bars, self.atr_len))
            hist = [w for w in width[-self.width_window - 1:-1] if w is not None]
            prior_w = width[-2]
            if atr and prior_w is not None and len(hist) >= 10:
                thr = sorted(hist)[int(len(hist) * self.squeeze_pctl)]
                upper_break = max(highs[-self.breakout_lookback - 1:-1])
                if prior_w <= thr and c > upper_break:
                    return StrategyIntent(self.id, state.symbol, self.timeframe, ts, LONG, 0.7,
                                          c, c - self.atr_mult * atr, state.regime_label,
                                          f"squeeze breakout: width<{thr:.4f}, close>{upper_break:.2f}",
                                          {"prior_width": prior_w, "thr": thr})
            return None

        lower = min(lows[-self.exit_lookback - 1:-1])
        if c < lower:
            return StrategyIntent(self.id, state.symbol, self.timeframe, ts, FLAT, 1.0,
                                  c, 0.0, state.regime_label, f"squeeze exit: close<{lower:.2f}", {})
        return None
