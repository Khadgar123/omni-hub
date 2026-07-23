"""A tiny, deterministic sample dataset for tests + demos.

Canonical data lives here as Python (so tests import it directly and no clock
/ network is needed).  ``materialize_sample`` writes it into a store;
``dump_ndjson`` emits the human-facing ``sample/trades.ndjson`` artifact.

The sample is designed to exercise the interesting paths:
  * two UTC days of ``DEMO`` trades (so 1m + 1d bars are non-trivial),
  * a 2:1 split on day 2 (so day-1 bars back-adjust by /2),
  * a delisted symbol ``OLDCO`` (anti-survivorship retention),
  * an equity trading calendar with a holiday + weekend.
"""

from __future__ import annotations

import json
from pathlib import Path

from .market_store import (
    DEFAULT_ROOT,
    parse_ts,
)

SAMPLE_SYMBOL = "DEMO"
SAMPLE_VENUE = "SIM"
CALENDAR_VENUE = "XNYS"

# (iso-datetime UTC, price, size, side, trade_id)
_RAW_TRADES = [
    # 2026-01-02 — three trades in [00:00,00:01), two in [00:01,00:02), one in [00:02,00:03)
    ("2026-01-02T00:00:05+00:00", 100.0, 1.0, "buy", "D1"),
    ("2026-01-02T00:00:35+00:00", 101.0, 2.0, "buy", "D2"),
    ("2026-01-02T00:00:50+00:00", 99.5, 1.5, "sell", "D3"),
    ("2026-01-02T00:01:10+00:00", 102.0, 1.0, "buy", "D4"),
    ("2026-01-02T00:01:40+00:00", 103.0, 0.5, "buy", "D5"),
    ("2026-01-02T00:02:05+00:00", 100.5, 3.0, "sell", "D6"),
    # 2026-01-03 — post-split day
    ("2026-01-03T00:00:10+00:00", 51.0, 4.0, "buy", "D7"),
    ("2026-01-03T00:00:50+00:00", 52.0, 2.0, "sell", "D8"),
    ("2026-01-03T00:01:30+00:00", 50.0, 1.0, "buy", "D9"),
]


def sample_trades(symbol: str = SAMPLE_SYMBOL) -> list[dict]:
    """Sample trade events in the frozen ``trades`` schema (exchange_ts micros)."""

    rows: list[dict] = []
    for iso, price, size, side, tid in _RAW_TRADES:
        ts = parse_ts(iso)
        rows.append(
            {
                "symbol": symbol,
                "exchange_ts": ts,
                "receive_ts": ts,
                "sequence": int(tid[1:]),
                "price": price,
                "size": size,
                "side": side,
                "trade_id": tid,
                "fee": 0.0,
                "slippage": 0.0,
                "order_state": "",
                "venue": SAMPLE_VENUE,
            }
        )
    return rows


def sample_corporate_actions() -> list[dict]:
    """A 2:1 split for DEMO on 2026-01-03 (day-1 bars back-adjust by /2)."""

    return [
        {
            "symbol": SAMPLE_SYMBOL,
            "event_date": "2026-01-03",
            "type": "split",
            "ratio": 2.0,
            "cash_amount": 0.0,
            "new_symbol": "",
            "notes": "2:1 forward split (sample)",
        }
    ]


def sample_listings() -> list[dict]:
    """Symbol master incl. a delisted symbol (retained for anti-survivorship)."""

    return [
        {
            "symbol": SAMPLE_SYMBOL, "name": "Demo Corp", "venue": SAMPLE_VENUE,
            "list_date": "2026-01-01", "delist_date": "", "status": "active",
            "asset_class": "crypto",
        },
        {
            "symbol": "OLDCO", "name": "Old Company", "venue": SAMPLE_VENUE,
            "list_date": "2024-01-01", "delist_date": "2025-06-30",
            "status": "delisted", "asset_class": "equity",
        },
    ]


def sample_calendar() -> list[dict]:
    """A small equity calendar: New-Year holiday + a weekend closure."""

    def _row(date: str, is_open: bool):
        return {
            "venue": CALENDAR_VENUE,
            "date": date,
            "is_open": is_open,
            "open_ts": parse_ts(date + "T14:30:00+00:00") if is_open else 0,
            "close_ts": parse_ts(date + "T21:00:00+00:00") if is_open else 0,
            "session": "regular" if is_open else "closed",
        }

    return [
        _row("2026-01-01", False),  # New Year's Day (holiday)
        _row("2026-01-02", True),   # Fri
        _row("2026-01-03", False),  # Sat
        _row("2026-01-04", False),  # Sun
        _row("2026-01-05", True),   # Mon
    ]


def materialize_sample(*, root: Path | str = DEFAULT_ROOT, symbol: str | None = None) -> dict:
    """Write the sample trades + reference tables into ``root``.  Returns a summary."""

    from . import market_store as ms

    sym = symbol or SAMPLE_SYMBOL
    trade_rows = sample_trades(sym)
    trade_paths = ms.write_trades(trade_rows, root=root)
    ca_path = ms.write_corporate_actions(sample_corporate_actions(), root=root)
    li_path = ms.write_listings(sample_listings(), root=root)
    cal_path = ms.write_calendar(sample_calendar(), root=root)
    return {
        "root": str(root),
        "symbol": sym,
        "trades": len(trade_rows),
        "trade_partitions": [str(p) for p in trade_paths],
        "corporate_actions": str(ca_path),
        "listings": str(li_path),
        "calendar": str(cal_path),
    }


def dump_ndjson(path: Path | str) -> Path:
    """Emit the sample trades as the human-facing ``trades.ndjson`` artifact."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for row in sample_trades():
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return target


if __name__ == "__main__":  # regenerate the committed sample artifact
    import sys

    out = dump_ndjson(Path(__file__).resolve().parents[1] / "sample" / "trades.ndjson")
    sys.stdout.write(f"wrote {out}\n")
