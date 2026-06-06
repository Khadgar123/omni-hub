"""Regime-gated strategy library (pure TA + deterministic sizing; no LLM).

Strategies are pure technical rules over bars; the runner (``base.gated_evaluate``)
enforces regime discipline so the gate is un-bypassable. Phase-1 is spot
long-only: a trend-follower (fires in trend regimes) and a mean-reversion
strategy (fires in range regimes). They emit ``StrategyIntent`` target-changes
(open long / close to flat); sizing is deterministic.
"""
