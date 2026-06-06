"""Strategy contract + regime-gated runner.

A Strategy is PURE TA over bars: given the bar history, the MarketState, and the
current position, it returns a target-change intent (open long / close to flat)
or ``None`` (hold). It does NOT enforce regime discipline itself — the runner
(``gated_evaluate``) does, so the gate is un-bypassable and unit-testable:

  * **Entries** (``direction != "flat"``) are gated — blocked on ``stand_down``,
    on a regime not in the strategy's ``eligible_regimes``, or on a
    ``composite_bias`` mismatch.
  * **Exits** (``direction == "flat"``) ALWAYS pass — a risk-reducing action is
    never gated out (if the regime flips while we're long, we must still close).

Phase-1 is spot long-only: ``direction`` is ``"long"`` (open/hold) or ``"flat"``
(close). ``"short"`` is reserved.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional, Protocol, runtime_checkable

LONG = "long"
FLAT = "flat"
SHORT = "short"  # reserved (spot long-only in Phase-1)


@dataclass(frozen=True, slots=True)
class StrategyIntent:
    """A target-position change emitted by a strategy (NOT an order)."""

    strategy_id: str
    symbol: str
    timeframe: str
    asof: int                 # signal-bar bucket_ts (epoch µs, UTC)
    direction: str            # "long" (open/hold long) | "flat" (close)
    conviction: float         # 0..1
    entry_ref: float          # signal-bar close (reference price)
    stop_price: float         # protective stop (0.0 for exits)
    regime_at_signal: str
    rationale: str
    features: dict = field(default_factory=dict)
    trail_distance: float = 0.0   # >0 (LONG entry) => engine trails the stop up by
                                  # this price distance below the running peak (Chandelier)

    def to_dict(self) -> dict:
        return asdict(self)


@runtime_checkable
class Strategy(Protocol):
    id: str
    timeframe: str
    eligible_regimes: frozenset
    requires_bias: Optional[str]  # "long" / "short" / None (range = None)

    def evaluate(self, bars, state, position_qty: float) -> Optional[StrategyIntent]:
        ...


def gated_evaluate(strat: Strategy, bars, state, position_qty: float) -> Optional[StrategyIntent]:
    """Run a strategy and apply the un-bypassable regime gate to ENTRIES only.

    ``state`` is a ``MarketState`` (duck-typed: needs ``regime_label``,
    ``composite_bias``, ``stand_down``).
    """
    intent = strat.evaluate(bars, state, position_qty)
    if intent is None:
        return None
    if intent.direction == FLAT:
        return intent  # exits / risk-reducing always allowed
    # --- entry gate (defense in depth; the strategy can't bypass it) ---
    if getattr(state, "stand_down", False):
        return None
    if state.regime_label not in strat.eligible_regimes:
        return None
    if strat.requires_bias is not None and state.composite_bias != strat.requires_bias:
        return None
    return intent
