"""OrderIntent — typed proposal for buy / sell / limit / stop (v0.36).

**Never executes by itself.**  The flow is:

1. CLI / agent emits an :class:`OrderIntent` (this dataclass)
2. :func:`risk_check` computes :class:`RiskCheckResult`
3. Caller writes a ``Proposal(kind="order_intent")`` carrying both
4. Human reviews via ``propose-list --kind order_intent``
5. Human approves → broker CLI in ``agent-harness/integrations/finance/``
   reads the approved Proposal, places the actual order, writes back
   a fill record.

Hard rule: **no path through main-repo code touches a broker SDK**.
That keeps the SEC/FINRA + secrets blast radius outside the main repo.
"""

from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _new_intent_id() -> str:
    return f"oi_{secrets.token_hex(6)}"


@dataclass(slots=True)
class RiskCheckResult:
    """Pre-trade risk audit."""

    passes: bool
    notional_usd: float                    # estimated cash impact
    position_pct_of_portfolio: float       # 0..1, fraction of total NAV
    warnings: list[str] = field(default_factory=list)
    hard_blocks: list[str] = field(default_factory=list)
    checked_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class OrderIntent:
    """Typed pre-order; **does not place anything**."""

    intent_id: str
    user_id: str
    instrument: str                        # "NVDA" | "BTC-USD" | "600519.SH"
    side: OrderSide
    qty: float
    type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day"
    rationale: str = ""
    risk_check: RiskCheckResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["side"] = self.side.value
        data["type"] = self.type.value
        if self.risk_check is not None:
            data["risk_check"] = self.risk_check.to_dict()
        return data

    @classmethod
    def new(
        cls,
        *,
        user_id: str,
        instrument: str,
        side: OrderSide,
        qty: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        rationale: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "OrderIntent":
        if qty <= 0:
            raise ValueError("qty must be positive")
        if order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT} and limit_price is None:
            raise ValueError(f"{order_type.value} order requires limit_price")
        if order_type in {OrderType.STOP, OrderType.STOP_LIMIT} and stop_price is None:
            raise ValueError(f"{order_type.value} order requires stop_price")
        return cls(
            intent_id=_new_intent_id(),
            user_id=user_id,
            instrument=instrument,
            side=side,
            qty=qty,
            type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            rationale=rationale,
            metadata=dict(metadata or {}),
        )


# ---------------------------------------------------------------------------
# Risk gate — deterministic, conservative defaults.
# ---------------------------------------------------------------------------


# Position size cap: any single intent over 25% of portfolio is auto-blocked.
HARD_BLOCK_POSITION_FRACTION = 0.25
WARN_POSITION_FRACTION = 0.10


def risk_check(
    intent: OrderIntent,
    *,
    portfolio_value_usd: float,
    estimated_price: float | None = None,
) -> RiskCheckResult:
    """Conservative pre-trade risk audit.

    Two thresholds:
        * > ``WARN_POSITION_FRACTION`` of portfolio → warning
        * > ``HARD_BLOCK_POSITION_FRACTION`` → hard block (intent fails)

    A market-order without ``estimated_price`` is auto-blocked: we
    can't size the risk without a price.  Same rule the SEC retail-
    trading guidance leans toward.
    """

    price = estimated_price or intent.limit_price or intent.stop_price
    warnings: list[str] = []
    hard_blocks: list[str] = []

    if price is None:
        hard_blocks.append(
            "no price available — refusing to size MARKET order from main repo; "
            "set --estimated-price or use a LIMIT order."
        )
        notional = 0.0
        pct = 0.0
    else:
        notional = float(price) * float(intent.qty)
        if portfolio_value_usd <= 0:
            warnings.append(
                "portfolio_value_usd not set — sizing fraction unknown; "
                "broker shim should populate before execute."
            )
            pct = 0.0
        else:
            pct = notional / portfolio_value_usd
            if pct > HARD_BLOCK_POSITION_FRACTION:
                hard_blocks.append(
                    f"position is {pct:.0%} of portfolio (cap "
                    f"{HARD_BLOCK_POSITION_FRACTION:.0%}); split the order."
                )
            elif pct > WARN_POSITION_FRACTION:
                warnings.append(
                    f"position is {pct:.0%} of portfolio (warn at "
                    f"{WARN_POSITION_FRACTION:.0%})."
                )

    if intent.type is OrderType.MARKET and intent.side is OrderSide.BUY and not warnings:
        warnings.append(
            "MARKET BUY: review fill price before executing in fast-moving names."
        )

    passes = not hard_blocks
    return RiskCheckResult(
        passes=passes,
        notional_usd=notional,
        position_pct_of_portfolio=pct,
        warnings=warnings,
        hard_blocks=hard_blocks,
    )


__all__ = [
    "HARD_BLOCK_POSITION_FRACTION",
    "OrderIntent",
    "OrderSide",
    "OrderType",
    "RiskCheckResult",
    "WARN_POSITION_FRACTION",
    "risk_check",
]
