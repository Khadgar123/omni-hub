"""Cost model for backtest + paper fills (fees + spread + slippage + market impact).

Deliberately explicit and conservative — under-modeling costs is how a backtest
lies. Friction is deterministic and paid every trade, so it compounds linearly in
turnover: a high-win-rate / low-edge / high-N strategy is the most fragile.

Costs, by component:
  * fee — maker (limit/passive, can be a rebate) vs taker (market). The single
    biggest controllable lever (~8 bps/round-trip on perps).
  * spread — crossing the book costs half the bid-ask per fill.
  * slippage — flat depth-naive floor.
  * impact — the square-root market-impact law I = Y·σ·√(Q/V) (δ≈0.5, confirmed
    on >1M Bitcoin metaorders, Donier-Bonart 2014). Off unless adv+σ+Y configured.

Defaults reproduce the previous flat model (spread/impact = 0) so engine parity is
unchanged; populate the impact fields to size-penalize large orders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(slots=True)
class CostModel:
    taker_bps: float = 10.0           # Binance spot taker ~0.10%
    maker_bps: float = 7.5            # ~0.075% (varies by tier/BNB); perps can be a rebate
    slippage_bps: float = 2.0         # flat depth-naive floor
    funding_bps_per_day: float = 0.0  # spot = 0; perps would populate this
    spread_bps: float = 0.0           # full bid-ask; half is paid per fill
    impact_y: float = 0.0             # square-root-law prefactor Y (~0.5-1.0); 0 = off
    adv: float = 0.0                  # average daily volume (same unit as qty)
    daily_sigma_bps: float = 0.0      # daily volatility in bps (for impact)
    maker: bool = False               # do fills earn maker (limit) or pay taker (market)?

    def impact_bps(self, qty: float | None) -> float:
        """Square-root market-impact law: ``I = Y·σ·√(Q/V)`` in bps. Concave —
        4× size ⇒ 2× impact. Returns 0 unless Y, adv, σ are all configured."""
        if (self.impact_y <= 0 or self.adv <= 0 or self.daily_sigma_bps <= 0
                or not qty or qty <= 0):
            return 0.0
        return self.impact_y * self.daily_sigma_bps * math.sqrt(qty / self.adv)

    def slip_bps(self, qty: float | None = None) -> float:
        """Total adverse price move per fill = slippage + half-spread + impact(qty)."""
        return self.slippage_bps + self.spread_bps / 2.0 + self.impact_bps(qty)

    def fill_price(self, ref: float, side: str, *, qty: float | None = None) -> float:
        """Adverse fill: buys fill higher, sells lower, by ``slip_bps(qty)``."""
        slip = self.slip_bps(qty) / 1e4
        return ref * (1.0 + slip) if side == "buy" else ref * (1.0 - slip)

    def fee(self, notional: float, *, maker: bool | None = None) -> float:
        """Fee on a fill. ``maker=None`` uses the model's default (``self.maker``)."""
        use_maker = self.maker if maker is None else maker
        bps = self.maker_bps if use_maker else self.taker_bps
        return abs(notional) * bps / 1e4

    def round_trip_bps(self, qty: float | None = None) -> float:
        """Break-even hurdle: total round-trip friction in bps (entry+exit). A
        strategy's gross per-trade edge must clear this just to be flat."""
        fee_bps = self.maker_bps if self.maker else self.taker_bps
        return 2.0 * (fee_bps + self.slip_bps(qty))


ZERO_COST = CostModel(taker_bps=0.0, maker_bps=0.0, slippage_bps=0.0)
