"""Reproducible market-data ingest: exchange API → dedup → Parquet store.

The store (``~/quant/market``, Hive-partitioned Parquet read by DuckDB) is the canonical,
best-practice local quant layout. This module is the *missing reproducible loader*: it pulls
klines from the configured exchange API (via ``live.fetch_*``), dedups by ``bucket_ts``,
persists through ``market_store.write_bars``, and appends a provenance line to
``<root>/_ingest_manifest.jsonl`` — so the multi-GB store is reproducible AND incrementally
refreshable instead of an undocumented one-off dump.

  refresh(sym, freq)      — page recent bars, append-dedup (keep the store CURRENT)
  backfill(sym, freq)     — deep page-back for COARSE tfs (4h/1d); fine tfs use bulk dumps
  refresh_all(syms,freqs) — sweep configured (symbol, freq) pairs

The seam stays clean: this lives in the ``quant`` package (it may use duckdb); the stdlib
``omni_hub`` side only shells out to the CLI below and parses its JSON (see SCHEMA.md §7).

CLI:
  python -m quant.ingest --refresh-all
  python -m quant.ingest --refresh  --symbol BTCUSDT --freq 4h
  python -m quant.ingest --backfill --symbol BTCUSDT --freq 1d --max-bars 3000
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import market_store as ms

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")
# coarse tfs page end-to-end from the klines API; 1m/5m deep history should come from
# binance.vision bulk dumps (see docs) — refresh still keeps their recent tail current.
DEFAULT_FREQS = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
MANIFEST = "_ingest_manifest.jsonl"


def _api_fetch(symbol: str, freq: str, max_bars: int) -> list[dict]:
    from . import live
    return live.fetch_history(symbol, freq, venue="binance", max_bars=max_bars)


def _existing_ts(symbol: str, freq: str, lo: int, hi: int, *, root) -> set:
    """bucket_ts already in the store across the touched date range (empty store -> empty)."""
    try:
        rows = ms.bars(symbol, freq, ms.micros_to_utc_date(lo), ms.micros_to_utc_date(hi), root=root)
    except Exception:
        return set()
    return {r["bucket_ts"] for r in rows}


def _persist(symbol: str, freq: str, bars: list[dict], *, root, source: str, stamp=None) -> dict:
    """Append-dedup ``bars`` into ``bars_<freq>/symbol=<symbol>`` and record provenance.

    Idempotent: rows whose ``bucket_ts`` already exist in the store are skipped, so re-running
    an overlapping window never duplicates."""
    bars = [b for b in bars if b.get("bucket_ts") is not None]
    if not bars:
        return {"symbol": symbol, "freq": freq, "fetched": 0, "written": 0,
                "first_ts": None, "last_ts": None, "parts": 0, "source": source, "stamp": stamp}
    bars.sort(key=lambda b: b["bucket_ts"])
    lo, hi = bars[0]["bucket_ts"], bars[-1]["bucket_ts"]
    existing = _existing_ts(symbol, freq, lo, hi, root=root)
    fresh = [{**b, "symbol": symbol} for b in bars if b["bucket_ts"] not in existing]
    paths = ms.write_bars(fresh, symbol=symbol, freq=freq, root=root) if fresh else []
    rec = {"symbol": symbol, "freq": freq, "fetched": len(bars), "written": len(fresh),
           "first_ts": lo, "last_ts": hi, "parts": len(paths), "source": source, "stamp": stamp}
    if fresh:
        _append_manifest(root, rec)
    return rec


def _append_manifest(root, rec: dict) -> None:
    path = Path(root) / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def refresh(symbol: str, freq: str, *, root=ms.DEFAULT_ROOT, max_bars: int = 2000,
            fetch=None, stamp=None) -> dict:
    """Pull the recent tail from the API (paging back ``max_bars``) and append-dedup it.

    2000 bars covers a multi-week gap for 1h+ tfs (4h≈333d, 1d≈5.5y); fine tfs over a long
    staleness need a larger ``max_bars`` or more frequent runs."""
    bars = (fetch or _api_fetch)(symbol, freq, max_bars)
    return _persist(symbol, freq, bars, root=root, source="binance-api:refresh", stamp=stamp)


def backfill(symbol: str, freq: str, *, root=ms.DEFAULT_ROOT, max_bars: int = 20000,
             fetch=None, stamp=None) -> dict:
    """Deep page-back backfill (coarse tfs). For 1m/5m deep history use bulk dumps, not this."""
    bars = (fetch or _api_fetch)(symbol, freq, max_bars)
    return _persist(symbol, freq, bars, root=root, source="binance-api:backfill", stamp=stamp)


def refresh_all(symbols=DEFAULT_SYMBOLS, freqs=DEFAULT_FREQS, *, root=ms.DEFAULT_ROOT,
                max_bars: int = 2000, fetch=None, stamp=None) -> list[dict]:
    """Refresh every configured (symbol, freq) pair — the scheduled-job entry point.

    Resilient: one failing pair (bad interval, network blip) is recorded with an ``error`` and
    the sweep continues, so a single bad symbol/freq never aborts the whole catch-up."""
    out = []
    for s in symbols:
        for f in freqs:
            try:
                out.append(refresh(s, f, root=root, max_bars=max_bars, fetch=fetch, stamp=stamp))
            except Exception as e:  # noqa: BLE001 — never let one pair abort the sweep
                out.append({"symbol": s, "freq": f, "fetched": 0, "written": 0,
                            "error": f"{type(e).__name__}: {e}", "stamp": stamp})
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quant.ingest", description="API → dedup → Parquet store.")
    p.add_argument("--symbol")
    p.add_argument("--freq")
    p.add_argument("--refresh", action="store_true", help="page recent tail, append-dedup")
    p.add_argument("--backfill", action="store_true", help="deep page-back (coarse tfs)")
    p.add_argument("--refresh-all", action="store_true", help="sweep all configured pairs")
    p.add_argument("--max-bars", type=int, default=None)
    p.add_argument("--root", default=None, help="store root (default ~/quant/market)")
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    root = a.root or ms.DEFAULT_ROOT
    stamp = time.time()
    if a.refresh_all:
        out = refresh_all(root=root, max_bars=a.max_bars or 2000, stamp=stamp)
    elif a.backfill:
        if not (a.symbol and a.freq):
            build_parser().error("--backfill needs --symbol and --freq")
        out = [backfill(a.symbol, a.freq, root=root, max_bars=a.max_bars or 20000, stamp=stamp)]
    elif a.refresh:
        if not (a.symbol and a.freq):
            build_parser().error("--refresh needs --symbol and --freq")
        out = [refresh(a.symbol, a.freq, root=root, max_bars=a.max_bars or 2000, stamp=stamp)]
    else:
        build_parser().error("one of --refresh / --backfill / --refresh-all is required")
    print(json.dumps({"ok": True, "ingested": out}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
