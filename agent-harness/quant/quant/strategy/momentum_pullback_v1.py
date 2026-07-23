"""momentum_pullback_v1 — the synthesis of the whole investigation.

Everything we established, encoded:
  * DIRECTION is the edge (not discrimination): trade WITH the trend. Multi-level
    sets it — the 1d regime (state.regime_label, denoised HTF) must not oppose, AND
    the 4h EMA20/50 must agree. (Multi-level used for DIRECTION, where it helps.)
  * ENTRY = "continuation after a pullback" (the only context that tested +EV):
    in an uptrend, buy the RECLAIM of EMA20 after a dip (re-acceleration), not a
    coil breakout (false at low TF) and not a climax fade (0 EV).
  * DISCRIMINATION is NOT relied on: bad entry timing only costs efficiency; the
    structural stop caps the loss. The 0.59 reversal-wall doesn't threaten us.
  * EXIT = trend-following convexity: a wide chandelier trail lets winners run
    (positive skew), exit on 4h trend flip. Small fixed loss, open upside.
  * Decision TF = 4h (moves dwarf cost); symmetric long/short.

Two free params (stop_buf_atr, trail_atr); the rest are fixed standard windows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant import features as F
from quant.strategy.base import FLAT, LONG, SHORT, StrategyIntent

EMA_FAST = 20
EMA_SLOW = 50
SWING_LB = 10        # structural stop = recent swing extreme
ATR_LEN = 14
ADX_MIN = 20         # only take CONTINUATION in a confirmed trend (cut chop whipsaw)


@dataclass(slots=True)
class MomentumPullbackV1:
    id: str = "momentum_pullback_v1"
    timeframe: str = "4h"
    stop_buf_atr: float = 0.5     # stop this far beyond the swing extreme
    trail_atr: float = 3.0        # chandelier trail — wide, to ride trends (convexity)
    eligible_regimes: frozenset = field(
        default_factory=lambda: frozenset(
            {"range", "up", "down", "strong_up", "strong_down"}))
    requires_bias: str | None = None

    def evaluate(self, bars, state, position_qty):
        if len(bars) < 60:
            return None
        cl = F.closes(bars); c = cl[-1]; ts = int(bars[-1].get("bucket_ts", 0))
        ef = F.ema(cl, EMA_FAST); es = F.ema(cl, EMA_SLOW)
        if ef[-1] is None or es[-1] is None or ef[-2] is None:
            return None
        atr = F.last_valid(F.atr(bars, ATR_LEN))
        if not atr or atr <= 0:
            return None
        up_trend = ef[-1] > es[-1]
        dn_trend = ef[-1] < es[-1]

        # ---- EXIT: trend flip ends the ride; else the engine's wide trail banks it ----
        if position_qty > 0:
            if dn_trend:
                return self._i(state, ts, FLAT, 0.0, c, 0.0, 0.0, "exit long: 4h trend flip")
            return None
        if position_qty < 0:
            if up_trend:
                return self._i(state, ts, FLAT, 0.0, c, 0.0, 0.0, "exit short: 4h trend flip")
            return None

        # ---- ENTRY: continuation after a pullback, ALIGNED with the 1d direction ----
        adx = F.last_valid(F.adx(bars, 14)["adx"])
        if adx is not None and adx < ADX_MIN:        # chop -> skip (the solvable whipsaw)
            return None
        rl = state.regime_label
        big_not_down = rl in ("up", "strong_up", "range")
        big_not_up = rl in ("down", "strong_down", "range")
        reclaim_up = cl[-2] <= ef[-2] and c > ef[-1]      # reclaimed EMA20 from below = dip's bounce
        lose_down = cl[-2] >= ef[-2] and c < ef[-1]
        los = F.lows(bars); his = F.highs(bars)
        if big_not_down and up_trend and reclaim_up:
            stop = min(los[-SWING_LB:]) - self.stop_buf_atr * atr
            if stop < c:
                return self._i(state, ts, LONG, 0.7, c, stop, self.trail_atr * atr,
                               f"pullback reclaim EMA20 in uptrend (1d={rl})")
        if big_not_up and dn_trend and lose_down:
            stop = max(his[-SWING_LB:]) + self.stop_buf_atr * atr
            if stop > c:
                return self._i(state, ts, SHORT, 0.7, c, stop, self.trail_atr * atr,
                               f"pullback lose EMA20 in downtrend (1d={rl})")
        return None

    def _i(self, state, ts, direction, conv, ref, stop, trail, rationale):
        return StrategyIntent(self.id, state.symbol, self.timeframe, ts, direction, conv,
                              ref, stop, state.regime_label, rationale, {}, trail)
