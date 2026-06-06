"""Materialize the timeframe ladder from the 1s base (gold cache), via DuckDB COPY.

1s stays the single source of truth; recomputing every timeframe from 181M 1s
rows per backtest is wasteful. This builds ``bars_<tf>`` gold tables once using a
single streaming DuckDB ``COPY ... PARTITION_BY (symbol, date)`` per timeframe —
no Python pylist, so even 15s (~12M rows/symbol) is low-memory and fast.

Ladder: trading is 1s (the stored truth); analysis spans 15s..1d. Sub-minute
(15s/30s/1m) derive from 1s; coarser (5m..1d) derive from 1m — which is LOSSLESS
(a 5m bar's OHLC/volume from 1m equals that from 1s). Downstream reads hit the
materialized table directly via ``resample(prefer_materialized=True)``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from quant import market_store
from quant.market_store import MICROS, _partition_files, _sql_file_list, freq_to_seconds

# trading granularity = 1s (stored truth); analysis ladder 15s .. 1d
DEFAULT_INTERVALS = ("15s", "30s", "1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d")


def _clear(root, tf, symbol):
    d = Path(root) / f"bars_{tf}" / f"symbol={symbol}"
    if d.exists():
        shutil.rmtree(d)


def _materialize_duckdb(symbol, target, root, source):
    """Aggregate ``bars_<source>`` -> ``bars_<target>`` with one streaming COPY."""
    import duckdb

    tgt = freq_to_seconds(target) * MICROS
    files = _partition_files(root, f"bars_{source}", symbol=symbol)
    if not files:
        return 0
    _clear(root, target, symbol)
    out_dir = str(Path(root) / f"bars_{target}")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    # OHLCV identifiers are quoted ("close"/"open" are DuckDB reserved words).
    sql = f'''COPY (
        SELECT symbol,
               strftime(make_timestamp(bkt), '%Y-%m-%d') AS date,
               bkt AS bucket_ts,
               arg_min("open", ots) AS "open", max("high") AS "high", min("low") AS "low",
               arg_max("close", ots) AS "close", sum("volume") AS "volume",
               CASE WHEN sum("volume") > 0 THEN sum("vwap" * "volume") / sum("volume")
                    ELSE arg_max("close", ots) END AS "vwap",
               sum("trades") AS "trades"
        FROM (
            SELECT bucket_ts AS ots, (bucket_ts - (bucket_ts % {tgt})) AS bkt,
                   '{symbol}' AS symbol, "open", "high", "low", "close", "volume", "vwap", "trades"
            FROM read_parquet({_sql_file_list(files)}, hive_partitioning=1)
        )
        GROUP BY symbol, bkt
    ) TO '{out_dir}' (FORMAT parquet, PARTITION_BY (symbol, date), OVERWRITE_OR_IGNORE, COMPRESSION zstd)'''
    con = duckdb.connect()
    try:
        con.execute(sql)
        g = str(Path(root) / f"bars_{target}" / f"symbol={symbol}" / "**" / "*.parquet")
        return con.execute(f"SELECT count(*) FROM read_parquet('{g}', hive_partitioning=1)").fetchone()[0]
    finally:
        con.close()


def materialize(symbol, *, root=None, base="1s", intervals=DEFAULT_INTERVALS, on_progress=None):
    """Build gold-cache bars tables for ``symbol``. Returns {tf: row_count}.

    Sub-minute (<=60s incl. 1m) derive from the 1s base; coarser timeframes derive
    from the (already-built) 1m table — lossless and far cheaper than re-scanning 1s.
    """
    root = root if root is not None else market_store.DEFAULT_ROOT
    out: dict[str, int] = {}
    from_base = sorted((tf for tf in intervals if freq_to_seconds(tf) <= 60), key=freq_to_seconds)
    from_1m = sorted((tf for tf in intervals if freq_to_seconds(tf) > 60), key=freq_to_seconds)
    src_coarse = "1m" if "1m" in intervals else base

    for tf in from_base:           # 15s, 30s, 1m  <- 1s
        out[tf] = _materialize_duckdb(symbol, tf, root, base)
        if on_progress:
            on_progress(symbol, tf, out[tf])
    for tf in from_1m:             # 5m .. 1d  <- 1m (lossless, cheap)
        out[tf] = _materialize_duckdb(symbol, tf, root, src_coarse)
        if on_progress:
            on_progress(symbol, tf, out[tf])
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
