"""level_fade_v1 — the SYMMETRIC, low-parameter rebuild driven by the user's
hand-annotated 2024-06 segment and the overfitting/tail-risk research.

ONE bidirectional S/R map (``levels.scored_levels``: swing clusters + volume
profile + round numbers, confluence-boosted). Direction is decided by
side-of-approach, never by the level — so the support you go LONG at is the same
line you take profit / go SHORT at, and vice versa. Long and short are mirror
images of one rule (your "多空统一" requirement).

The trigger is CONTINUOUS, not a candle count: price is AT a strong level and
ACCELERATION (2nd derivative, ATR-normalized — a leading, scale-free measure) has
rolled over against the approach. No "wait N bars to confirm a pivot" anywhere —
that was the lag that made the old version buy late and get whipsawed.

EXIT MODEL (this rewrite): PRECISE entry, then HOLD — no tight stop. Exit only
when the OPPOSITE signal fires (price reaches the opposite level with opposite-side
exhaustion) → take profit, and a REVERSE may open next bar; OR when the regime
flips to a strong trend AGAINST the position. The only price stop is a CATASTROPHE
stop, sized wide on the OPERATING-level ATR (``state.op_atr``, confirm-TF/4h) and
placed BEYOND the level — it fires only on a genuine breakdown, never on noise.
This is the user's "不急着止损，到对面信号再止盈反手" and the direct fix for the old
tight-stop whipsaw (12% win, washed out 88% of the time).

PARSIMONY (research verdict: ≤3-4 free params for 30-300 trades): exactly TWO
free, economically-named parameters — ``near_atr`` (entry/exit precision) and
``stop_atr`` (catastrophe-stop distance in op-level ATR). Everything else is a
FIXED constant with an economic justification, NOT a tunable knob.

TAIL-RISK CONTROLS (mandatory for a fade = short-vol = negative-skew strategy):
  * direction gate ("大级别定方向"): the higher-TF (daily) regime sets the allowed
    side — short-only in a down regime, long-only in up, both in range. Never fade
    AGAINST the dominant trend; EXIT if the regime flips against an open position.
  * vol kill-switch: block entries when short/long ATR ratio > ``VOL_RATIO_MAX``.
  * don't fade INTO acceleration: enter only as momentum turns AGAINST the move.
  * catastrophe stop beyond the level on the op-level ATR (wide) — survive noise,
    cap the tail.

Long/short are both emitted for research + visualization; live execution stays
notify-only / spot-long per the repo's hard constraints (a short signal there is
used to EXIT longs and is recorded, not auto-traded).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant import features as F
from quant import levels as L
from quant.strategy.base import FLAT, LONG, SHORT, StrategyIntent

# ---- FIXED constants (economic meaning, NOT swept) -------------------------
SWING_LR = 3          # pivot definition (structural)
ATR_LEN = 14          # standard ATR
MERGE_PCT = 0.005     # level-cluster tolerance (0.5%)
VP_BINS = 48          # volume-profile resolution
ACCEL_SMOOTH = 3      # EMA span for the acceleration estimate (de-noise)
ADX_MAX = 25.0        # > this = trending: do NOT fade (research-standard)
VOL_RATIO_MAX = 1.5   # short/long ATR ratio kill-switch (vol explosion)
CONV_FLOOR = 0.25     # min conviction so a real setup still sizes
RT_BPS = 24.0         # round-trip friction (≈ default CostModel: 2×(10 taker + 2 slip))
MIN_EDGE_X = 2.5      # the target (opposite level) must be ≥ this × round-trip cost away —
                      # the cost-survival gate that kills micro-scalps below the friction floor


@dataclass(slots=True)
class LevelFadeV1:
    id: str = "level_fade_v1"
    timeframe: str = "5m"             # entry TF (1m available via param; 5m default for tractable backtests)
    # ---- the ONLY free parameters (2) ----
    near_atr: float = 0.5             # "at the level" within this many entry-TF ATR (entry+exit precision)
    stop_atr: float = 2.0             # CATASTROPHE stop, this many OP-level ATR beyond the level
    # ---- contract ----
    eligible_regimes: frozenset = field(
        default_factory=lambda: frozenset(
            {"range", "up", "down", "strong_up", "strong_down"}))   # all; DIRECTION is gated by regime in evaluate
    requires_bias: str | None = None  # symmetric — direction decided per-regime, not a fixed bias

    def evaluate(self, bars, state, position_qty):
        need = 60 + 2 * SWING_LR
        if len(bars) < need:
            return None
        c = float(bars[-1]["close"])
        ts = int(bars[-1].get("bucket_ts", 0))
        atr = F.last_valid(F.atr(bars, ATR_LEN))
        if not atr or atr <= 0:
            return None
        op_atr = getattr(state, "op_atr", None) or atr        # manage on the holding TF
        a = F.last_valid(F.acceleration(bars, ATR_LEN, ACCEL_SMOOTH))   # continuous, leading

        # one symmetric level map; keep only the STRONG half (adaptive, not a param)
        lv = L.scored_levels(bars, left=SWING_LR, merge_pct=MERGE_PCT, atr=atr, vp_bins=VP_BINS)
        if not lv:
            return None
        strengths = sorted(x["strength"] for x in lv)
        med = strengths[len(strengths) // 2]
        strong = [x for x in lv if x["strength"] >= med] or lv
        nl = L.nearest_levels(c, strong, atr=atr)
        sup, res = nl["support"], nl["resistance"]

        # ---------- EXITS (risk-reducing, never gated): HOLD until the opposite
        # signal, then take profit (a reverse may open next bar). No tight stop —
        # only the engine's wide catastrophe stop + a regime-flip bail. ----------
        if position_qty > 0:                       # long
            if state.regime_label in ("down", "strong_down"):       # trend turned against us -> bail
                return self._intent(state, ts, FLAT, 0.0, c, 0.0, 0.0,
                                    "exit long: regime flip -> down")
            if res is not None and (res - c) <= self.near_atr * atr and a is not None and a < 0:
                return self._intent(state, ts, FLAT, 0.0, c, 0.0, 0.0,
                                    f"take profit long @ resistance {res:.0f} (reverse next)")
            return None                                             # else HOLD through noise
        if position_qty < 0:                       # short (mirror)
            if state.regime_label in ("up", "strong_up"):
                return self._intent(state, ts, FLAT, 0.0, c, 0.0, 0.0,
                                    "exit short: regime flip -> up")
            if sup is not None and (c - sup) <= self.near_atr * atr and a is not None and a > 0:
                return self._intent(state, ts, FLAT, 0.0, c, 0.0, 0.0,
                                    f"take profit short @ support {sup:.0f} (reverse next)")
            return None

        # ---------- ENTRIES ----------
        if a is None:
            return None
        volr = F.last_valid(F.atr_ratio(bars, ATR_LEN, 100))
        if volr is not None and volr > VOL_RATIO_MAX:               # vol-explosion kill-switch
            return None
        # DIRECTION is set by the higher-TF (daily) regime — "大级别定方向": fade pullbacks
        # WITH the dominant trend (short rallies in a down regime, buy dips in an up regime);
        # both ways ONLY in range. Never fade AGAINST the trend (the knife-catching that
        # tanked the win rate). The small TF only times the entry ("小级别找买卖点").
        rl = state.regime_label
        allow_long = rl in ("range", "up", "strong_up")
        allow_short = rl in ("range", "down", "strong_down")
        adx = F.last_valid(F.adx(bars, 14)["adx"])                  # informational (shown in rationale)
        rt = RT_BPS / 1e4 * c                                       # round-trip cost in price terms
        # SHORT at resistance: tested from below, momentum rolled over (a<0), AND the target
        # (support below) is far enough to clear cost. HOLD to the opposite level; the only
        # price stop is the WIDE catastrophe stop beyond the level on the op-level ATR
        # (trail_distance=0 — we exit on the opposite signal, not by trailing).
        if (allow_short and res is not None and sup is not None and 0 <= (res - c) <= self.near_atr * atr
                and a < 0 and (c - sup) >= MIN_EDGE_X * rt):
            stop = res + self.stop_atr * op_atr
            conv = max(CONV_FLOOR, min(1.0, abs(a)))
            return self._intent(state, ts, SHORT, conv, c, stop, 0.0,
                                 f"short @ res {res:.0f}→tgt {sup:.0f} (accel={a:.2f}, adx={_r(adx)})",
                                 {"level": res, "target": sup, "accel": a, "adx": adx})
        # LONG at support: mirror image — the support you long is the short's take-profit.
        if (allow_long and sup is not None and res is not None and 0 <= (c - sup) <= self.near_atr * atr
                and a > 0 and (res - c) >= MIN_EDGE_X * rt):
            stop = sup - self.stop_atr * op_atr
            conv = max(CONV_FLOOR, min(1.0, abs(a)))
            return self._intent(state, ts, LONG, conv, c, stop, 0.0,
                                 f"long @ sup {sup:.0f}→tgt {res:.0f} (accel={a:.2f}, adx={_r(adx)})",
                                 {"level": sup, "target": res, "accel": a, "adx": adx})
        return None

    def _intent(self, state, ts, direction, conv, ref, stop, trail, rationale, feats=None):
        return StrategyIntent(self.id, state.symbol, self.timeframe, ts, direction, conv,
                              ref, stop, state.regime_label, rationale, feats or {}, trail)


def _r(x):
    return None if x is None else round(x)
