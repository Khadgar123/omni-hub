#!/usr/bin/env python3
"""Daily crypto vol+trend reference indicator — scheduled regime snapshot.

Shells the quant venv's regime read (SCHEMA §9: ``python -m quant.market_state``)
for each watched symbol and records the multi-timeframe **vol+trend reference
indicator** — regime label, composite bias, trend direction/strength (ADX +
EMA-slope/ATR), realized-vol bucket, and the CUSUM change-point veto. omni-hub
stays stdlib-only and never imports ``quant``; the quant analysis runs in the
quant venv and is reached purely by subprocess (the shell-out seam).

Freshness: the stored bars can lag, so each symbol is read **live** first
(``--live``, public Coinbase/Kraken candles), falling back to live's other venue
and then to the stored bars. Every record is stamped with its ``source`` and
``stale_days`` so a consumer never mistakes a stale reading for a current one.

Outputs (the omni integration points):
  * ``.omni/briefs/quant-YYYY-MM-DD.md``     — human daily brief
  * ``.omni/quant/regime-indicator.jsonl``   — append-only indicator feed (1 line/symbol/run)
  * ``.omni/quant/regime-latest.json``       — latest snapshot (all symbols)

Fail-soft: a symbol that can't be read is recorded as unavailable rather than
aborting the run. Override symbols via ``DAILY_QUANT_SYMBOLS`` (comma-separated);
the quant interpreter via ``QUANT_PY`` (default: best-effort discovery).

This is NOT a trade signal and NOT a forecast — it is the mechanical state of the
regime committee. No order is ever placed here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

_DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
_MICROS = 1_000_000
# fields copied verbatim from the MarketState (SCHEMA §9) into the indicator record
_INDICATOR_FIELDS = ("regime_label", "composite_bias", "direction", "vol_bucket", "stand_down")
# live-first, then stored — each (source-tag, market_state kwargs). Binance fapi
# leads because it is reachable from CN/Asia where Coinbase/Kraken are geo-blocked.
_SOURCES = (
    ("live:binance", {"live": True, "venue": "binance"}),
    ("live:coinbase", {"live": True, "venue": "coinbase"}),
    ("live:kraken", {"live": True, "venue": "kraken"}),
    ("stored", {"live": False, "venue": "coinbase"}),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _quant_py() -> str | None:
    """Resolve the quant venv interpreter (the one with duckdb + ``quant``).

    ``QUANT_PY`` wins; else the sibling ``python`` of a ``quant-market-store``
    console script on PATH; else None (caller records the symbol unavailable)."""
    env = os.environ.get("QUANT_PY")
    if env and Path(env).expanduser().exists():
        return str(Path(env).expanduser())
    cli = shutil.which(os.environ.get("QUANT_CLI", "quant-market-store"))
    if cli:
        sib = Path(cli).resolve().parent / "python"
        if sib.exists():
            return str(sib)
    return None


def _run_market_state(symbol: str, *, quant_py: str, live: bool, venue: str,
                      timeout: float = 45.0) -> dict:
    """One subprocess call to the quant regime CLI; returns the MarketState dict."""
    cmd = [quant_py, "-m", "quant.market_state", "--symbol", symbol]
    if live:
        cmd += ["--live", "--venue", venue]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"rc={proc.returncode}: {(proc.stderr or '').strip()[:140]}")
    data = json.loads(proc.stdout)
    if not isinstance(data, dict) or "composite_bias" not in data:
        raise ValueError("unexpected market_state payload")
    return data


def _stale_days(as_of_micros, now: datetime) -> float | None:
    if not isinstance(as_of_micros, (int, float)) or as_of_micros <= 0:
        return None
    return round((now.timestamp() - as_of_micros / _MICROS) / 86400, 2)


def _to_record(ms: dict, *, source: str, now: datetime) -> dict:
    """Project a MarketState (§9) into a flat, append-friendly indicator record."""
    as_of = ms.get("as_of")
    htf = ms.get("htf") or {}
    rec = {
        "symbol": ms.get("symbol"),
        "as_of": as_of,
        "as_of_utc": (datetime.fromtimestamp(as_of / _MICROS, UTC).isoformat()
                      if isinstance(as_of, (int, float)) and as_of > 0 else None),
        "source": source,
        "stale_days": _stale_days(as_of, now),
        "adx": htf.get("adx"),
        "slope_per_atr": htf.get("slope_per_atr"),
    }
    for k in _INDICATOR_FIELDS:
        rec[k] = ms.get(k)
    return rec


def _default_fetch(symbol: str, *, quant_py: str | None, now: datetime) -> dict:
    """live coinbase → live kraken → stored; returns the indicator record.

    Raises only when EVERY source fails (caller marks the symbol unavailable)."""
    if quant_py is None:
        raise FileNotFoundError("quant venv not found (set QUANT_PY)")
    last: Exception | None = None
    for source, kw in _SOURCES:
        try:
            ms = _run_market_state(symbol, quant_py=quant_py, **kw)
            return _to_record(ms, source=source, now=now)
        except Exception as exc:  # noqa: BLE001 - cascade to the next source
            last = exc
    raise RuntimeError(f"all sources failed (last {type(last).__name__}: {str(last)[:90]})")


def collect_indicators(symbols, *, fetch, now: datetime | None = None) -> list[dict]:
    """Run ``fetch(symbol)`` per symbol, fail-soft. ``fetch`` is injected so this
    is unit-testable with no venv and no network."""
    now = now or datetime.now(UTC)
    out: list[dict] = []
    for sym in symbols:
        try:
            out.append(fetch(sym))
        except Exception as exc:  # noqa: BLE001 - one symbol must not abort the run
            out.append({"symbol": sym, "error": f"{type(exc).__name__}: {str(exc)[:100]}"})
    return out


def _framework(symbol: str, *, quant_py: str | None, timeout: float = 70.0) -> dict:
    """Shell the quant venv's unified framework read (JSON, incl. the human ``narrative``)."""
    if quant_py is None:
        raise FileNotFoundError("quant venv not found (set QUANT_PY)")
    proc = subprocess.run([quant_py, "-m", "quant.framework", "--symbol", symbol, "--json"],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"rc={proc.returncode}: {(proc.stderr or '').strip()[:140]}")
    return json.loads(proc.stdout)


def collect_framework(symbols, *, fetch) -> list[dict]:
    """Per-symbol framework edge-audit read; fail-soft. ``fetch`` injected for testing."""
    out: list[dict] = []
    for sym in symbols:
        try:
            out.append(fetch(sym))
        except Exception as exc:  # noqa: BLE001
            out.append({"symbol": sym, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
    return out


_BIAS_GLYPH = {"long": "▲ long", "short": "▼ short", "flat": "■ flat"}


def render_brief(records, *, today: str) -> str:
    """Markdown daily brief for the human surface."""
    lines = [f"# Crypto vol+trend reference — {today}", ""]
    for r in records:
        sym = r.get("symbol", "?")
        if "error" in r:
            lines.append(f"- **{sym}**: unavailable ({r['error']})")
            continue
        bias = _BIAS_GLYPH.get(r.get("composite_bias"), str(r.get("composite_bias")))
        stand_down = " · ⚠ STAND-DOWN" if r.get("stand_down") else ""
        stale = r.get("stale_days")
        fresh = (f"{r.get('source')}, {stale:g}d old" if isinstance(stale, (int, float))
                 else str(r.get("source")))
        adx = r.get("adx")
        adx_s = f"ADX {adx:.0f}" if isinstance(adx, (int, float)) else "ADX —"
        lines.append(
            f"- **{sym}**: {bias} · regime *{r.get('regime_label')}* "
            f"· vol {r.get('vol_bucket')} · {adx_s}{stand_down}  _( {fresh} )_"
        )
    lines += ["",
              "_机械指标(ADX / EMA 斜率 / 已实现波动率桶 / CUSUM 变点)的状态读数，"
              "非投资建议、非涨跌预测。_"]
    return "\n".join(lines) + "\n"


def render_framework(framework) -> str:
    """A readable '解读' (edge-audit) section — the narrative, not a pile of indicators."""
    if not framework:
        return ""
    lines = ["", "## 解读 (edge-audit)", ""]
    for fr in framework:
        sym = fr.get("symbol", "?")
        if "error" in fr:
            lines.append(f"- **{sym}**: 不可用 ({fr['error']})")
        else:
            lines.append(f"- **{sym}**: {fr.get('narrative', '(no narrative)')}")
    return "\n".join(lines) + "\n"


def write_outputs(records, *, root: Path, today: str, now: datetime, framework=None) -> str:
    """Write the omni integration artifacts; return the brief text. ``framework`` (optional)
    appends the readable edge-audit narrative + a framework-latest.json feed."""
    omni = root / ".omni"
    briefs = omni / "briefs"
    qdir = omni / "quant"
    briefs.mkdir(parents=True, exist_ok=True)
    qdir.mkdir(parents=True, exist_ok=True)

    brief = render_brief(records, today=today) + render_framework(framework)
    (briefs / f"quant-{today}.md").write_text(brief, encoding="utf-8")

    run_ts = now.isoformat()
    with open(qdir / "regime-indicator.jsonl", "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps({"run_ts": run_ts, **r}, ensure_ascii=False, default=str) + "\n")

    (qdir / "regime-latest.json").write_text(
        json.dumps({"run_ts": run_ts, "date": today, "indicators": records},
                   ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    if framework:
        (qdir / "framework-latest.json").write_text(
            json.dumps({"run_ts": run_ts, "date": today, "reads": framework},
                       ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    return brief


def main() -> int:
    symbols = [s.strip() for s in os.environ.get("DAILY_QUANT_SYMBOLS", "").split(",") if s.strip()] \
        or _DEFAULT_SYMBOLS
    now = datetime.now(UTC)
    today = date.today().isoformat()
    quant_py = _quant_py()
    records = collect_indicators(
        symbols, fetch=lambda s: _default_fetch(s, quant_py=quant_py, now=now), now=now
    )
    framework = collect_framework(symbols, fetch=lambda s: _framework(s, quant_py=quant_py))
    brief = write_outputs(records, root=_repo_root(), today=today, now=now, framework=framework)
    sys.stdout.write(brief)
    return 0


if __name__ == "__main__":
    sys.exit(main())
