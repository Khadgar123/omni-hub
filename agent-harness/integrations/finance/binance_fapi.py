#!/usr/bin/env python3
"""Binance USD-M futures PUBLIC data — funding rate + open interest (no auth).

The #1 crypto regime/conviction signal per the research: persistently high
funding = crowded longs = fragile trend / squeeze risk; OI rising with price =
real trend, OI rising with flat price = building squeeze. These are SIGNALS for
the regime committee + conviction scoring — NOT tradeable legs (we're spot
notify+manual). Public ``fapi`` endpoints, no API key. ``urllib`` (dep-light);
``request_fn`` is injectable so the mappers/fetchers test with no network.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from typing import Any, Callable

DEFAULT_BASE_URL = "https://fapi.binance.com"
_UA = "omni-hub-quant-fapi/0.1"
_MICROS = 1_000_000
# 8-hour funding magnitudes (per-interval): |rate| >= this = crowded/elevated.
FUNDING_ELEVATED = 0.0005  # 0.05%/8h ~= 54% annualized


def _get_json(method, base_url, path, *, params=None, opener=None, timeout=15.0):
    import urllib.parse
    opener = opener or urllib.request.urlopen
    q = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = base_url.rstrip("/") + path + (f"?{q}" if q else "")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with opener(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --- pure mappers -----------------------------------------------------------

def funding_to_row(raw: dict, symbol: str) -> dict:
    """`/fapi/v1/fundingRate` record -> row (fundingTime ms -> µs)."""
    return {"symbol": symbol, "bucket_ts": int(raw["fundingTime"]) * 1000,
            "funding_rate": float(raw["fundingRate"])}


def oi_to_row(raw: dict, symbol: str) -> dict:
    """`/futures/data/openInterestHist` record -> row (timestamp ms -> µs)."""
    return {"symbol": symbol, "bucket_ts": int(raw["timestamp"]) * 1000,
            "open_interest": float(raw["sumOpenInterest"]),
            "open_interest_value": float(raw["sumOpenInterestValue"])}


def funding_regime(funding_rate: float, *, elevated: float = FUNDING_ELEVATED) -> str:
    """Crowded-positioning label from an 8h funding rate (a conviction overlay)."""
    if funding_rate >= elevated:
        return "crowded_long"     # longs pay shorts heavily -> fragile uptrend / squeeze risk
    if funding_rate <= -elevated:
        return "crowded_short"
    return "neutral"


# --- fetchers (network; request_fn injectable) ------------------------------

def fetch_funding_rate(symbol, *, start_ms=None, end_ms=None, limit=1000,
                       base_url=DEFAULT_BASE_URL, request_fn=None, timeout=15.0) -> list[dict]:
    request_fn = request_fn or _get_json
    params: dict[str, Any] = {"symbol": symbol, "limit": int(limit)}
    if start_ms is not None:
        params["startTime"] = int(start_ms)
    if end_ms is not None:
        params["endTime"] = int(end_ms)
    raw = request_fn("GET", base_url, "/fapi/v1/fundingRate", params=params, timeout=timeout)
    return [funding_to_row(r, symbol) for r in raw]


def fetch_open_interest_hist(symbol, period="1h", *, limit=500,
                             base_url=DEFAULT_BASE_URL, request_fn=None, timeout=15.0) -> list[dict]:
    request_fn = request_fn or _get_json
    params = {"symbol": symbol, "period": period, "limit": int(limit)}
    raw = request_fn("GET", base_url, "/futures/data/openInterestHist", params=params, timeout=timeout)
    return [oi_to_row(r, symbol) for r in raw]


# --- CLI --------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(prog="binance_fapi", description=__doc__)
    p.add_argument("command", choices=["funding", "oi"])
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--period", default="1h")
    p.add_argument("--limit", type=int, default=30)
    args = p.parse_args(argv)
    try:
        if args.command == "funding":
            rows = fetch_funding_rate(args.symbol, limit=args.limit)
            latest = rows[-1] if rows else None
            out = {"symbol": args.symbol, "n": len(rows), "latest": latest,
                   "regime": funding_regime(latest["funding_rate"]) if latest else None}
        else:
            rows = fetch_open_interest_hist(args.symbol, args.period, limit=args.limit)
            out = {"symbol": args.symbol, "period": args.period, "n": len(rows),
                   "latest": rows[-1] if rows else None}
    except Exception as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stderr); sys.stderr.write("\n")
        return 2
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
