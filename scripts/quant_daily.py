#!/usr/bin/env python3
"""Daily quant brief — scheduled snapshot of watched symbols via the quant CLI.

Shells the quant module's CLI (SCHEMA §7: ``quant-market-store bars ...``),
which runs in the QUANT venv (duckdb); omni-hub stays stdlib-only and never
imports quant.  Fail-soft: if the quant venv / CLI isn't installed, the brief
records that per-symbol rather than crashing.  Writes
``.omni/briefs/quant-YYYY-MM-DD.md``.

This is the SCHEDULING seam: launchd ``com.omni-hub.quant`` runs it daily so
the quant plane participates in the big loop's cron cadence.  (Regime/strategy
scans deepen once the quant CLI exposes those subcommands — betafish side.)

Override symbols via ``DAILY_QUANT_SYMBOLS`` (comma-separated); the CLI binary
via ``QUANT_CLI`` (default ``quant-market-store``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

_DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_fetch(symbol: str, *, freq: str = "1d") -> list[dict]:
    cli = os.environ.get("QUANT_CLI", "quant-market-store")
    if shutil.which(cli) is None:
        raise FileNotFoundError(f"{cli} not on PATH (quant venv not installed)")
    proc = subprocess.run(
        [cli, "--format", "json", "bars", "--symbol", symbol, "--freq", freq,
         "--start", "1970-01-01", "--end", "2100-01-01"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{cli} rc={proc.returncode}: {proc.stderr[:120]}")
    data = json.loads(proc.stdout or "[]")
    return data if isinstance(data, list) else []


def build_quant_brief(symbols, *, fetch=_default_fetch, today: str | None = None) -> str:
    """Render a daily brief for ``symbols``.  ``fetch(symbol)`` returns a list of
    bar rows (SCHEMA §4); injected for testing.  Fail-soft per symbol."""
    day = today or date.today().isoformat()
    lines = [f"# Quant daily brief — {day}", ""]
    for sym in symbols:
        try:
            bars = fetch(sym)
        except Exception as exc:  # noqa: BLE001 - one symbol must not abort the brief
            lines.append(f"- **{sym}**: unavailable ({type(exc).__name__}: {str(exc)[:80]})")
            continue
        if not bars:
            lines.append(f"- **{sym}**: no bars")
            continue
        close = bars[-1].get("close")
        first = bars[0].get("close")
        chg = (
            (close - first) / first * 100
            if (isinstance(close, (int, float)) and isinstance(first, (int, float)) and first)
            else None
        )
        tail = f" ({chg:+.1f}% over {len(bars)} bars)" if chg is not None else ""
        lines.append(f"- **{sym}**: close {close}{tail}")
    return "\n".join(lines) + "\n"


def main() -> int:
    symbols = [
        s.strip() for s in os.environ.get("DAILY_QUANT_SYMBOLS", "").split(",") if s.strip()
    ] or _DEFAULT_SYMBOLS
    brief = build_quant_brief(symbols)
    out = _repo_root() / ".omni" / "briefs"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"quant-{date.today().isoformat()}.md").write_text(brief, encoding="utf-8")
    sys.stdout.write(brief)
    return 0


if __name__ == "__main__":
    sys.exit(main())
