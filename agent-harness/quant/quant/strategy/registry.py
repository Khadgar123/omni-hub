"""Strategy registry — the Phase-1 default set."""

from __future__ import annotations

from quant.strategy.base import Strategy
from quant.strategy.divergence_reversal import DivergenceReversal
from quant.strategy.ma_cross import MACross
from quant.strategy.range_bb_revert import RangeBBRevert
from quant.strategy.squeeze_breakout import SqueezeBreakout
from quant.strategy.trend_donchian import TrendDonchian
from quant.strategy.tsmom import TSMomentum
from quant.strategy.zscore_revert import ZScoreRevert


def default_strategies() -> list[Strategy]:
    """The strategy library across families: trend (Donchian breakout, TS-momentum,
    EMA cross), range mean-reversion (Bollinger+RSI, z-score), momentum-exhaustion
    reversal (缠论 背驰), and volatility-squeeze breakout (中枢突破)."""
    return [TrendDonchian(), TSMomentum(), MACross(), RangeBBRevert(), ZScoreRevert(),
            DivergenceReversal(), SqueezeBreakout()]


def by_id(strategy_id: str) -> Strategy:
    for s in default_strategies():
        if s.id == strategy_id:
            return s
    raise KeyError(f"unknown strategy_id {strategy_id!r}; known: {[s.id for s in default_strategies()]}")


_CLASS_BY_ID = {s.id: type(s) for s in default_strategies()}


def build(strategy_id: str, **params) -> Strategy:
    """Instantiate a strategy by id with overridden params (for sweeps)."""
    cls = _CLASS_BY_ID.get(strategy_id)
    if cls is None:
        raise KeyError(f"unknown strategy_id {strategy_id!r}; known: {sorted(_CLASS_BY_ID)}")
    return cls(**params)
