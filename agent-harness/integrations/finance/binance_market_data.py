#!/usr/bin/env python3
"""Binance public market-data ingestion -> quant trades/bars store.

This is the *market-data* sibling of ``binance_spot_live.py`` (which handles
signed account/order checks).  It pulls **public** endpoints (no API key needed):

  * ``/api/v3/aggTrades`` -> the TRUTH ``trades`` table (append-only).
  * ``/api/v3/klines``    -> DERIVED ``bars_<freq>`` (Binance pre-aggregates;
    our own truth is ``bars_from_trades``, so prefer ingesting aggTrades when
    you care about microstructure).

Design notes:
  * HTTP goes through ``binance_spot_live.request_json`` (one code path; it
    already supports an injectable ``opener`` and raises ``BinanceLiveError``).
    ``request_fn`` is injectable here too, so the mapping/fetch logic is
    unit-testable with **no network**.
  * The pure mappers (``agg_trade_to_row``, ``kline_to_bar_row``) have no
    third-party deps and import nothing heavy — testable standalone.
  * Writing imports the ``quant`` package lazily (it lives in the quant venv,
    a sibling sub-instance).  Per the seam, this integration layer MAY import
    ``quant``; the stdlib-only ``src/omni_hub`` may not.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

DEFAULT_BASE_URL = "https://api.binance.com"
MICROS = 1_000_000
_HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------
# Lazy wiring to siblings (no heavy work at import; pure mappers stay isolated).
# --------------------------------------------------------------------------


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_REQUEST_JSON: Callable | None = None


def _default_request_json(method, base_url, path, *, params=None, timeout=15.0):
    """Reuse binance_spot_live.request_json (loaded on first call)."""

    global _REQUEST_JSON
    if _REQUEST_JSON is None:
        _REQUEST_JSON = _load_sibling("binance_spot_live").request_json
    return _REQUEST_JSON(method, base_url, path, params=params, timeout=timeout)


def _market_store():
    """Import the quant ``market_store`` (sibling sub-instance, lazy)."""

    try:
        from quant import market_store  # type: ignore
    except ImportError:
        sys.path.insert(0, str(_HERE.parents[1] / "quant"))
        from quant import market_store  # type: ignore
    return market_store


def _now_us() -> int:
    return int(time.time() * MICROS)


def _to_ms(value) -> int:
    """Normalize a date/iso/epoch to epoch millis (for Binance params)."""

    return _market_store().parse_ts(value) // 1_000


# --------------------------------------------------------------------------
# Pure mappers — frozen-schema rows (see agent-harness/quant/SCHEMA.md).
# --------------------------------------------------------------------------


def agg_trade_to_row(raw: dict, symbol: str, *, venue: str = "binance", receive_ts_us: int | None = None) -> dict:
    """Binance ``/api/v3/aggTrades`` record -> a ``trades`` row.

    Binance fields: ``a`` aggTradeId, ``p`` price, ``q`` qty, ``T`` timestamp
    (ms), ``m`` isBuyerMaker.  ``m=true`` means the buyer was the *maker*, so
    the aggressor (taker) was the **seller**.
    """

    ts_us = int(raw["T"]) * 1_000  # ms -> us
    return {
        "symbol": symbol,
        "exchange_ts": ts_us,
        "receive_ts": receive_ts_us if receive_ts_us is not None else ts_us,
        "sequence": int(raw["a"]),
        "price": float(raw["p"]),
        "size": float(raw["q"]),
        "side": "sell" if raw.get("m") else "buy",
        "trade_id": str(raw["a"]),
        "fee": 0.0,
        "slippage": 0.0,
        "order_state": "",
        "venue": venue,
    }


def kline_to_bar_row(raw: list, symbol: str) -> dict:
    """Binance ``/api/v3/klines`` record -> a DERIVED ``bars_<freq>`` row.

    Kline layout: ``[openTime(ms), open, high, low, close, volume, closeTime,
    quoteAssetVolume, numTrades, ...]``.
    """

    volume = float(raw[5])
    quote_vol = float(raw[7])
    return {
        "symbol": symbol,
        "bucket_ts": int(raw[0]) * 1_000,  # openTime ms -> us
        "open": float(raw[1]),
        "high": float(raw[2]),
        "low": float(raw[3]),
        "close": float(raw[4]),
        "volume": volume,
        "vwap": (quote_vol / volume) if volume else float(raw[4]),
        "trades": int(raw[8]),
    }


# --------------------------------------------------------------------------
# Fetchers (network; request_fn injectable for tests).
# --------------------------------------------------------------------------


def fetch_agg_trades(
    symbol: str,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
    from_id: int | None = None,
    limit: int = 1000,
    base_url: str = DEFAULT_BASE_URL,
    request_fn: Callable | None = None,
    timeout: float = 15.0,
) -> list[dict]:
    request_fn = request_fn or _default_request_json
    params: dict[str, Any] = {"symbol": symbol, "limit": int(limit)}
    if from_id is not None:
        params["fromId"] = int(from_id)
    else:
        if start_ms is not None:
            params["startTime"] = int(start_ms)
        if end_ms is not None:
            params["endTime"] = int(end_ms)
    return request_fn("GET", base_url, "/api/v3/aggTrades", params=params, timeout=timeout)


def fetch_klines(
    symbol: str,
    interval: str,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = 1000,
    base_url: str = DEFAULT_BASE_URL,
    request_fn: Callable | None = None,
    timeout: float = 15.0,
) -> list[list]:
    request_fn = request_fn or _default_request_json
    params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": int(limit)}
    if start_ms is not None:
        params["startTime"] = int(start_ms)
    if end_ms is not None:
        params["endTime"] = int(end_ms)
    return request_fn("GET", base_url, "/api/v3/klines", params=params, timeout=timeout)


# --------------------------------------------------------------------------
# Ingestion (fetch -> map -> write parquet).
# --------------------------------------------------------------------------


def ingest_agg_trades(
    symbol: str,
    *,
    start,
    end,
    root=None,
    venue: str = "binance",
    base_url: str = DEFAULT_BASE_URL,
    request_fn: Callable | None = None,
    limit: int = 1000,
    max_pages: int = 100,
    timeout: float = 15.0,
) -> dict:
    """Page aggTrades over ``[start, end]`` and append them to ``trades/``.

    Pages by ``fromId`` (the robust way past Binance's 1000-row / 1-hour caps),
    cutting off at ``end``.  Returns a summary dict.
    """

    ms = _market_store()
    root = root if root is not None else ms.DEFAULT_ROOT
    start_ms = _to_ms(start)
    end_ms = _to_ms(end)

    rows: list[dict] = []
    from_id: int | None = None
    pages = 0
    done = False
    while pages < max_pages and not done:
        if from_id is None:
            page = fetch_agg_trades(
                symbol, start_ms=start_ms, end_ms=min(start_ms + 3_600_000, end_ms),
                limit=limit, base_url=base_url, request_fn=request_fn, timeout=timeout,
            )
        else:
            page = fetch_agg_trades(
                symbol, from_id=from_id, limit=limit,
                base_url=base_url, request_fn=request_fn, timeout=timeout,
            )
        if not page:
            break
        recv = _now_us()
        for raw in page:
            if int(raw["T"]) > end_ms:
                done = True
                break
            rows.append(agg_trade_to_row(raw, symbol, venue=venue, receive_ts_us=recv))
        from_id = int(page[-1]["a"]) + 1
        pages += 1
        if len(page) < limit:
            break

    paths = ms.write_trades(rows, root=root) if rows else []
    return {
        "symbol": symbol,
        "ingested": len(rows),
        "pages": pages,
        "partitions": [str(p) for p in paths],
        "root": str(root),
    }


def ingest_klines(
    symbol: str,
    interval: str,
    *,
    start,
    end,
    freq: str | None = None,
    root=None,
    base_url: str = DEFAULT_BASE_URL,
    request_fn: Callable | None = None,
    limit: int = 1000,
    timeout: float = 15.0,
) -> dict:
    """Fetch klines and persist them as DERIVED ``bars_<freq>`` (freq defaults
    to ``interval``)."""

    ms = _market_store()
    root = root if root is not None else ms.DEFAULT_ROOT
    freq = freq or interval
    raw = fetch_klines(
        symbol, interval, start_ms=_to_ms(start), end_ms=_to_ms(end),
        limit=limit, base_url=base_url, request_fn=request_fn, timeout=timeout,
    )
    bars = [kline_to_bar_row(k, symbol) for k in raw]
    paths = ms.write_bars(bars, symbol=symbol, freq=freq, root=root) if bars else []
    return {
        "symbol": symbol,
        "freq": freq,
        "ingested": len(bars),
        "partitions": [str(p) for p in paths],
        "root": str(root),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="binance_market_data",
        description="Ingest Binance public market data (aggTrades/klines) into the quant store.",
    )
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--root", default=None, help="quant data root (default: ~/quant/market)")
    p.add_argument("--timeout", type=float, default=15.0)
    sub = p.add_subparsers(dest="command", required=True)

    at = sub.add_parser("agg-trades", help="fetch aggTrades and print raw JSON (no write)")
    at.add_argument("--symbol", required=True)
    at.add_argument("--start", default=None)
    at.add_argument("--end", default=None)
    at.add_argument("--from-id", type=int, default=None)
    at.add_argument("--limit", type=int, default=1000)

    ig = sub.add_parser("ingest", help="page aggTrades over [start,end] -> trades/ parquet")
    ig.add_argument("--symbol", required=True)
    ig.add_argument("--start", required=True)
    ig.add_argument("--end", required=True)
    ig.add_argument("--limit", type=int, default=1000)
    ig.add_argument("--max-pages", type=int, default=100)

    kl = sub.add_parser("klines", help="fetch klines and print raw JSON (no write)")
    kl.add_argument("--symbol", required=True)
    kl.add_argument("--interval", default="1m")
    kl.add_argument("--start", default=None)
    kl.add_argument("--end", default=None)
    kl.add_argument("--limit", type=int, default=1000)

    ik = sub.add_parser("ingest-klines", help="fetch klines -> bars_<freq> parquet")
    ik.add_argument("--symbol", required=True)
    ik.add_argument("--interval", default="1m")
    ik.add_argument("--freq", default=None, help="store freq (defaults to --interval)")
    ik.add_argument("--start", required=True)
    ik.add_argument("--end", required=True)
    ik.add_argument("--limit", type=int, default=1000)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def emit(obj):
        json.dump(obj, sys.stdout, ensure_ascii=False, default=str)
        sys.stdout.write("\n")

    try:
        if args.command == "agg-trades":
            start_ms = _to_ms(args.start) if args.start else None
            end_ms = _to_ms(args.end) if args.end else None
            emit(fetch_agg_trades(
                args.symbol, start_ms=start_ms, end_ms=end_ms, from_id=args.from_id,
                limit=args.limit, base_url=args.base_url, timeout=args.timeout,
            ))
        elif args.command == "ingest":
            emit(ingest_agg_trades(
                args.symbol, start=args.start, end=args.end, root=args.root,
                base_url=args.base_url, limit=args.limit, max_pages=args.max_pages,
                timeout=args.timeout,
            ))
        elif args.command == "klines":
            start_ms = _to_ms(args.start) if args.start else None
            end_ms = _to_ms(args.end) if args.end else None
            emit(fetch_klines(
                args.symbol, args.interval, start_ms=start_ms, end_ms=end_ms,
                limit=args.limit, base_url=args.base_url, timeout=args.timeout,
            ))
        elif args.command == "ingest-klines":
            emit(ingest_klines(
                args.symbol, args.interval, start=args.start, end=args.end,
                freq=args.freq, root=args.root, base_url=args.base_url,
                limit=args.limit, timeout=args.timeout,
            ))
        else:  # pragma: no cover
            return 2
    except Exception as exc:  # surface a clean JSON error on stderr
        json.dump({"ok": False, "error": str(exc)}, sys.stderr, ensure_ascii=False)
        sys.stderr.write("\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
