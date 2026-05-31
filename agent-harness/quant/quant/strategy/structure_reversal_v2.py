"""structure_reversal_v2 — the ②③④ rebuild of the composed reversal.

Addresses the three real flaws the discussion exposed in v1:

  ② LEVEL SEPARATION (entry-TF ≠ management-TF): entry is still triggered on the
     1h (precise), but the STOP and TRAILING are sized by the OPERATING-level ATR
     (the confirm TF, e.g. 4h) injected as ``state.op_atr`` — so the hold is
     governed by the holding timeframe's volatility, not the tight 1h ATR that
     made v1's 1-ATR stop a noise magnet. ("enter small, manage big".)

  ③ RICHER / MULTI-METHOD S/R: support is the nearest of {swing-pivot cluster,
     Volume-Profile VAL, Volume-Profile POC} below price — not swing alone. The
     vacuous v1 R:R gate (tiny denominator at support → always passes) is dropped;
     we just require a resistance to exist above (a real upside target).

  ④ MOMENTUM-GATED FADE: keep the down-leg 背驰 (exhaustion), but DON'T fade a
     violently accelerating downtrend — block when ADX is very high. Fade
     exhaustion, not strength. (A follow/breakout branch is future work.)

Still long-only spot, regime-gated to down/range. A HYPOTHESIS to be judged by the
backtest + forward/DSR gate, not an asserted edge. Deferred (need causal cross-TF
injection): true HTF-level confluence for the dominant S/R, and a sub-level entry
trigger via 区间套.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant import features as F
from quant import levels as L
from quant import structure as S
from quant.strategy.base import FLAT, LONG, StrategyIntent


@dataclass(slots=True)
class StructureReversalV2:
    id: str = "structure_reversal_v2"
    timeframe: str = "1h"            # entry-trigger TF
    swing_lr: int = 3
    atr_len: int = 14
    div_ratio: float = 0.9
    near_atr: float = 1.0            # "at support" within this many ENTRY-TF ATR (precise entry)
    stop_atr: float = 1.5            # initial stop below support, in OPERATING-level ATR
    trail_atr: float = 3.0           # trailing distance, in OPERATING-level ATR
    fresh_bars: int = 3
    require_range: bool = True
    chop_min: float = 55.0
    adx_max: float = 45.0            # ④ don't fade a violently accelerating downtrend
    merge_pct: float = 0.004
    vp_bins: int = 48
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
        atr = F.last_valid(F.atr(bars, self.atr_len))          # entry-TF ATR (precision)
        if not atr or atr <= 0:
            return None
        op_atr = getattr(state, "op_atr", None) or atr          # ② management ATR (op level)

        div = S.divergence(bars, left=self.swing_lr, right=self.swing_lr, ratio=self.div_ratio)
        last = div[-1] if div else None
        fresh = bool(last and (len(bars) - 1 - last["idx"]) <= self.swing_lr + self.fresh_bars)

        # ---- exit: up-leg 背驰 ends the bounce; else the engine's op-level trail banks it ----
        if position_qty > 0:
            if fresh and last is not None and last["dir"] == "up" and last["is_divergence"]:
                return StrategyIntent(self.id, state.symbol, self.timeframe, ts, FLAT, 1.0, c, 0.0,
                                      state.regime_label, "exit: bounce 背驰 (up-leg)", {})
            return None

        # ---- ③ multi-method support (swing cluster / VP VAL / VP POC), nearest below price ----
        lv = L.swing_levels(bars, left=self.swing_lr, right=self.swing_lr, merge_pct=self.merge_pct)
        nl = L.nearest_levels(c, lv, atr=atr)
        vp = L.volume_profile(bars, n_bins=self.vp_bins)
        cands = [x for x in (nl["support"], vp["val"], vp["poc"]) if x is not None and x < c]
        sup = max(cands) if cands else None                    # nearest support below = highest one under price
        res = nl["resistance"]
        if sup is None or res is None:                         # need a support to lean on AND an upside target
            return None
        if (c - sup) > self.near_atr * atr:                    # ② precise "at support" on the entry TF
            return None
        if not (fresh and last["dir"] == "down" and last["is_divergence"]):
            return None
        if self.require_range:
            chop = F.last_valid(F.choppiness(bars, 14))
            if chop is None or chop < self.chop_min:
                return None
        adx = F.last_valid(F.adx(bars, 14)["adx"])             # ④ momentum gate
        if adx is not None and adx > self.adx_max:             # too strong a trend to fade
            return None

        stop = sup - self.stop_atr * op_atr                    # ② stop sized on the operating level
        conv = max(0.0, min(1.0, 1.0 - last["metric_ratio"]))
        return StrategyIntent(
            self.id, state.symbol, self.timeframe, ts, LONG, conv, c, stop,
            state.regime_label,
            f"v2 down-leg 背驰 @support {sup:.0f} (op_atr={op_atr:.0f}), adx={adx if adx is None else round(adx)}, ratio={last['metric_ratio']:.2f}",
            {"metric_ratio": last["metric_ratio"], "support": sup, "op_atr": op_atr, "adx": adx},
            self.trail_atr * op_atr)                           # trail on the operating level
