#!/usr/bin/env python3
"""Bulk historical klines from data.binance.vision -> quant bars store.

The public **static dump** site (NOT the strict signed/trading API): monthly
ZIPs of klines with SHA-256 ``.CHECKSUM`` siblings.  This is the only feasible
way to backfill *full second-level* history (REST ``/klines`` caps at 1000
rows/call).  It writes ``bars_<interval>`` rows conforming to
``agent-harness/quant/SCHEMA.md`` (epoch-µs ``bucket_ts``, vwap, …).

Design (mirrors ``binance_market_data.py``):
  * pure mappers (``kline_csv_row_to_bar``) — no deps, unit-testable;
  * ``fetcher`` is injectable, so download/parse logic tests with **no network**;
  * the ``quant`` package is imported lazily for the write (this integration
    layer MAY import quant; the stdlib-only main repo may not);
  * a **storage budget** (``--max-gb``) caps total store size — stops cleanly.

Gotcha handled: Binance SPOT dump timestamps switched ms -> **microseconds**
from 2025-01-01, so ``open_time`` is normalized back to ms before mapping.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

BASE_URL = "https://data.binance.vision/data/spot"
_UA = "omni-hub-quant-vision/0.1"
_MICROS_THRESHOLD = 100_000_000_000_000  # 1e14: ms epochs stay below, µs above


class BinanceVisionError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------

def monthly_url(symbol: str, interval: str, year: int, month: int) -> tuple[str, str]:
    name = f"{symbol}-{interval}-{year:04d}-{month:02d}"
    return f"{BASE_URL}/monthly/klines/{symbol}/{interval}/{name}.zip", name


def iter_months(start: tuple[int, int], end: tuple[int, int]):
    y, m = start
    while (y, m) <= end:
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def parse_ym(text: str) -> tuple[int, int]:
    y, m = text.split("-")
    return int(y), int(m)


# --------------------------------------------------------------------------
# Pure helpers (no deps; testable)
# --------------------------------------------------------------------------

def to_millis(ts) -> int:
    """Normalize a Binance epoch to milliseconds (handles the 2025 µs switch)."""
    v = int(float(ts))
    return v // 1000 if v >= _MICROS_THRESHOLD else v


def _looks_like_header(row: list[str]) -> bool:
    if not row:
        return True
    try:
        int(float(row[0]))
        return False
    except (ValueError, TypeError):
        return True


def kline_csv_row_to_bar(row: list[str], symbol: str) -> dict:
    """A data.binance.vision kline CSV row -> a SCHEMA ``bars_<freq>`` row.

    CSV layout: ``[openTime, open, high, low, close, volume, closeTime,
    quoteAssetVolume, numTrades, takerBuyBase, takerBuyQuote, ignore]``.
    """
    volume = float(row[5])
    quote_vol = float(row[7])
    return {
        "symbol": symbol,
        "bucket_ts": to_millis(row[0]) * 1000,  # -> epoch µs
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": volume,
        "vwap": (quote_vol / volume) if volume else float(row[4]),
        "trades": int(float(row[8])),
    }


def verify_checksum(zip_bytes: bytes, checksum_text: str) -> tuple[bool, str, str]:
    expected = checksum_text.split()[0].strip().lower() if checksum_text.strip() else ""
    actual = hashlib.sha256(zip_bytes).hexdigest().lower()
    return (bool(expected) and actual == expected), expected, actual


def bars_from_zip(zip_bytes: bytes, symbol: str) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        name = zf.namelist()[0]
        text = zf.read(name).decode("utf-8")
    out: list[dict] = []
    for row in csv.reader(io.StringIO(text)):
        if _looks_like_header(row) or len(row) < 9:
            continue
        out.append(kline_csv_row_to_bar(row, symbol))
    return out


# --------------------------------------------------------------------------
# Network + write
# --------------------------------------------------------------------------

def fetch_bytes(url: str, *, opener: Callable | None = None, timeout: float = 120.0) -> bytes:
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with opener(req, timeout=timeout) as resp:
        return resp.read()


def _market_store():
    try:
        from quant import market_store  # type: ignore
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "quant"))
        from quant import market_store  # type: ignore
    return market_store


def _default_write(bars: list[dict], symbol: str, interval: str, root) -> list:
    return _market_store().write_bars(bars, symbol=symbol, freq=interval, root=root)


def dir_size_bytes(path) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


# --------------------------------------------------------------------------
# Backfill
# --------------------------------------------------------------------------

def backfill_month(
    symbol: str,
    interval: str,
    year: int,
    month: int,
    *,
    root=None,
    fetcher: Callable = fetch_bytes,
    verify: bool = True,
    write_fn: Callable | None = None,
    timeout: float = 120.0,
) -> dict:
    root = root if root is not None else _market_store().DEFAULT_ROOT
    write_fn = write_fn or _default_write
    url, name = monthly_url(symbol, interval, year, month)
    zip_bytes = fetcher(url, timeout=timeout)
    if verify:
        chk = fetcher(url + ".CHECKSUM", timeout=timeout)
        if isinstance(chk, bytes):
            chk = chk.decode("utf-8", "replace")
        ok, expected, actual = verify_checksum(zip_bytes, chk)
        if not ok:
            raise BinanceVisionError(
                f"checksum mismatch for {name}: expected {expected!r}, got {actual!r}"
            )
    bars = bars_from_zip(zip_bytes, symbol)
    paths = write_fn(bars, symbol, interval, root) if bars else []
    return {
        "archive": name,
        "downloaded_bytes": len(zip_bytes),
        "bars": len(bars),
        "partitions": len(paths),
    }


def backfill(
    symbols: list[str],
    interval: str,
    start_ym: str,
    end_ym: str,
    *,
    root=None,
    fetcher: Callable = fetch_bytes,
    verify: bool = True,
    write_fn: Callable | None = None,
    max_bytes: int | None = None,
    on_progress: Callable | None = None,
    timeout: float = 120.0,
    newest_first: bool = False,
) -> list[dict]:
    """Month-outer / symbol-inner backfill so a budget cut leaves symbols even.

    Stops cleanly once the store reaches ``max_bytes`` (the --max-gb budget).
    Missing archives (HTTP 404 — e.g. a month before listing) are skipped.
    ``newest_first`` downloads recent months first, so the highest-value (and
    most microstructure-relevant) second-level data lands soonest and a budget
    cut keeps the newest history rather than the oldest.
    """
    root = root if root is not None else _market_store().DEFAULT_ROOT
    results: list[dict] = []
    start, end = parse_ym(start_ym), parse_ym(end_ym)
    months = list(iter_months(start, end))
    if newest_first:
        months.reverse()
    for year, month in months:
        if max_bytes is not None and dir_size_bytes(root) >= max_bytes:
            stop = {"stopped": "storage budget reached", "bytes": dir_size_bytes(root)}
            results.append(stop)
            if on_progress:
                on_progress(stop)
            break
        for symbol in symbols:
            try:
                res = backfill_month(
                    symbol, interval, year, month, root=root, fetcher=fetcher,
                    verify=verify, write_fn=write_fn, timeout=timeout,
                )
            except urllib.error.HTTPError as exc:
                res = {"archive": f"{symbol}-{interval}-{year:04d}-{month:02d}", "skipped": f"HTTP {exc.code}"}
            except (urllib.error.URLError, BinanceVisionError, zipfile.BadZipFile) as exc:
                res = {"archive": f"{symbol}-{interval}-{year:04d}-{month:02d}", "error": str(exc)}
            if on_progress:
                on_progress(res)
            results.append(res)
    return results


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="binance_vision", description=__doc__)
    p.add_argument("--symbols", required=True, help="comma-separated, e.g. BTCUSDT,ETHUSDT")
    p.add_argument("--interval", default="1s")
    p.add_argument("--from", dest="start", required=True, help="YYYY-MM inclusive")
    p.add_argument("--to", dest="end", required=True, help="YYYY-MM inclusive")
    p.add_argument("--root", default=None, help="quant data root (default ~/quant/market)")
    p.add_argument("--max-gb", type=float, default=50.0, help="storage budget; stop when reached")
    p.add_argument("--no-verify", action="store_true", help="skip SHA-256 CHECKSUM verify")
    p.add_argument("--newest-first", action="store_true", help="download recent months first")
    p.add_argument("--timeout", type=float, default=120.0)
    args = p.parse_args(argv)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    max_bytes = int(args.max_gb * (1024 ** 3)) if args.max_gb else None
    root = Path(args.root).expanduser() if args.root else _market_store().DEFAULT_ROOT

    def progress(res):
        # one JSON line per archive to stderr (stdout stays a clean final summary)
        print(json.dumps(res, ensure_ascii=False), file=sys.stderr, flush=True)

    results = backfill(
        symbols, args.interval, args.start, args.end, root=root,
        verify=not args.no_verify, max_bytes=max_bytes, on_progress=progress,
        timeout=args.timeout, newest_first=args.newest_first,
    )
    bars_total = sum(r.get("bars", 0) for r in results)
    dl_total = sum(r.get("downloaded_bytes", 0) for r in results)
    print(json.dumps({
        "ok": True,
        "symbols": symbols,
        "interval": args.interval,
        "archives": len([r for r in results if "bars" in r]),
        "bars_total": bars_total,
        "downloaded_gb": round(dl_total / (1024 ** 3), 3),
        "store_gb": round(dir_size_bytes(root) / (1024 ** 3), 3),
        "root": str(root),
        "stopped": next((r["stopped"] for r in results if "stopped" in r), None),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
