"""Single-user quant market store — DuckDB + Hive-partitioned Parquet.

SCAFFOLD / GATED.  Separate sub-instance with its own deps (see
``pyproject.toml``); the main omni-hub repo stays stdlib-only.  Build the
ingestion out only when real quant work starts.

Design (see ``README.md``):
  * trades/quotes/orderbook events are the TRUTH; OHLCV bars are DERIVED.
  * Hive layout:  ``<root>/<table>/symbol=<SYM>/date=<YYYY-MM-DD>/part.parquet``
  * DuckDB globs the Parquet files in-process (no server; billion-row on a
    laptop).

The pure helpers below (``partition_path``, ``bars_from_trades``) have NO
third-party deps and are unit-testable as-is.  The Parquet/DuckDB I/O
lazy-imports duckdb/pyarrow so this module imports cleanly without them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

# Default data root — deliberately OUTSIDE the knowledge vault (vault/ is the
# raw→evidence→wiki harness; OHLCV numerics do not belong there).
DEFAULT_ROOT = Path("~/quant/market").expanduser()


def partition_path(root: Path | str, table: str, symbol: str, date: str) -> Path:
    """Hive partition path for one (table, symbol, date)."""

    return (
        Path(root) / table / f"symbol={symbol}" / f"date={date}" / "part.parquet"
    )


def bars_from_trades(
    trades: Iterable[dict],
    *,
    interval_seconds: int = 60,
) -> list[dict]:
    """Derive OHLCV bars from raw trade events (the TRUTH → derived step).

    Pure-python (no deps) so the core derivation is testable + dependency-free.
    Each trade is ``{"ts": <epoch_seconds>, "price": float, "size": float}``.
    Returns bars sorted by bucket start: ``{bucket, open, high, low, close,
    volume, trades}``.
    """

    buckets: dict[int, dict] = {}
    for t in sorted(trades, key=lambda x: x["ts"]):
        bucket = int(t["ts"]) // interval_seconds * interval_seconds
        price = float(t["price"])
        size = float(t.get("size", 0.0))
        bar = buckets.get(bucket)
        if bar is None:
            buckets[bucket] = {
                "bucket": bucket, "open": price, "high": price,
                "low": price, "close": price, "volume": size, "trades": 1,
            }
        else:
            bar["high"] = max(bar["high"], price)
            bar["low"] = min(bar["low"], price)
            bar["close"] = price
            bar["volume"] += size
            bar["trades"] += 1
    return [buckets[k] for k in sorted(buckets)]


# --------------------------------------------------------------------------
# Parquet / DuckDB I/O — lazy-imports so the module loads without the deps.
# These are thin, deliberately minimal entry points; flesh out when quant
# work actually starts (the GATE).
# --------------------------------------------------------------------------


def write_parquet(path: Path | str, rows: list[dict]) -> Path:
    """Write ``rows`` to a Parquet partition file (creates parent dirs)."""

    import pyarrow as pa
    import pyarrow.parquet as pq

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), target)
    return target


def query(sql: str, root: Path | str = DEFAULT_ROOT):
    """Run a DuckDB SQL query over the Parquet store; returns an Arrow table.

    Example::

        query("SELECT * FROM read_parquet('bars_1d/symbol=NVDA/**/*.parquet')")
    """

    import duckdb

    con = duckdb.connect()
    con.execute(f"SET FILE_SEARCH_PATH='{Path(root)}'")
    return con.execute(sql).arrow()


if __name__ == "__main__":  # tiny self-check of the dependency-free core
    sample = [
        {"ts": 0, "price": 10.0, "size": 1},
        {"ts": 30, "price": 11.0, "size": 2},
        {"ts": 61, "price": 9.0, "size": 1},
    ]
    bars = bars_from_trades(sample, interval_seconds=60)
    assert bars[0] == {
        "bucket": 0, "open": 10.0, "high": 11.0, "low": 10.0,
        "close": 11.0, "volume": 3, "trades": 2,
    }, bars
    assert len(bars) == 2 and bars[1]["bucket"] == 60
    print("market_store core self-check OK:", bars)
