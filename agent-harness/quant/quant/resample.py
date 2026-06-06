"""Resample stored bars to a coarser timeframe (DuckDB aggregation over Parquet).

UTC-epoch-aligned buckets (so a 1h bar matches Binance's clock). vwap aggregates
volume-weighted; open/close via arg_min/arg_max on bucket_ts. This is how we get
1h/4h/1d strategy + regime bars from the canonical 1s store without storing every
timeframe.
"""

from __future__ import annotations

from pathlib import Path

from quant import market_store
from quant.market_store import (
    MICROS,
    _fetch_arrow,
    _partition_files,
    _sql_file_list,
    freq_to_seconds,
    micros_to_utc_date,
    parse_ts,
)


def resample(symbol, target_interval, *, root=market_store.DEFAULT_ROOT,
             source_interval="1s", start=None, end=None, prefer_materialized=True):
    """Aggregate ``bars_<source_interval>`` -> ``target_interval`` bars.

    Returns ``list[dict]`` (bucket_ts µs / open / high / low / close / volume /
    vwap / trades), sorted ascending. Empty list if no source data.

    If ``prefer_materialized`` and a ``bars_<target_interval>`` gold-cache table
    already has data for the symbol, it is read directly (instant) instead of
    re-aggregating the 1s base. ``materialize`` passes ``False`` to force a real
    aggregation when (re)building the cache.
    """
    if prefer_materialized and target_interval != source_interval:
        cached = market_store.bars(symbol, target_interval,
                                   start if start is not None else "1970-01-01",
                                   end if end is not None else "2100-01-01", root=root)
        if cached:
            return cached
    tgt_us = freq_to_seconds(target_interval) * MICROS
    table = f"bars_{source_interval}"
    start_us = parse_ts(start) if start is not None else None
    end_us = parse_ts(end, end_of_day=True) if end is not None else None
    files = _partition_files(
        root, table, symbol=symbol,
        start_date=micros_to_utc_date(start_us) if start_us is not None else None,
        end_date=micros_to_utc_date(end_us) if end_us is not None else None,
    )
    if not files:
        return []
    conds = []
    if start_us is not None:
        conds.append(f"bucket_ts >= {start_us}")
    if end_us is not None:
        conds.append(f"bucket_ts <= {end_us}")
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    sql = f"""
        SELECT
            (bucket_ts - (bucket_ts % {tgt_us}))                        AS bucket_ts,
            arg_min(open, bucket_ts)                                    AS open,
            max(high)                                                   AS high,
            min(low)                                                    AS low,
            arg_max(close, bucket_ts)                                   AS close,
            sum(volume)                                                 AS volume,
            CASE WHEN sum(volume) > 0 THEN sum(vwap * volume) / sum(volume)
                 ELSE arg_max(close, bucket_ts) END                     AS vwap,
            sum(trades)                                                 AS trades
        FROM read_parquet({_sql_file_list(files)}, hive_partitioning=1){where}
        GROUP BY 1 ORDER BY 1
    """
    import duckdb

    con = duckdb.connect()
    try:
        return _fetch_arrow(con.execute(sql)).to_pylist()
    finally:
        con.close()
