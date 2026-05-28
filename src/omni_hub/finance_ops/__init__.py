"""Finance ops plane (v0.36).

**Read-only analysis in main repo**.  Any operation that moves money
— place / cancel / modify orders — emits ``Proposal(kind=order_intent)``
through the existing Proposal[T] gate.  A human approves; the broker
shim under ``agent-harness/integrations/finance/`` executes.

2026 Q2 SOTA consensus (FinRL-X, Alpaca, ccxt, freqtrade): retail AI
trading is paper-trade + read-only signals.  Auto-executing LLM agents
on margin accounts compounds SEC/FINRA risk with hallucination risk;
omni-hub refuses by construction.

Components:

* :class:`FinanceAnalyst` — screen / watch / portfolio_stats (no money moves)
* :class:`OrderIntent` — typed proposal for buy / sell / limit / stop
* :class:`RiskCheckResult` — risk-aware "would-this-be-safe" gate
"""

from __future__ import annotations

from .analyst import (
    AlertRule,
    FinanceAnalyst,
    PortfolioSnapshot,
    ScreenCriteria,
    StockSignal,
)
from .order import (
    HARD_BLOCK_POSITION_FRACTION,
    OrderIntent,
    OrderSide,
    OrderType,
    RiskCheckResult,
    WARN_POSITION_FRACTION,
    risk_check,
)

__all__ = [
    "AlertRule",
    "FinanceAnalyst",
    "HARD_BLOCK_POSITION_FRACTION",
    "OrderIntent",
    "OrderSide",
    "OrderType",
    "PortfolioSnapshot",
    "RiskCheckResult",
    "ScreenCriteria",
    "StockSignal",
    "WARN_POSITION_FRACTION",
    "risk_check",
]
