"""Deterministic position sizing — never an LLM number.

The min of a stop-distance (vol/ATR-target) sizer and a fractional-Kelly cap,
scaled by conviction, hard-capped at a fraction of equity.
"""

from __future__ import annotations

RISK_PER_TRADE = 0.01          # 1% of equity at risk per trade (2% ceiling)
KELLY_FRACTION = 0.25          # quarter-Kelly
MAX_POSITION_FRAC = 0.25       # never more than 25% of equity in one position


def size_qty(
    *,
    equity: float,
    entry: float,
    stop: float,
    conviction: float = 1.0,
    risk_per_trade: float = RISK_PER_TRADE,
    kelly_fraction: float = KELLY_FRACTION,
    edge_estimate: float = 0.5,
    max_position_frac: float = MAX_POSITION_FRAC,
) -> float:
    """Position size in base units.

    ``risk_qty`` makes risk-per-trade constant in dollars: a wider ATR stop ->
    smaller size, so notional auto-shrinks in high vol. The fractional-Kelly leg
    is a second cap.

    Phase-1 note: with a flat ``edge_estimate=0.5`` the Kelly term is constant,
    so this is effectively a **fixed-fraction** sizer — name it honestly; a real
    ``edge_estimate`` comes only from walk-forward backtest stats, never live or
    from an LLM.
    """
    if entry <= 0:
        return 0.0
    conviction = max(0.0, min(1.0, conviction))
    stop_dist = abs(entry - stop)
    risk_qty = (equity * risk_per_trade) / stop_dist if stop_dist > 0 else 0.0
    kelly_frac = max(0.0, min(0.5, kelly_fraction * edge_estimate))
    kelly_qty = (equity * kelly_frac) / entry
    qty = min(risk_qty, kelly_qty) * conviction
    max_qty = (equity * max_position_frac) / entry
    return max(0.0, min(qty, max_qty))
