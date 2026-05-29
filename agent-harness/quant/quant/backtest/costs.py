"""Cost model for backtest + paper fills (fees + slippage; funding=0 for spot).

Deliberately explicit and conservative — under-modeling costs is how a backtest
lies. Slippage is a flat depth-naive stub now; a depth-based model (walking the
order book from the trades/L2 layer) replaces it before any live promotion.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CostModel:
    taker_bps: float = 10.0          # Binance spot taker ~0.10%
    maker_bps: float = 7.5           # ~0.075% (varies by tier/BNB)
    slippage_bps: float = 2.0        # flat stub; depth-based later (quant-rigor gate)
    funding_bps_per_day: float = 0.0  # spot = 0; perps would populate this

    def fill_price(self, ref: float, side: str) -> float:
        """Adverse slippage: buys fill higher, sells lower."""
        slip = self.slippage_bps / 1e4
        return ref * (1.0 + slip) if side == "buy" else ref * (1.0 - slip)

    def fee(self, notional: float, *, maker: bool = False) -> float:
        bps = self.maker_bps if maker else self.taker_bps
        return abs(notional) * bps / 1e4


ZERO_COST = CostModel(taker_bps=0.0, maker_bps=0.0, slippage_bps=0.0)
