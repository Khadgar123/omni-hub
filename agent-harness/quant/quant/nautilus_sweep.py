"""Strategy iteration + improvement on the nautilus base.

Sweep a param grid -> run each config through a real nautilus backtest (via the
adapter) -> gate on OUT-OF-SAMPLE performance (the practitioner-preferred test):
rank configs by in-sample per-trade Sharpe, then keep only the in-sample winner
if it GENERALIZES out-of-sample (positive OOS Sharpe, PSR above a floor, limited
degradation). This is the improvement loop: grid -> honest OOS gate -> adopt only
what survives.

(CSCV-PBO needs time-aligned per-bar returns; nautilus gives per-trade returns,
so PBO lives in the pure-python per-bar sweep. Here the gate is OOS+PSR, which the
validation research ranked ABOVE CSCV for low-parameter rule systems.)
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys

from quant.backtest import metrics as M
from quant.backtest.sweep import DEFAULT_GRIDS, config_grid


def _sr(rets):
    if len(rets) < 2:
        return 0.0
    sd = statistics.pstdev(rets)
    return statistics.fmean(rets) / sd if sd > 0 else 0.0


def _run_config(strategy_id, symbol, root, start, end, tf, cfg):
    """Run ONE config in a fresh SUBPROCESS — nautilus's Rust logger is a process
    global, so only one BacktestEngine may exist per process. Parse the worker's
    one-line ``RESULT:<json>`` output."""
    cmd = [sys.executable, "-m", "quant.nautilus_run", "--strategy", strategy_id,
           "--symbol", symbol, "--tf", tf, "--params", json.dumps(cfg), "--with-returns"]
    if root:
        cmd += ["--root", str(root)]
    if start:
        cmd += ["--from", start]
    if end:
        cmd += ["--to", end]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        return {"returns": [], "error": "timeout"}
    for line in r.stdout.splitlines():
        if line.startswith("RESULT:"):
            try:
                return json.loads(line[len("RESULT:"):])
            except Exception:
                break
    return {"returns": [], "error": (r.stderr or r.stdout or "no RESULT")[-200:]}


def sweep(strategy_id, symbol="BTCUSDT", *, root=None, start=None, end=None, tf="1h",
          grid=None, oos_frac=0.3, psr_min=0.6, min_trades=4):
    grid = grid if grid is not None else DEFAULT_GRIDS.get(strategy_id, {})
    configs = config_grid(grid) if grid else [{}]
    rows = []
    for cfg in configs:
        res = _run_config(strategy_id, symbol, root, start, end, tf, cfg)
        rets = res.get("returns", []) or []
        s = max(1, int(len(rets) * (1 - oos_frac))) if len(rets) >= 2 else len(rets)
        rows.append({
            "config": cfg, "total_return_pct": res.get("total_return_pct"),
            "n_returns": len(rets), "sr_is": _sr(rets[:s]), "sr_oos": _sr(rets[s:]),
            "oos_returns": rets[s:],
        })
    tradeable = [r for r in rows if r["n_returns"] >= min_trades]
    if not tradeable:
        return {"strategy": strategy_id, "symbol": symbol, "n_configs": len(configs),
                "viable": False, "reject_reasons": ["too few trades in all configs"]}
    best = max(tradeable, key=lambda r: r["sr_is"])
    psr_oos = M.probabilistic_sharpe(best["oos_returns"]) if len(best["oos_returns"]) >= 3 else None
    oos_deg = ((best["sr_is"] - best["sr_oos"]) / best["sr_is"]) if best["sr_is"] > 0 else None
    reasons = []
    if best["sr_oos"] <= 0:
        reasons.append("OOS Sharpe <= 0")
    if psr_oos is None or psr_oos < psr_min:
        reasons.append(f"OOS PSR={None if psr_oos is None else round(psr_oos, 3)} (need >{psr_min})")
    if oos_deg is not None and oos_deg > 0.5:
        reasons.append(f"OOS degradation={round(oos_deg, 2)}")
    return {
        "strategy": strategy_id, "symbol": symbol, "n_configs": len(configs),
        "best_config": best["config"], "best_total_return_pct": best["total_return_pct"],
        "best_sr_is": round(best["sr_is"], 4), "best_sr_oos": round(best["sr_oos"], 4),
        "oos_psr": psr_oos, "oos_degradation": oos_deg,
        "viable": len(reasons) == 0, "reject_reasons": reasons,
    }


def main(argv=None):
    import argparse
    import json
    import sys
    from pathlib import Path

    p = argparse.ArgumentParser(prog="quant.nautilus_sweep")
    p.add_argument("--strategy", required=True)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--root", default=None)
    p.add_argument("--from", dest="start", default=None)
    p.add_argument("--to", dest="end", default=None)
    p.add_argument("--tf", default="1h")
    p.add_argument("--grid", default=None, help="JSON grid; default per strategy")
    a = p.parse_args(argv)
    grid = json.loads(a.grid) if a.grid else None
    out = sweep(a.strategy, a.symbol, root=Path(a.root).expanduser() if a.root else None,
                start=a.start, end=a.end, tf=a.tf, grid=grid)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
