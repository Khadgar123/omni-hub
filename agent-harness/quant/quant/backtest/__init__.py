"""Backtesting with research-to-live PARITY.

The engine drives the SAME ``gated_evaluate`` + ``sizing`` the live/notify path
uses, so a backtest describes the live system by construction. Costs (fees +
slippage; funding=0 for spot) are charged on every fill. Metrics include the
Probabilistic Sharpe Ratio; the Deflated Sharpe + PBO (which need a multi-config
trial set) live in the sweep/validation layer.

This package also re-exports the nautilus_trader read-path mapping (``nautilus``
submodule) so ``from quant import backtest; backtest.bar_type_str(...)`` keeps
working.
"""

from quant.backtest.nautilus import (
    NS_PER_US,
    bar_type_str,
    instrument_id,
    nautilus_available,
    read_for_backtest,
    read_trade_ticks_for_backtest,
    to_aggressor_side,
    to_nautilus_bar_dict,
    to_nautilus_trade_tick_dict,
)

__all__ = [
    "NS_PER_US",
    "bar_type_str",
    "instrument_id",
    "nautilus_available",
    "read_for_backtest",
    "read_trade_ticks_for_backtest",
    "to_aggressor_side",
    "to_nautilus_bar_dict",
    "to_nautilus_trade_tick_dict",
]
