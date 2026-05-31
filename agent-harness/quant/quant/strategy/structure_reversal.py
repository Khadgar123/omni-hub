"""structure_reversal_v1 — the composed structure-driven candidate.

Assembles the whole stack into one falsifiable long-only spot reversal:

  1. CONTEXT (横盘是趋势的酝酿): only act when ranging/compressed — choppiness ≥
     ``chop_min`` (vol-compression is where bounces, not trend-continuation, pay).
  2. STRUCTURE + 背驰 timing (向下下不去就要上): a DOWN-leg divergence — price made a
     LOWER low on WEAKER force (``structure.divergence`` dir="down") = the down-move
     is exhausting = bullish reversal. Fresh (within the last few bars).
  3. S/R (Q5): the signal must fire AT a swing-support level (within ``near_atr``·ATR),
     and the reward:risk by levels (next resistance vs that support) must clear ``min_rr``.
  4. RISK: stop just BELOW the support (``stop_atr``·ATR); the engine's ``size_qty``
     turns the stop distance + conviction into position size. Exit at the next
     resistance (target) or on an UP-leg divergence (the bounce itself exhausts).

Eligible only in down / range regimes (counter-trend); the regime gate is enforced
by ``gated_evaluate``, not here. This is a HYPOTHESIS to be judged by the backtest +
the forward/paper gate (DSR/PBO + frozen-config OOS), NOT an asserted edge.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant import features as F
from quant import levels as L
from quant import structure as S
from quant.strategy.base import FLAT, LONG, StrategyIntent


@dataclass(slots=True)
class StructureReversal:
    id: str = "structure_reversal_v1"
    timeframe: str = "1h"
    swing_lr: int = 3
    atr_len: int = 14
    div_ratio: float = 0.9        # 背驰 threshold (out/in metric)
    near_atr: float = 1.0         # "at support": within this many ATR
    min_rr: float = 1.5           # reward:risk by levels gate
    stop_atr: float = 1.0         # initial stop below support by this many ATR
    trail_atr: float = 3.0        # Chandelier trailing-stop distance (ATR) — banks the move
    fresh_bars: int = 3           # divergence must be within the last N legs-ends
    require_range: bool = True
    chop_min: float = 55.0
    merge_pct: float = 0.004
    require_nested: bool = False   # 区间套: also require a same-direction sub-level 背驰
                                   # (state.sub_div, supplied by harness.run(sub=...))
    eligible_regimes: frozenset = field(
        default_factory=lambda: frozenset({"down", "strong_down", "range"}))
    requires_bias: str | None = None

    def evaluate(self, bars, state, position_qty):
        need = 60 + 2 * self.swing_lr
        if len(bars) < need:
            return None
        cl = F.closes(bars)
        c = cl[-1]
        ts = int(bars[-1].get("bucket_ts", 0))
        atr = F.last_valid(F.atr(bars, self.atr_len))
        if not atr or atr <= 0:
            return None
        lv = L.swing_levels(bars, left=self.swing_lr, right=self.swing_lr, merge_pct=self.merge_pct)
        nl = L.nearest_levels(c, lv, atr=atr)
        div = S.divergence(bars, left=self.swing_lr, right=self.swing_lr, ratio=self.div_ratio)
        last = div[-1] if div else None
        # a leg-end pivot is only confirmable `swing_lr` bars after it, so "fresh"
        # must allow that confirmation lag plus a small tolerance.
        fresh = bool(last and (len(bars) - 1 - last["idx"]) <= self.swing_lr + self.fresh_bars)

        # ---- exit: let the engine's trailing stop (set at entry) bank the move —
        #      this is the redo: the previous premature "near resistance / overbought"
        #      targets capped winners and drove exit_efficiency to -1.45. The only
        #      discretionary exit kept is the bounce itself exhausting (up-leg 背驰). ----
        if position_qty > 0:
            if fresh and last is not None and last["dir"] == "up" and last["is_divergence"]:
                return StrategyIntent(self.id, state.symbol, self.timeframe, ts, FLAT, 1.0, c, 0.0,
                                      state.regime_label, "exit: bounce 背驰 (up-leg)", {})
            return None

        # ---- entry (long): down-leg 背驰 AT support, with R:R, in a range ----
        sup = nl["support"]
        rr = nl["rr_by_levels"]
        if sup is None or rr is None or rr < self.min_rr:
            return None
        if (c - sup) > self.near_atr * atr:                 # must be AT support, not mid-range
            return None
        if not (fresh and last["dir"] == "down" and last["is_divergence"]):
            return None
        if self.require_range:
            chop = F.last_valid(F.choppiness(bars, 14))
            if chop is None or chop < self.chop_min:
                return None
        if self.require_nested:                 # 区间套: sub-level must also show a down-leg 背驰
            sd = getattr(state, "sub_div", None)
            if not (sd and sd.get("dir") == "down"):
                return None
        stop = sup - self.stop_atr * atr
        conv = max(0.0, min(1.0, 1.0 - last["metric_ratio"]))   # weaker down-leg => higher conviction
        return StrategyIntent(
            self.id, state.symbol, self.timeframe, ts, LONG, conv, c, stop,
            state.regime_label,
            f"down-leg 背驰 @support {sup:.0f}, rr={rr:.1f}, ratio={last['metric_ratio']:.2f}",
            {"metric_ratio": last["metric_ratio"], "rr": rr, "support": sup},
            self.trail_atr * atr)        # engine trails the stop by trail_atr·ATR below the peak
