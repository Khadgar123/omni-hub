"""omni-hub-quant — single-user market store (DuckDB + Hive-partitioned Parquet).

Public read/write API.  This package lives in its own venv with duckdb /
pyarrow / polars; the stdlib-only main omni-hub repo MUST NOT import it (the
seam is a CLI shell-out + the frozen SCHEMA.md, never a Python import).

Re-exports are lazy (PEP 562) so ``import quant`` stays cheap and does not pull
in duckdb/pyarrow until a function that needs them is actually used.  This also
avoids ``python -m quant.market_store`` re-importing an already-imported
submodule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__version__ = "0.1.0"

# attribute -> submodule it lives in
_EXPORTS = {
    name: "market_store"
    for name in (
        "DEFAULT_ROOT", "MICROS",
        "TRADE_FIELDS", "QUOTE_FIELDS", "ORDERBOOK_FIELDS", "BAR_FIELDS",
        "CORPORATE_ACTION_FIELDS", "LISTING_FIELDS", "CALENDAR_FIELDS",
        "parse_ts", "micros_to_utc_date", "micros_to_iso", "freq_to_seconds",
        "partition_path", "bars_from_trades",
        "write_trades", "write_quotes", "write_orderbook", "write_bars",
        "write_corporate_actions", "write_listings", "write_calendar", "write_parquet",
        "query", "bars", "trades", "last_price",
        "corporate_actions_for", "adjust_bars", "listings_asof", "live_symbols",
        "trading_sessions", "main",
    )
}

__all__ = ["__version__", *sorted(_EXPORTS)]


def __getattr__(name: str):
    submod = _EXPORTS.get(name)
    if submod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(f".{submod}", __name__)
    return getattr(mod, name)


def __dir__():
    return sorted(__all__)


if TYPE_CHECKING:  # help editors/type-checkers see the lazy exports
    from .market_store import (  # noqa: F401
        DEFAULT_ROOT, MICROS, TRADE_FIELDS, QUOTE_FIELDS, ORDERBOOK_FIELDS,
        BAR_FIELDS, CORPORATE_ACTION_FIELDS, LISTING_FIELDS, CALENDAR_FIELDS,
        parse_ts, micros_to_utc_date, micros_to_iso, freq_to_seconds,
        partition_path, bars_from_trades, write_trades, write_quotes,
        write_orderbook, write_bars, write_corporate_actions, write_listings,
        write_calendar, write_parquet, query, bars, trades, last_price,
        corporate_actions_for, adjust_bars, listings_asof, live_symbols,
        trading_sessions, main,
    )
