"""Strategy registry — the Phase-1 default set."""

from __future__ import annotations

from quant.strategy.base import Strategy
from quant.strategy.divergence_reversal import DivergenceReversal
from quant.strategy.range_bb_revert import RangeBBRevert
from quant.strategy.trend_donchian import TrendDonchian
from quant.strategy.tsmom import TSMomentum
from quant.strategy.zscore_revert import ZScoreRevert


def default_strategies() -> list[Strategy]:
    """The strategy library: trend (Donchian, TS-momentum), range (Bollinger+RSI,
    z-score), and momentum-exhaustion reversal (缠论 背驰, quantified)."""
    return [TrendDonchian(), TSMomentum(), RangeBBRevert(), ZScoreRevert(), DivergenceReversal()]


def by_id(strategy_id: str) -> Strategy:
    for s in default_strategies():
        if s.id == strategy_id:
            return s
    raise KeyError(f"unknown strategy_id {strategy_id!r}; known: {[s.id for s in default_strategies()]}")
