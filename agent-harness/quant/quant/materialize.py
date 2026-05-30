"""Materialize common timeframes from the 1s base (gold cache).

1s stays the single source of truth, but recomputing 1h/4h/1d from 181M 1s rows
on every backtest is wasteful. This resamples ONCE: 1s -> 1m (the single heavy
scan over the 1s data), then derives 5m/15m/1h/4h/1d cheaply from 1m, writing
each as a ``bars_<tf>`` store table. Downstream reads then hit the materialized
table directly (instant) instead of re-aggregating. Idempotent: clears each
target table's symbol partitions before rewriting.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from quant import market_store
from quant import resample as rs

# the full analysis ladder: trading is 1s (the stored truth), analysis tops at 1d
DEFAULT_INTERVALS = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d")


def _clear(root, tf, symbol):
    d = Path(root) / f"bars_{tf}" / f"symbol={symbol}"
    if d.exists():
        shutil.rmtree(d)


def materialize(symbol, *, root=None, base="1s", intervals=DEFAULT_INTERVALS, on_progress=None):
    """Build gold-cache bars tables for ``symbol``. Returns {tf: row_count}."""
    root = root if root is not None else market_store.DEFAULT_ROOT
    out: dict[str, int] = {}

    # 1m from the 1s base (the one expensive full scan)
    if "1m" in intervals:
        _clear(root, "1m", symbol)
        rows = rs.resample(symbol, "1m", root=root, source_interval=base, prefer_materialized=False)
        if rows:
            market_store.write_bars(rows, symbol=symbol, freq="1m", root=root)
        out["1m"] = len(rows)
        if on_progress:
            on_progress(symbol, "1m", len(rows))

    # coarser timeframes derived from 1m (cheap) — fall back to base if no 1m
    derive_src = "1m" if "1m" in intervals else base
    for tf in intervals:
        if tf == "1m":
            continue
        _clear(root, tf, symbol)
        rows = rs.resample(symbol, tf, root=root, source_interval=derive_src, prefer_materialized=False)
        if rows:
            market_store.write_bars(rows, symbol=symbol, freq=tf, root=root)
        out[tf] = len(rows)
        if on_progress:
            on_progress(symbol, tf, len(rows))
    return out


def main(argv=None):
    import argparse
    import json
    import sys
    from pathlib import Path as _P

    p = argparse.ArgumentParser(prog="quant.materialize", description=__doc__)
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    p.add_argument("--intervals", default=",".join(DEFAULT_INTERVALS))
    p.add_argument("--root", default=None)
    p.add_argument("--base", default="1s")
    a = p.parse_args(argv)
    root = _P(a.root).expanduser() if a.root else None
    intervals = tuple(s.strip() for s in a.intervals.split(",") if s.strip())
    summary = {}
    for sym in [s.strip() for s in a.symbols.split(",") if s.strip()]:
        def prog(symbol, tf, n):
            print(json.dumps({"symbol": symbol, "tf": tf, "rows": n}), file=sys.stderr, flush=True)
        summary[sym] = materialize(sym, root=root, base=a.base, intervals=intervals, on_progress=prog)
    print(json.dumps({"ok": True, "materialized": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
