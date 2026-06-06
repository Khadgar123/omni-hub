"""Single-user quant market store — DuckDB + Hive-partitioned Parquet.

Separate sub-instance with its own deps (see ``pyproject.toml``); the main
omni-hub repo stays stdlib-only and NEVER imports this package.

Design (see ``README.md`` + ``SCHEMA.md``):
  * ``trades`` / ``quotes`` / ``orderbook`` events are the TRUTH (append-only,
    carrying ``exchange_ts`` / ``receive_ts`` / ``sequence`` / ``fee`` /
    ``slippage`` / ``order_state``).  OHLCV bars are DERIVED from trades and
    are never the source of truth.
  * Hive layout (``symbol`` + ``date`` live in the PATH, not in the file —
    canonical Hive; DuckDB recovers them via ``hive_partitioning=1``)::

        <root>/<table>/symbol=<SYM>/date=<YYYY-MM-DD>/part-NNNNN.parquet

  * DuckDB globs the Parquet files in-process (no server; billion-row on a
    laptop).
  * Point-in-time correctness: delisted symbols are RETAINED, corporate
    actions carry an ``event_date`` and are applied only when
    ``event_date <= asof`` (anti look-ahead).

Dependency discipline: the pure helpers (``partition_path``,
``freq_to_seconds``, ``bars_from_trades``, the timestamp helpers) have NO
third-party deps and are unit-testable as-is.  Parquet/DuckDB I/O
lazy-imports ``pyarrow`` / ``duckdb`` so this module imports cleanly without
them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

# Default data root — deliberately OUTSIDE the knowledge vault (vault/ is the
# raw->evidence->wiki harness; OHLCV numerics do not belong there).
DEFAULT_ROOT = Path("~/quant/market").expanduser()

#: epoch-micros per second; canonical timestamp unit of the store.
MICROS = 1_000_000

TRUTH_TABLES = ("trades", "quotes", "orderbook")
REFERENCE_DIRNAME = "_reference"


# --------------------------------------------------------------------------
# Record schema (the cross-session contract; see SCHEMA.md).  Declared as
# plain data so this module imports without pyarrow; materialized to an Arrow
# schema lazily in the writer.  ``symbol`` and ``date`` are partition keys
# (in the PATH) and are intentionally NOT payload columns.
# --------------------------------------------------------------------------

# Frozen seam version — bump on ANY column add/rename/retype; SCHEMA.md §8 must agree
# (tests/test_schema_doc.py asserts code ↔ doc stay in sync).
SCHEMA_VERSION = 1

# (name, logical-type, default)
TRADE_FIELDS: list[tuple[str, str, object]] = [
    ("exchange_ts", "int64", 0),     # event time at the exchange (epoch micros, UTC)
    ("receive_ts", "int64", 0),      # local receive time (epoch micros, UTC)
    ("sequence", "int64", 0),        # exchange sequence / monotonic id per symbol
    ("price", "float64", 0.0),
    ("size", "float64", 0.0),        # base-asset quantity
    ("side", "string", ""),          # aggressor side: "buy"/"sell"/"" (unknown)
    ("trade_id", "string", ""),      # exchange trade id (dedup key)
    ("fee", "float64", 0.0),         # realized fee (own fills only; 0 for market data)
    ("slippage", "float64", 0.0),    # realized slippage (own fills only)
    ("order_state", "string", ""),   # own exec lifecycle; "" for market data
    ("venue", "string", ""),         # "binance" / "alpaca" / ...
]

QUOTE_FIELDS: list[tuple[str, str, object]] = [
    ("exchange_ts", "int64", 0),
    ("receive_ts", "int64", 0),
    ("sequence", "int64", 0),
    ("bid_px", "float64", 0.0),
    ("bid_sz", "float64", 0.0),
    ("ask_px", "float64", 0.0),
    ("ask_sz", "float64", 0.0),
    ("venue", "string", ""),
]

# L2/L3 deltas (store deltas, not full snapshots).  size==0 => level removed.
ORDERBOOK_FIELDS: list[tuple[str, str, object]] = [
    ("exchange_ts", "int64", 0),
    ("receive_ts", "int64", 0),
    ("sequence", "int64", 0),
    ("side", "string", ""),          # "bid"/"ask"
    ("price", "float64", 0.0),
    ("size", "float64", 0.0),        # 0 => level removed
    ("is_snapshot", "bool", False),
    ("venue", "string", ""),
]

# OHLCV bars (DERIVED).  ``bucket_ts`` is the bar OPEN time (epoch micros UTC).
BAR_FIELDS: list[tuple[str, str, object]] = [
    ("bucket_ts", "int64", 0),
    ("open", "float64", 0.0),
    ("high", "float64", 0.0),
    ("low", "float64", 0.0),
    ("close", "float64", 0.0),
    ("volume", "float64", 0.0),
    ("vwap", "float64", 0.0),
    ("trades", "int64", 0),
]

# Reference tables (small; single Parquet each under <root>/_reference/).
CORPORATE_ACTION_FIELDS: list[tuple[str, str, object]] = [
    ("symbol", "string", ""),
    ("event_date", "string", ""),    # YYYY-MM-DD (ex-date); applied only if <= asof
    ("type", "string", ""),          # "split" / "dividend" / "rename" / "delist"
    ("ratio", "float64", 1.0),       # split ratio (4.0 => 4:1); 1.0 = no-op
    ("cash_amount", "float64", 0.0), # dividend per share
    ("new_symbol", "string", ""),    # for "rename"
    ("notes", "string", ""),
]

LISTING_FIELDS: list[tuple[str, str, object]] = [
    ("symbol", "string", ""),
    ("name", "string", ""),
    ("venue", "string", ""),
    ("list_date", "string", ""),     # YYYY-MM-DD
    ("delist_date", "string", ""),   # YYYY-MM-DD or "" if still active
    ("status", "string", "active"),  # "active" / "delisted"
    ("asset_class", "string", ""),   # "equity" / "crypto" / ...
]

CALENDAR_FIELDS: list[tuple[str, str, object]] = [
    ("venue", "string", ""),
    ("date", "string", ""),          # YYYY-MM-DD
    ("is_open", "bool", True),
    ("open_ts", "int64", 0),         # session open (epoch micros UTC)
    ("close_ts", "int64", 0),        # session close (epoch micros UTC)
    ("session", "string", "regular"),
]

_TABLE_FIELDS = {
    "trades": TRADE_FIELDS,
    "quotes": QUOTE_FIELDS,
    "orderbook": ORDERBOOK_FIELDS,
}


# --------------------------------------------------------------------------
# Timestamp helpers (pure; no deps).
# --------------------------------------------------------------------------


def _smart_epoch_to_micros(value: int | float) -> int:
    """Heuristically map a numeric epoch to micros by magnitude.

    seconds (~1.7e9) < 1e11 <= millis (~1.7e12) < 1e14 <= micros (~1.7e15).
    """

    v = float(value)
    if abs(v) < 1e11:
        return int(round(v * MICROS))        # seconds
    if abs(v) < 1e14:
        return int(round(v * 1_000))         # millis
    return int(round(v))                     # micros


def parse_ts(value, *, end_of_day: bool = False) -> int:
    """Normalize a timestamp to epoch microseconds (UTC).

    Accepts epoch int/float (seconds/millis/micros by magnitude), a
    ``datetime``/``date``, ``"YYYY-MM-DD"`` (midnight UTC, or 23:59:59.999999
    when ``end_of_day``), or an ISO-8601 datetime string.
    """

    if value is None:
        raise ValueError("timestamp is None")
    if isinstance(value, bool):  # guard: bool is an int subclass
        raise TypeError("bool is not a timestamp")
    if isinstance(value, (int, float)):
        return _smart_epoch_to_micros(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.astimezone(timezone.utc).timestamp() * MICROS)
    if isinstance(value, _date):
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        if end_of_day:
            dt += timedelta(days=1, microseconds=-1)
        return int(dt.timestamp() * MICROS)
    if isinstance(value, str):
        s = value.strip()
        if len(s) == 4 and s.isdigit():  # YYYY -> year bounds
            y = int(s)
            return parse_ts(_date(y, 12, 31), end_of_day=True) if end_of_day else parse_ts(_date(y, 1, 1))
        if len(s) == 7 and s[4] == "-" and s[:4].isdigit() and s[5:].isdigit():  # YYYY-MM -> month bounds
            y, mo = int(s[:4]), int(s[5:])
            if end_of_day:  # last microsecond of the month = first of next month - 1µs
                ny, nm = (y + 1, 1) if mo == 12 else (y, mo + 1)
                return parse_ts(datetime(ny, nm, 1, tzinfo=timezone.utc)) - 1
            return parse_ts(_date(y, mo, 1))
        if len(s) == 10 and s[4] == "-" and s[7] == "-":  # YYYY-MM-DD
            y, m, d = (int(p) for p in s.split("-"))
            return parse_ts(_date(y, m, d), end_of_day=end_of_day)
        # ISO-8601 datetime
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        return parse_ts(dt, end_of_day=end_of_day)
    raise TypeError(f"unsupported timestamp type: {type(value)!r}")


def micros_to_utc_date(micros: int) -> str:
    """``epoch micros`` -> ``"YYYY-MM-DD"`` (UTC) — the Hive partition key."""

    dt = datetime.fromtimestamp(micros / MICROS, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def micros_to_iso(micros: int) -> str:
    """``epoch micros`` -> ISO-8601 UTC string (for human/JSON output)."""

    dt = datetime.fromtimestamp(micros / MICROS, tz=timezone.utc)
    return dt.isoformat()


_FREQ_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def freq_to_seconds(freq: str) -> int:
    """Parse a bar frequency like ``"1m"``, ``"5m"``, ``"1h"``, ``"1d"``."""

    f = str(freq).strip().lower()
    if not f:
        raise ValueError("empty freq")
    unit = f[-1]
    if unit not in _FREQ_UNIT_SECONDS:
        raise ValueError(f"unknown freq unit in {freq!r} (use s/m/h/d/w)")
    num = f[:-1] or "1"
    n = int(num)
    if n <= 0:
        raise ValueError(f"freq must be positive: {freq!r}")
    return n * _FREQ_UNIT_SECONDS[unit]


def partition_path(root: Path | str, table: str, symbol: str, date: str, *, part: str = "part-00000.parquet") -> Path:
    """Hive partition file path for one (table, symbol, date)."""

    return Path(root) / table / f"symbol={symbol}" / f"date={date}" / part


# --------------------------------------------------------------------------
# bars_from_trades — the TRUTH -> DERIVED step (pure; no deps).
# --------------------------------------------------------------------------


def _trade_ts_us(t: dict) -> int:
    """Trade event time in epoch micros.

    Prefers the schema field ``exchange_ts`` (already micros); falls back to a
    legacy ``ts`` field in epoch *seconds*.
    """

    if "exchange_ts" in t and t["exchange_ts"] is not None:
        return int(t["exchange_ts"])
    if "ts" in t:
        return _smart_epoch_to_micros(t["ts"])
    raise KeyError("trade needs 'exchange_ts' (micros) or 'ts' (seconds)")


def bars_from_trades(
    trades: Iterable[dict],
    *,
    freq: str = "1m",
    interval_seconds: int | None = None,
    symbol: str | None = None,
) -> list[dict]:
    """Derive OHLCV bars from raw trade events (TRUTH -> derived).

    Pure-python (no deps).  Buckets are aligned to the UTC epoch (so ``1d`` =
    UTC calendar day; session-aligned bars are a calendar-aware extension).
    Trades are deduplicated by ``trade_id`` when present (ingestion is
    at-least-once).  Returns bars sorted by ``bucket_ts`` with keys
    ``bucket_ts, open, high, low, close, volume, vwap, trades`` (+ ``symbol``
    when given).
    """

    secs = interval_seconds if interval_seconds is not None else freq_to_seconds(freq)
    bucket_us = secs * MICROS
    seen_ids: set[str] = set()
    buckets: dict[int, dict] = {}

    for t in sorted(trades, key=_trade_ts_us):
        tid = t.get("trade_id")
        if tid:
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
        ts_us = _trade_ts_us(t)
        bucket = (ts_us // bucket_us) * bucket_us
        price = float(t["price"])
        size = float(t.get("size", 0.0) or 0.0)
        bar = buckets.get(bucket)
        if bar is None:
            buckets[bucket] = {
                "bucket_ts": bucket, "open": price, "high": price,
                "low": price, "close": price, "volume": size, "trades": 1,
                "_pv": price * size,
            }
        else:
            bar["high"] = max(bar["high"], price)
            bar["low"] = min(bar["low"], price)
            bar["close"] = price
            bar["volume"] += size
            bar["trades"] += 1
            bar["_pv"] += price * size

    out: list[dict] = []
    for key in sorted(buckets):
        bar = buckets[key]
        pv = bar.pop("_pv")
        bar["vwap"] = (pv / bar["volume"]) if bar["volume"] else bar["close"]
        if symbol is not None:
            bar = {"symbol": symbol, **bar}
        out.append(bar)
    return out


# --------------------------------------------------------------------------
# Parquet / DuckDB I/O — lazy-imports so the module loads without the deps.
# --------------------------------------------------------------------------


def _arrow_type(name: str):
    import pyarrow as pa

    return {
        "int64": pa.int64(),
        "float64": pa.float64(),
        "string": pa.string(),
        "bool": pa.bool_(),
    }[name]


def _arrow_schema(fields: Sequence[tuple[str, str, object]]):
    import pyarrow as pa

    return pa.schema([(n, _arrow_type(t)) for (n, t, _d) in fields])


def _coerce_rows(rows: Iterable[dict], fields: Sequence[tuple[str, str, object]]) -> list[dict]:
    """Project each row onto ``fields`` with type coercion + defaults.

    Partition keys (``symbol``/``date``) are dropped — they live in the path.
    """

    coercers = {"int64": int, "float64": float, "string": str, "bool": bool}
    out: list[dict] = []
    for row in rows:
        rec: dict[str, object] = {}
        for name, typ, default in fields:
            val = row.get(name, default)
            if val is None:
                val = default
            rec[name] = coercers[typ](val)
        out.append(rec)
    return out


def write_parquet(path: Path | str, rows: list[dict], *, schema=None) -> Path:
    """Write ``rows`` to a Parquet file (creates parent dirs)."""

    import pyarrow as pa
    import pyarrow.parquet as pq

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, target)
    return target


def _next_part_name(part_dir: Path) -> str:
    """Append-safe part filename: ``part-NNNNN.parquet`` (no clobber)."""

    existing = list(part_dir.glob("part-*.parquet")) if part_dir.is_dir() else []
    return f"part-{len(existing):05d}.parquet"


def _write_event_table(
    table: str,
    rows: Iterable[dict],
    *,
    root: Path | str = DEFAULT_ROOT,
    ts_field: str = "exchange_ts",
) -> list[Path]:
    """Group event rows by (symbol, UTC date of ``ts_field``) and write one
    append-safe part file per partition.  Returns written paths.
    """

    if table not in _TABLE_FIELDS:
        raise ValueError(f"unknown event table: {table!r}")
    fields = _TABLE_FIELDS[table]
    schema = _arrow_schema(fields)

    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        symbol = row.get("symbol")
        if not symbol:
            raise ValueError(f"{table} row missing 'symbol': {row!r}")
        ts_us = int(row[ts_field])
        if table == "trades" and not row.get("receive_ts"):
            row = {**row, "receive_ts": ts_us}  # default receive_ts to event time
        date = micros_to_utc_date(ts_us)
        groups.setdefault((str(symbol), date), []).append(row)

    written: list[Path] = []
    for (symbol, date), grp in sorted(groups.items()):
        part_dir = Path(root) / table / f"symbol={symbol}" / f"date={date}"
        part_dir.mkdir(parents=True, exist_ok=True)
        target = part_dir / _next_part_name(part_dir)
        write_parquet(target, _coerce_rows(grp, fields), schema=schema)
        written.append(target)
    return written


def write_trades(rows: Iterable[dict], *, root: Path | str = DEFAULT_ROOT) -> list[Path]:
    """Append trade events to the TRUTH store (Hive-partitioned by symbol/date)."""

    return _write_event_table("trades", rows, root=root, ts_field="exchange_ts")


def write_quotes(rows: Iterable[dict], *, root: Path | str = DEFAULT_ROOT) -> list[Path]:
    return _write_event_table("quotes", rows, root=root, ts_field="exchange_ts")


def write_orderbook(rows: Iterable[dict], *, root: Path | str = DEFAULT_ROOT) -> list[Path]:
    return _write_event_table("orderbook", rows, root=root, ts_field="exchange_ts")


def write_bars(
    rows: Iterable[dict],
    *,
    symbol: str | None = None,
    freq: str = "1d",
    root: Path | str = DEFAULT_ROOT,
) -> list[Path]:
    """Persist DERIVED OHLCV bars to ``bars_<freq>`` (partitioned by symbol/date).

    ``symbol`` may be supplied per-row (``row['symbol']``) or once via the arg.
    """

    table = f"bars_{freq}"
    schema = _arrow_schema(BAR_FIELDS)
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        sym = row.get("symbol", symbol)
        if not sym:
            raise ValueError("bar row needs a symbol (per-row or via arg)")
        date = micros_to_utc_date(int(row["bucket_ts"]))
        groups.setdefault((str(sym), date), []).append(row)

    written: list[Path] = []
    for (sym, date), grp in sorted(groups.items()):
        part_dir = Path(root) / table / f"symbol={sym}" / f"date={date}"
        part_dir.mkdir(parents=True, exist_ok=True)
        target = part_dir / _next_part_name(part_dir)
        write_parquet(target, _coerce_rows(grp, BAR_FIELDS), schema=schema)
        written.append(target)
    return written


# ---- low-level read helpers ----------------------------------------------


def _partition_files(
    root: Path | str,
    table: str,
    *,
    symbol: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[Path]:
    """Enumerate partition files, pruning by symbol + ``date=`` range in Python
    (so empty/missing partitions return ``[]`` instead of a DuckDB glob error).
    """

    base = Path(root) / table
    if not base.is_dir():
        return []
    sym_dirs = (
        [base / f"symbol={symbol}"] if symbol is not None
        else sorted(base.glob("symbol=*"))
    )
    files: list[Path] = []
    for sd in sym_dirs:
        if not sd.is_dir():
            continue
        for dd in sorted(sd.glob("date=*")):
            d = dd.name.split("=", 1)[1]
            if start_date is not None and d < start_date:
                continue
            if end_date is not None and d > end_date:
                continue
            files.extend(sorted(dd.glob("*.parquet")))
    return files


def _sql_file_list(files: Sequence[Path]) -> str:
    parts = ["'" + str(f).replace("'", "''") + "'" for f in files]
    return "[" + ", ".join(parts) + "]"


def _fetch_arrow(result):
    """Return a pyarrow.Table from a DuckDB result, across duckdb versions.

    duckdb 1.5 renamed ``fetch_arrow_table`` -> ``to_arrow_table`` (and made
    ``.arrow()`` stream a RecordBatchReader); 1.1 only has the former.
    """

    for name in ("to_arrow_table", "fetch_arrow_table"):
        fn = getattr(result, name, None)
        if fn is not None:
            return fn()
    tbl = result.arrow()
    return tbl.read_all() if hasattr(tbl, "read_all") else tbl


def _query_files(
    files: Sequence[Path],
    *,
    columns: str = "*",
    where: str | None = None,
    params: Sequence | None = None,
    order_by: str | None = None,
    limit: int | None = None,
):
    """Run a DuckDB query over an explicit file list; return an Arrow table.

    Returns ``None`` when ``files`` is empty (callers map that to empty result).
    """

    if not files:
        return None
    import duckdb

    sql = f"SELECT {columns} FROM read_parquet({_sql_file_list(files)}, hive_partitioning=1)"
    if where:
        sql += f" WHERE {where}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    con = duckdb.connect()
    try:
        return _fetch_arrow(con.execute(sql, list(params or [])))
    finally:
        con.close()


def query(sql: str, root: Path | str = DEFAULT_ROOT):
    """Run an arbitrary DuckDB SQL query rooted at the store; returns Arrow.

    ``FILE_SEARCH_PATH`` is set to ``root`` so relative globs resolve, e.g.::

        query("SELECT * FROM read_parquet('bars_1d/symbol=NVDA/**/*.parquet', "
              "hive_partitioning=1)")
    """

    import duckdb

    con = duckdb.connect()
    try:
        con.execute(f"SET FILE_SEARCH_PATH='{Path(root)}'")
        return _fetch_arrow(con.execute(sql))
    finally:
        con.close()


def _rows(table) -> list[dict]:
    return table.to_pylist() if table is not None else []


# --------------------------------------------------------------------------
# Public query API (the importable seam; mirrored by the CLI).
# --------------------------------------------------------------------------


def trades(
    symbol: str,
    start,
    end,
    *,
    root: Path | str = DEFAULT_ROOT,
    as_arrow: bool = False,
):
    """Raw trade events for ``symbol`` in ``[start, end]`` (inclusive)."""

    start_us = parse_ts(start)
    end_us = parse_ts(end, end_of_day=True)
    files = _partition_files(
        root, "trades", symbol=symbol,
        start_date=micros_to_utc_date(start_us), end_date=micros_to_utc_date(end_us),
    )
    tbl = _query_files(
        files,
        where="exchange_ts BETWEEN ? AND ?",
        params=[start_us, end_us],
        order_by="exchange_ts, sequence",
    )
    return tbl if as_arrow else _rows(tbl)


def bars(
    symbol: str,
    freq: str,
    start,
    end,
    *,
    root: Path | str = DEFAULT_ROOT,
    asof=None,
    adjust: bool = False,
    as_arrow: bool = False,
):
    """DERIVED OHLCV bars for ``symbol`` at ``freq`` in ``[start, end]``.

    ``asof`` (point-in-time): drop bars with ``bucket_ts > asof`` and, when
    ``adjust`` is set, apply corporate actions with ``event_date <= asof``
    (anti look-ahead).  ``adjust`` without ``asof`` adjusts up to ``end``.
    """

    table = f"bars_{freq}"
    start_us = parse_ts(start)
    end_us = parse_ts(end, end_of_day=True)
    asof_us = parse_ts(asof, end_of_day=True) if asof is not None else None
    upper = min(end_us, asof_us) if asof_us is not None else end_us

    files = _partition_files(
        root, table, symbol=symbol,
        start_date=micros_to_utc_date(start_us), end_date=micros_to_utc_date(upper),
    )
    tbl = _query_files(
        files,
        where="bucket_ts BETWEEN ? AND ?",
        params=[start_us, upper],
        order_by="bucket_ts",
    )
    rows = _rows(tbl)
    if adjust and rows:
        asof_date = micros_to_utc_date(asof_us) if asof_us is not None else micros_to_utc_date(end_us)
        rows = adjust_bars(rows, symbol, asof_date, root=root)
    if as_arrow:
        import pyarrow as pa

        return pa.Table.from_pylist(rows)
    return rows


def last_price(symbol: str, asof, *, root: Path | str = DEFAULT_ROOT) -> float | None:
    """Last trade price with ``exchange_ts <= asof`` (point-in-time; no look-ahead).

    Returns ``None`` if there is no trade at or before ``asof``.
    """

    asof_us = parse_ts(asof, end_of_day=True)
    files = _partition_files(
        root, "trades", symbol=symbol,
        end_date=micros_to_utc_date(asof_us),
    )
    tbl = _query_files(
        files,
        columns="price, exchange_ts, sequence",
        where="exchange_ts <= ?",
        params=[asof_us],
        order_by="exchange_ts DESC, sequence DESC",
        limit=1,
    )
    rows = _rows(tbl)
    return float(rows[0]["price"]) if rows else None


# --------------------------------------------------------------------------
# Reference tables + point-in-time correctness.
# --------------------------------------------------------------------------


def _reference_path(root: Path | str, name: str) -> Path:
    return Path(root) / REFERENCE_DIRNAME / f"{name}.parquet"


def _write_reference(root: Path | str, name: str, rows: Iterable[dict], fields) -> Path:
    schema = _arrow_schema(fields)
    target = _reference_path(root, name)
    return write_parquet(target, _coerce_rows(rows, fields), schema=schema)


def _read_reference(root: Path | str, name: str, fields) -> list[dict]:
    path = _reference_path(root, name)
    if not path.is_file():
        return []
    import duckdb

    con = duckdb.connect()
    try:
        return _fetch_arrow(con.execute(
            f"SELECT * FROM read_parquet('{str(path).replace(chr(39), chr(39) * 2)}')"
        )).to_pylist()
    finally:
        con.close()


def write_corporate_actions(rows: Iterable[dict], *, root: Path | str = DEFAULT_ROOT) -> Path:
    return _write_reference(root, "corporate_actions", rows, CORPORATE_ACTION_FIELDS)


def corporate_actions_for(symbol: str, asof, *, root: Path | str = DEFAULT_ROOT) -> list[dict]:
    """Corporate actions for ``symbol`` with ``event_date <= asof`` (sorted).

    The ``<= asof`` gate is the anti-look-ahead guarantee: a backtest at
    ``asof`` can only see actions known by then.
    """

    asof_date = micros_to_utc_date(parse_ts(asof, end_of_day=True))
    out = [
        r for r in _read_reference(root, "corporate_actions", CORPORATE_ACTION_FIELDS)
        if r["symbol"] == symbol and r["event_date"] and r["event_date"] <= asof_date
    ]
    out.sort(key=lambda r: r["event_date"])
    return out


def adjust_bars(rows: list[dict], symbol: str, asof_date: str, *, root: Path | str = DEFAULT_ROOT) -> list[dict]:
    """Split-back-adjust OHLC prices + volume of ``rows`` as of ``asof_date``.

    For a bar on date ``d``, the factor is the product of split ratios of
    events with ``d < event_date <= asof_date``; prices are divided by it and
    volume multiplied by it (standard back-adjustment).  Only ``type=="split"``
    with ``ratio != 1`` participates; dividends are stored but left to the
    caller (see ``corporate_actions_for``).
    """

    splits = [
        a for a in corporate_actions_for(symbol, asof_date, root=root)
        if a["type"] == "split" and a["ratio"] and a["ratio"] != 1.0
    ]
    if not splits:
        return rows
    out: list[dict] = []
    for bar in rows:
        bar_date = micros_to_utc_date(int(bar["bucket_ts"]))
        factor = 1.0
        for s in splits:
            if bar_date < s["event_date"]:
                factor *= float(s["ratio"])
        if factor == 1.0:
            out.append(bar)
            continue
        adj = dict(bar)
        for px in ("open", "high", "low", "close", "vwap"):
            if px in adj and adj[px] is not None:
                adj[px] = float(adj[px]) / factor
        if "volume" in adj and adj["volume"] is not None:
            adj["volume"] = float(adj["volume"]) * factor
        adj["adjusted"] = True
        out.append(adj)
    return out


def write_listings(rows: Iterable[dict], *, root: Path | str = DEFAULT_ROOT) -> Path:
    """Write the symbol master.  Delisted symbols are RETAINED here (never
    deleted) — that retention is the anti-survivorship guarantee.
    """

    return _write_reference(root, "listings", rows, LISTING_FIELDS)


def listings_asof(asof, *, root: Path | str = DEFAULT_ROOT, venue: str | None = None) -> list[dict]:
    """All known listings annotated with ``is_live`` at ``asof``.

    A symbol is live at ``asof`` iff ``list_date <= asof`` and
    (``delist_date`` empty or ``delist_date > asof``).  Delisted rows are still
    returned (retention); callers filter on ``is_live`` for a point-in-time
    universe without survivorship bias.
    """

    asof_date = micros_to_utc_date(parse_ts(asof, end_of_day=True))
    out: list[dict] = []
    for r in _read_reference(root, "listings", LISTING_FIELDS):
        if venue is not None and r["venue"] != venue:
            continue
        listed = (not r["list_date"]) or r["list_date"] <= asof_date
        delisted = bool(r["delist_date"]) and r["delist_date"] <= asof_date
        rec = dict(r)
        rec["is_live"] = listed and not delisted
        out.append(rec)
    return out


def live_symbols(asof, *, root: Path | str = DEFAULT_ROOT, venue: str | None = None) -> list[str]:
    """Symbols tradable at ``asof`` (point-in-time universe, no survivorship)."""

    return sorted(
        r["symbol"] for r in listings_asof(asof, root=root, venue=venue) if r["is_live"]
    )


def write_calendar(rows: Iterable[dict], *, root: Path | str = DEFAULT_ROOT) -> Path:
    return _write_reference(root, "calendar", rows, CALENDAR_FIELDS)


def trading_sessions(
    start,
    end,
    *,
    root: Path | str = DEFAULT_ROOT,
    venue: str | None = None,
    open_only: bool = True,
) -> list[dict]:
    """Trading-calendar rows in ``[start, end]`` (UTC date range)."""

    start_date = micros_to_utc_date(parse_ts(start))
    end_date = micros_to_utc_date(parse_ts(end, end_of_day=True))
    out = []
    for r in _read_reference(root, "calendar", CALENDAR_FIELDS):
        if venue is not None and r["venue"] != venue:
            continue
        if not (start_date <= r["date"] <= end_date):
            continue
        if open_only and not r["is_open"]:
            continue
        out.append(r)
    out.sort(key=lambda r: (r["venue"], r["date"]))
    return out


# --------------------------------------------------------------------------
# CLI — the shell-out seam for omni-hub (`python -m quant.market_store ...`).
# Emits JSON to stdout so the stdlib-only main repo can parse without importing
# this package.  See SCHEMA.md for the frozen field contract.
# --------------------------------------------------------------------------


def _emit(rows, fmt: str) -> None:
    if fmt == "csv":
        import csv

        rows = list(rows)
        if not rows:
            return
        writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    else:
        json.dump(rows, sys.stdout, ensure_ascii=False, default=str)
        sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="quant.market_store",
        description="Single-user quant market store (DuckDB + Hive Parquet). "
        "Read API + ingestion; emits JSON for the omni-hub shell-out seam.",
    )
    p.add_argument("--root", default=str(DEFAULT_ROOT), help="data root (default: ~/quant/market)")
    p.add_argument("--format", choices=["json", "csv"], default="json")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("bars", help="DERIVED OHLCV bars in [start, end]")
    b.add_argument("--symbol", required=True)
    b.add_argument("--freq", default="1d")
    b.add_argument("--start", required=True)
    b.add_argument("--end", required=True)
    b.add_argument("--asof", default=None, help="point-in-time cutoff (no look-ahead)")
    b.add_argument("--adjust", action="store_true", help="split-adjust as of asof/end")

    lp = sub.add_parser("last-price", help="last trade price as of a timestamp")
    lp.add_argument("--symbol", required=True)
    lp.add_argument("--asof", required=True)

    tr = sub.add_parser("trades", help="raw trade events in [start, end]")
    tr.add_argument("--symbol", required=True)
    tr.add_argument("--start", required=True)
    tr.add_argument("--end", required=True)

    bft = sub.add_parser("bars-from-trades", help="derive bars from stored trades")
    bft.add_argument("--symbol", required=True)
    bft.add_argument("--freq", default="1m")
    bft.add_argument("--start", required=True)
    bft.add_argument("--end", required=True)
    bft.add_argument("--persist", action="store_true", help="also write bars_<freq> partitions")

    ls = sub.add_parser("listings", help="symbol master, annotated is_live at asof")
    ls.add_argument("--asof", required=True)
    ls.add_argument("--venue", default=None)
    ls.add_argument("--live-only", action="store_true")

    ca = sub.add_parser("corporate-actions", help="actions for symbol with event_date<=asof")
    ca.add_argument("--symbol", required=True)
    ca.add_argument("--asof", required=True)

    cal = sub.add_parser("calendar", help="trading sessions in [start, end]")
    cal.add_argument("--start", required=True)
    cal.add_argument("--end", required=True)
    cal.add_argument("--venue", default=None)

    si = sub.add_parser("ingest-sample", help="materialize the bundled sample dataset into --root")
    si.add_argument("--symbol", default=None, help="limit to one sample symbol")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).expanduser()
    cmd = args.command

    if cmd == "bars":
        rows = bars(args.symbol, args.freq, args.start, args.end, root=root,
                    asof=args.asof, adjust=args.adjust)
        _emit(rows, args.format)
    elif cmd == "last-price":
        price = last_price(args.symbol, args.asof, root=root)
        _emit([{"symbol": args.symbol, "asof": args.asof, "last_price": price}], args.format)
    elif cmd == "trades":
        _emit(trades(args.symbol, args.start, args.end, root=root), args.format)
    elif cmd == "bars-from-trades":
        raw = trades(args.symbol, args.start, args.end, root=root)
        derived = bars_from_trades(raw, freq=args.freq, symbol=args.symbol)
        if args.persist and derived:
            write_bars(derived, symbol=args.symbol, freq=args.freq, root=root)
        _emit(derived, args.format)
    elif cmd == "listings":
        rows = listings_asof(args.asof, root=root, venue=args.venue)
        if args.live_only:
            rows = [r for r in rows if r["is_live"]]
        _emit(rows, args.format)
    elif cmd == "corporate-actions":
        _emit(corporate_actions_for(args.symbol, args.asof, root=root), args.format)
    elif cmd == "calendar":
        _emit(trading_sessions(args.start, args.end, root=root, venue=args.venue), args.format)
    elif cmd == "ingest-sample":
        from .sample import materialize_sample

        summary = materialize_sample(root=root, symbol=args.symbol)
        _emit([summary], args.format)
    else:  # pragma: no cover - argparse enforces a valid subcommand
        return 2
    return 0


if __name__ == "__main__":  # `python -m quant.market_store ...`
    raise SystemExit(main())
