"""Thin backtest read-path + nautilus_trader ``ParquetDataCatalog`` compatibility.

nautilus_trader's catalog IS Parquet, so the goal is to be *compatible* with
its ``TradeTick`` / ``Bar`` record shapes rather than reinvent a catalog.  The
converters here are dependency-free (they emit plain dicts using nautilus field
names + nanosecond timestamps); feeding them into a real ``ParquetDataCatalog``
only needs the ``nautilus_trader`` package installed (see ``nautilus_available``).

nautilus reference:
  * ``TradeTick``: instrument_id, price, size, aggressor_side
    (NO_AGGRESSOR/BUYER/SELLER), trade_id, ts_event (ns), ts_init (ns).
  * ``Bar``: bar_type (e.g. ``"NVDA.XNAS-1-DAY-LAST-EXTERNAL"``), open, high,
    low, close, volume, ts_event (ns), ts_init (ns).
"""

from __future__ import annotations

from pathlib import Path

from . import market_store as ms

NS_PER_US = 1_000

# our trade.side -> nautilus AggressorSide
_AGGRESSOR = {"buy": "BUYER", "sell": "SELLER", "": "NO_AGGRESSOR"}

# freq unit -> nautilus BarAggregation
_AGGREGATION = {"s": "SECOND", "m": "MINUTE", "h": "HOUR", "d": "DAY", "w": "WEEK"}


def nautilus_available() -> bool:
    """True if ``nautilus_trader`` is importable in this venv."""

    import importlib.util

    return importlib.util.find_spec("nautilus_trader") is not None


def to_aggressor_side(side: str) -> str:
    return _AGGRESSOR.get((side or "").lower(), "NO_AGGRESSOR")


def instrument_id(symbol: str, venue: str) -> str:
    """nautilus InstrumentId string, e.g. ``"NVDA.XNAS"`` / ``"BTCUSDT.BINANCE"``."""

    return f"{symbol}.{venue}"


def bar_type_str(symbol: str, venue: str, freq: str, *, price_type: str = "LAST") -> str:
    """nautilus BarType string, e.g. ``"NVDA.XNAS-1-DAY-LAST-EXTERNAL"``."""

    f = str(freq).strip().lower()
    unit = f[-1]
    step = int(f[:-1] or "1")
    agg = _AGGREGATION[unit]
    return f"{instrument_id(symbol, venue)}-{step}-{agg}-{price_type}-EXTERNAL"


def to_nautilus_trade_tick_dict(row: dict, *, venue: str | None = None) -> dict:
    """Map a frozen ``trades`` row -> a nautilus ``TradeTick`` kwargs dict."""

    v = venue if venue is not None else row.get("venue", "")
    ts = int(row["exchange_ts"]) * NS_PER_US
    ts_init = int(row.get("receive_ts", row["exchange_ts"])) * NS_PER_US
    return {
        "instrument_id": instrument_id(row["symbol"], v),
        "price": float(row["price"]),
        "size": float(row["size"]),
        "aggressor_side": to_aggressor_side(row.get("side", "")),
        "trade_id": str(row.get("trade_id", "") or row.get("sequence", "")),
        "ts_event": ts,
        "ts_init": ts_init,
    }


def to_nautilus_bar_dict(row: dict, *, symbol: str, venue: str, freq: str) -> dict:
    """Map a DERIVED bar row -> a nautilus ``Bar`` kwargs dict (ts in ns)."""

    ts = int(row["bucket_ts"]) * NS_PER_US
    return {
        "bar_type": bar_type_str(symbol, venue, freq),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row.get("volume", 0.0)),
        "ts_event": ts,
        "ts_init": ts,
    }


def read_for_backtest(
    symbol: str,
    freq: str,
    start,
    end,
    *,
    root: Path | str = ms.DEFAULT_ROOT,
    asof=None,
    adjust: bool = True,
    venue: str = "SIM",
) -> dict:
    """Thin, point-in-time backtest read-path.

    Returns ``{"instrument_id", "bar_type", "bars": [...nautilus Bar dicts...]}``
    with split-adjusted, ``asof``-bounded bars (no look-ahead by default).
    """

    rows = ms.bars(symbol, freq, start, end, root=root, asof=asof, adjust=adjust)
    return {
        "instrument_id": instrument_id(symbol, venue),
        "bar_type": bar_type_str(symbol, venue, freq),
        "bars": [to_nautilus_bar_dict(r, symbol=symbol, venue=venue, freq=freq) for r in rows],
    }


def read_trade_ticks_for_backtest(
    symbol: str,
    start,
    end,
    *,
    root: Path | str = ms.DEFAULT_ROOT,
    venue: str = "SIM",
) -> dict:
    """Backtest read-path for raw trade ticks (nautilus ``TradeTick`` dicts)."""

    rows = ms.trades(symbol, start, end, root=root)
    return {
        "instrument_id": instrument_id(symbol, venue),
        "ticks": [to_nautilus_trade_tick_dict(r, venue=venue) for r in rows],
    }
