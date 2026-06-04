"""phase_trend_v1 — the PHASE-CONDITIONAL strategy the data + research both point to.

The empirical phase-edge test (full 2024-26) showed the phase-appropriate action's
forward edge is ~0 at 5m but 0.3–4% (>> the 0.24% round-trip cost) at 4h/1d, and at
those timeframes BTC is MOMENTUM/breakout-driven (Moskowitz-Ooi-Pedersen time-series
momentum; Wood-Roberts-Zohren regime conditioning). So this trades the HOLDING
timeframe (4h default) where the move dwarfs cost, and the action is set by the phase:

  * trend_up   → FOLLOW long ;  trend_down → FOLLOW short  (ride with a trailing stop)
  * coil       → BREAKOUT-follow: go with a close beyond the recent range (the spring)
  * chop / mid → STAND ASIDE  (the phases with no cost-surviving edge)
  * exit when the phase flips to the OPPOSITE trend, else let the trail ride it.

This is the user's "大级别定方向": the phase (on the holding TF) decides direction;
a 1m entry refinement is future work ("小级别找买卖点"). Symmetric long/short.
Two free params (``stop_atr``, ``trail_atr``); phase thresholds are fixed constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant import features as F
from quant import phase as P
from quant.strategy.base import FLAT, LONG, SHORT, StrategyIntent

TREND_ER = 0.35       # ER ≥ this = trend (fixed; calibrate by walk-forward, not per-run)
RANGE_ER = 0.22       # ER < this = range
BREAKOUT_N = 20       # coil breakout lookback (bars)
ATR_LEN = 14


@dataclass(slots=True)
class PhaseTrendV1:
    id: str = "phase_trend_v1"
    timeframe: str = "4h"            # HOLDING/decision TF — where the move dwarfs cost
    stop_atr: float = 1.5            # initial protective stop (ATR)
    trail_atr: float = 3.0           # trailing distance (ATR) — ride the trend
    eligible_regimes: frozenset = field(
        default_factory=lambda: frozenset(
            {"range", "up", "down", "strong_up", "strong_down"}))  # phase (own bars) is the gate
    requires_bias: str | None = None

    def evaluate(self, bars, state, position_qty):
        if len(bars) < 60:
            return None
        c = float(bars[-1]["close"])
        ts = int(bars[-1].get("bucket_ts", 0))
        atr = F.last_valid(F.atr(bars, ATR_LEN))
        if not atr or atr <= 0:
            return None
        ph = P.latest(bars, trend_er=TREND_ER, range_er=RANGE_ER)["phase"]

        # ---- EXITS: bail only when the phase flips to the OPPOSITE trend; else the
        #      engine's trailing stop rides/banks the move. ----
        if position_qty > 0:
            if ph == "trend_down":
                return self._i(state, ts, FLAT, 0.0, c, 0.0, 0.0, "exit long: phase->trend_down")
            return None
        if position_qty < 0:
            if ph == "trend_up":
                return self._i(state, ts, FLAT, 0.0, c, 0.0, 0.0, "exit short: phase->trend_up")
            return None

        # ---- ENTRIES: follow the trend; follow a coil breakout; else stand aside. ----
        if ph == "trend_up":
            return self._i(state, ts, LONG, 0.7, c, c - self.stop_atr * atr, self.trail_atr * atr,
                           "follow trend_up")
        if ph == "trend_down":
            return self._i(state, ts, SHORT, 0.7, c, c + self.stop_atr * atr, self.trail_atr * atr,
                           "follow trend_down")
        if ph == "coil":
            his = F.highs(bars); los = F.lows(bars)
            hh = max(his[-BREAKOUT_N - 1:-1]); ll = min(los[-BREAKOUT_N - 1:-1])
            if c > hh:
                return self._i(state, ts, LONG, 0.6, c, c - self.stop_atr * atr, self.trail_atr * atr,
                               f"coil breakout ↑ {hh:.0f}")
            if c < ll:
                return self._i(state, ts, SHORT, 0.6, c, c + self.stop_atr * atr, self.trail_atr * atr,
                               f"coil breakout ↓ {ll:.0f}")
        return None

    def _i(self, state, ts, direction, conv, ref, stop, trail, rationale):
        return StrategyIntent(self.id, state.symbol, self.timeframe, ts, direction, conv,
                              ref, stop, state.regime_label, rationale, {}, trail)
