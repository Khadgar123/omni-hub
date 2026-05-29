"""Strategy registry — the Phase-1 default set."""

from __future__ import annotations

from quant.strategy.base import Strategy
from quant.strategy.range_bb_revert import RangeBBRevert
from quant.strategy.trend_donchian import TrendDonchian


def default_strategies() -> list[Strategy]:
    """Phase-1: one trend-follower + one mean-reversion strategy."""
    return [TrendDonchian(), RangeBBRevert()]


def by_id(strategy_id: str) -> Strategy:
    for s in default_strategies():
        if s.id == strategy_id:
            return s
    raise KeyError(f"unknown strategy_id {strategy_id!r}; known: {[s.id for s in default_strategies()]}")
