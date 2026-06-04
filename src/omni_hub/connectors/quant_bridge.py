"""Subprocess bridge to the ``quant`` market-data CLI — the SCHEMA.md shell-out seam.

The stdlib core **never imports** ``quant`` (duckdb/pyarrow are not main-repo deps). Instead it
shells out to the quant package's own interpreter and parses the JSON that ``quant.market_store``
prints (``--format json`` is the default). Stays silent on a missing quant install — graceful
``"not_installed"`` so callers degrade to whatever they had before (same pattern as
``trafilatura_bridge``).

Config (env, all optional):
  ``OMNI_QUANT_PYTHON``  interpreter that has the quant package + duckdb (default: ``python3``)
  ``OMNI_QUANT_DIR``     dir holding the ``quant`` package (default: ``<repo>/agent-harness/quant``)

Contract — every fn returns ``(payload, status)`` with status in the stable taxonomy:
  ``ok`` | ``not_installed`` | ``timeout`` | ``error`` | ``empty``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def _quant_dir() -> Path:
    """Directory that contains the ``quant`` package (so ``-m quant.market_store`` resolves)."""
    env = os.environ.get("OMNI_QUANT_DIR")
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve()
    for parent in here.parents:                                    # walk up to the repo root
        cand = parent / "agent-harness" / "quant"
        if (cand / "quant" / "market_store.py").exists():
            return cand
    return Path("agent-harness/quant")


def _quant_python() -> str:
    return os.environ.get("OMNI_QUANT_PYTHON") or "python3"


def has_quant() -> bool:
    """True iff the quant package dir + an interpreter are resolvable on this machine."""
    py = _quant_python()
    if shutil.which(py) is None and not Path(py).exists():
        return False
    return (_quant_dir() / "quant" / "market_store.py").exists()


def _run(args: list[str], *, timeout_sec: float) -> tuple[object, str]:
    """Invoke ``quant.market_store --format json <args>`` and parse stdout JSON."""
    if not has_quant():
        return None, "not_installed"
    cmd = [_quant_python(), "-m", "quant.market_store", "--format", "json", *args]
    try:
        result = subprocess.run(cmd, cwd=str(_quant_dir()), capture_output=True,
                                text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception:                                              # noqa: BLE001
        return None, "error"
    if result.returncode != 0:
        return None, "error"
    raw = (result.stdout or "").strip()
    if not raw:
        return None, "empty"
    try:
        return json.loads(raw), "ok"
    except json.JSONDecodeError:
        return None, "error"


def market_bars(symbol: str, freq: str, start, end, *, timeout_sec: float = 30.0) -> tuple[list, str]:
    """OHLCV bars for ``[start, end]`` as a list of row dicts. ``([], status)`` when not ``ok``."""
    payload, status = _run(
        ["bars", "--symbol", symbol, "--freq", freq, "--start", str(start), "--end", str(end)],
        timeout_sec=timeout_sec,
    )
    if status == "ok":
        return (payload, "ok") if isinstance(payload, list) else ([], "error")
    return [], status


def last_price(symbol: str, asof, *, timeout_sec: float = 15.0) -> tuple[dict, str]:
    """Last trade price as of ``asof``. ``({}, status)`` when not ``ok``."""
    payload, status = _run(["last-price", "--symbol", symbol, "--asof", str(asof)],
                           timeout_sec=timeout_sec)
    if status == "ok" and isinstance(payload, list) and payload:
        return payload[0], "ok"
    return {}, status if status != "ok" else "error"
