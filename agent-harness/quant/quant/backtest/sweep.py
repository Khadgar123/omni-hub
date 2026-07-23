"""Parameter sweep + CSCV/OOS gate — the validation moat made actionable.

Resample ONCE, run the parity engine across a parameter grid, then judge the
WHOLE sweep with the validation layer rather than the in-sample leaderboard:

  * **PBO** (CSCV) over all configs — is the selection process overfit?
  * **Deflated Sharpe** of the IS-best config, deflated by the number of
    configs tried (the selection-bias correction).
  * **IS -> OOS Sharpe degradation** + **event-concentration** of the winner.

The verdict is deliberately strict: a config is ``viable`` only if it survives
deflation + overfitting checks AND has a positive OOS Sharpe — "don't trust the
backtest" encoded as a gate. With untuned defaults on real BTC/ETH this is
expected to REJECT, and that honest "no edge here" is the point.
"""

from __future__ import annotations

import itertools
import statistics

from quant.backtest import engine, metrics, validation
from quant.backtest.costs import CostModel

# strategy_id -> default parameter grid to sweep
DEFAULT_GRIDS = {
    "trend_donchian_v1": {"entry_lookback": [10, 15, 20, 30], "atr_mult": [1.5, 2.0, 3.0]},
    "tsmom_v1": {"lookback": [120, 240, 480, 720], "atr_mult": [2.0, 3.0]},
    "ma_cross_v1": {"fast": [20, 50], "slow": [100, 200], "atr_mult": [2.0, 3.0]},
    "range_bb_revert_v1": {"bb_k": [1.5, 2.0, 2.5], "rsi_floor": [25.0, 30.0, 35.0]},
    "zscore_revert_v1": {"lookback": [24, 48, 96], "z_entry": [-1.5, -2.0, -2.5]},
    "divergence_reversal_v1": {"window": [40, 60, 90], "atr_mult": [1.5, 2.0, 3.0], "exit_rsi": [50.0, 55.0, 60.0]},
    "squeeze_breakout_v1": {"squeeze_pctl": [0.2, 0.25, 0.35], "breakout_lookback": [15, 20, 30], "atr_mult": [2.0, 2.5, 3.0]},
}

# gate thresholds (strict)
PBO_MAX = 0.5
DSR_MIN = 0.95
EVENT_CONC_MAX = 0.7
OOS_DEGRADE_MAX = 0.5


def config_grid(grid: dict) -> list[dict]:
    """Cartesian product of a param grid -> list of kwargs dicts."""
    keys = list(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*[grid[k] for k in keys])]


def _sr(rets):
    """Per-period Sharpe (mean/stdev), un-annualized — the unit DSR/PBO use."""
    if len(rets) < 2:
        return 0.0
    sd = statistics.pstdev(rets)
    return statistics.fmean(rets) / sd if sd > 0 else 0.0


def sweep_configs(strategy_class, configs, bars, states, *, cost=None, equity0=10000.0,
                  symbol="BTCUSDT", oos_frac=0.3, n_groups=10):
    """Run a grid over pre-resampled ``bars`` + per-bar ``states`` and gate it.

    Pure of any data I/O (bars/states injected) so it's unit-testable; the
    ``sweep()`` wrapper does the real-data resample.
    """
    cost = cost or CostModel()
    state_for = lambda i: states[i]
    rows, perf = [], []
    for cfg in configs:
        res = engine.run_backtest(strategy_class(**cfg), bars, state_for=state_for,
                                  cost=cost, equity0=equity0, symbol=symbol)
        rets = metrics.returns_from_curve(res.equity_curve)
        perf.append(rets)
        s = max(1, min(int(len(rets) * (1 - oos_frac)), len(rets) - 1)) if len(rets) >= 2 else len(rets)
        rows.append({
            "config": cfg,
            "sr_is": _sr(rets[:s]),
            "sr_oos": _sr(rets[s:]),
            "returns": rets,
            "n_trades": len(res.trades),
            "trades": res.trades,
        })

    pbo_res = (validation.probability_of_backtest_overfitting(perf, n_groups=n_groups)
               if len(configs) >= 2 else {"pbo": None})
    pbo = pbo_res.get("pbo")
    best = max(rows, key=lambda r: r["sr_is"]) if rows else None
    dsr = (validation.deflated_sharpe_from_returns(best["returns"], [r["sr_is"] for r in rows])
           if best and len(rows) >= 2 else None)
    evconc = validation.event_concentration(best["trades"]) if best else None
    oos_deg = validation.oos_sharpe_degradation(best["sr_is"], best["sr_oos"]) if best else None

    reasons = []
    if pbo is None or pbo >= PBO_MAX:
        reasons.append(f"PBO={pbo} (need <{PBO_MAX})")
    if not best or best["sr_oos"] <= 0:
        reasons.append("OOS Sharpe <= 0")
    if dsr is None or dsr < DSR_MIN:
        reasons.append(f"DSR={None if dsr is None else round(dsr, 3)} (need >{DSR_MIN})")
    if evconc is not None and evconc > EVENT_CONC_MAX:
        reasons.append(f"event-concentration={round(evconc, 2)} (need <{EVENT_CONC_MAX})")
    if oos_deg is not None and oos_deg > OOS_DEGRADE_MAX:
        reasons.append(f"OOS-degradation={round(oos_deg, 2)} (need <{OOS_DEGRADE_MAX})")

    return {
        "n_configs": len(configs),
        "best_config": best["config"] if best else None,
        "best_sr_is": best["sr_is"] if best else None,
        "best_sr_oos": best["sr_oos"] if best else None,
        "best_n_trades": best["n_trades"] if best else None,
        "pbo": pbo,
        "deflated_sharpe": dsr,
        "event_concentration": evconc,
        "oos_degradation": oos_deg,
        "viable": len(reasons) == 0,
        "reject_reasons": reasons,
    }


def sweep(strategy_id, symbol, *, root=None, start=None, end=None, htf="1d", confirm="4h",
          source="1s", grid=None, cost=None, equity0=10000.0, oos_frac=0.3, n_groups=10):
    """Real-data sweep: resample once, build per-bar regime states, gate the grid."""
    from quant import market_store, regime
    from quant import resample as rs
    from quant.backtest.harness import _build_states
    from quant.strategy.registry import by_id

    root = root if root is not None else market_store.DEFAULT_ROOT
    proto = by_id(strategy_id)
    cls = type(proto)
    tf = proto.timeframe
    bars = rs.resample(symbol, tf, root=root, source_interval=source, start=start, end=end)
    if not bars:
        return {"error": f"no {source} data for {symbol}", "strategy": strategy_id, "symbol": symbol}
    htf_bars = rs.resample(symbol, htf, root=root, source_interval=source, start=start, end=end)
    confirm_bars = rs.resample(symbol, confirm, root=root, source_interval=source, start=start, end=end)
    states = _build_states(bars, regime.classify_series(htf_bars),
                           regime.classify_series(confirm_bars), symbol)
    configs = config_grid(grid if grid is not None else DEFAULT_GRIDS.get(strategy_id, {}))
    if not configs:
        configs = [{}]
    out = sweep_configs(cls, configs, bars, states, cost=cost, equity0=equity0,
                        symbol=symbol, oos_frac=oos_frac, n_groups=n_groups)
    out.update(strategy=strategy_id, symbol=symbol, timeframe=tf, bars=len(bars))
    return out


def main(argv=None):
    import argparse
    import json
    import sys
    from pathlib import Path

    p = argparse.ArgumentParser(prog="quant.backtest.sweep", description=__doc__)
    p.add_argument("--strategy", required=True)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--root", default=None)
    p.add_argument("--from", dest="start", default=None)
    p.add_argument("--to", dest="end", default=None)
    p.add_argument("--htf", default="1d")
    p.add_argument("--confirm", default="4h")
    p.add_argument("--oos-frac", type=float, default=0.3)
    p.add_argument("--grid", default=None, help="JSON param grid; default per strategy")
    args = p.parse_args(argv)
    grid = json.loads(args.grid) if args.grid else None
    root = Path(args.root).expanduser() if args.root else None
    out = sweep(args.strategy, args.symbol, root=root, start=args.start, end=args.end,
                htf=args.htf, confirm=args.confirm, grid=grid, oos_frac=args.oos_frac)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
