"""Compact many small ``part-*.parquet`` within each date partition into one zstd file.

Streaming/append writes leave 10-25 tiny files per day in some tiers (e.g. bars_15s), which
slows scans and wastes space. This merges each ``date=`` partition down to a single
``part-00000.parquet``, deduping by ``bucket_ts``. Idempotent: a single-file partition is
skipped, so re-running is a no-op.

CLI:
  python -m quant.compact --symbol BTCUSDT --freq 15s
  python -m quant.compact --symbol BTCUSDT --freq 15s --root /custom/market
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import market_store as ms


def compact_dir(date_dir: Path) -> int:
    """Merge all ``*.parquet`` in one ``date=`` dir into a single zstd file (dedup by
    ``bucket_ts``). Returns the number of source files merged (0 = skipped, already ≤1 file)."""
    parts = sorted(p for p in date_dir.glob("*.parquet") if not p.name.startswith("_"))
    if len(parts) <= 1:
        return 0
    import duckdb

    tmp = date_dir / "_compact.tmp.parquet"
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect()
    try:
        con.execute(
            "COPY (SELECT * EXCLUDE(rn) FROM ("
            "  SELECT *, row_number() OVER (PARTITION BY bucket_ts ORDER BY bucket_ts) AS rn"
            f"  FROM read_parquet({ms._sql_file_list([str(p) for p in parts])})"
            ") WHERE rn = 1 ORDER BY bucket_ts)"
            f" TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)"
        )
    finally:
        con.close()
    for p in parts:
        p.unlink()
    tmp.rename(date_dir / "part-00000.parquet")
    return len(parts)


def compact(symbol: str, freq: str, *, root=ms.DEFAULT_ROOT) -> dict:
    """Compact every date partition of ``bars_<freq>/symbol=<symbol>``."""
    base = Path(root) / f"bars_{freq}" / f"symbol={symbol}"
    dirs = sorted(base.glob("date=*")) if base.is_dir() else []
    merged_dirs = merged_files = 0
    for d in dirs:
        n = compact_dir(d)
        if n:
            merged_dirs += 1
            merged_files += n
    return {"symbol": symbol, "freq": freq, "date_dirs": len(dirs),
            "compacted_dirs": merged_dirs, "files_merged": merged_files}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quant.compact", description="Merge small per-day part files.")
    p.add_argument("--symbol", required=True)
    p.add_argument("--freq", required=True)
    p.add_argument("--root", default=None, help="store root (default ~/quant/market)")
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    out = compact(a.symbol, a.freq, root=a.root or ms.DEFAULT_ROOT)
    print(json.dumps({"ok": True, **out}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
