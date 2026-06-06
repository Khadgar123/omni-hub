"""Real-data backtest harness — resample 1s -> strategy+HTF bars, wire per-bar
MTF regime, run the parity engine, summarize.

This is the empirical payoff: the SAME gated_evaluate + sizing as live, driven
over real 1s-derived bars, with a point-in-time MarketState per bar assembled
top-down (1d bias, 4h veto-to-flat) from precomputed regime tracks.

CLI:
    python -m quant.backtest.harness --strategy trend_donchian_v1 --symbol BTCUSDT \
        --from 2024-07 --to 2025-12 [--htf 1d --confirm 4h --root R]
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
from types import SimpleNamespace

from quant import regime, resample as resample_mod, structure
from quant.backtest import engine, metrics
from quant.backtest.costs import CostModel
from quant.market_state import _compose_bias
from quant.strategy.registry import by_id


def _asof(track, times, ts):
    """Latest regime dict in ``track`` with as_of <= ts (point-in-time)."""
    j = bisect.bisect_right(times, ts) - 1
    return track[j] if j >= 0 else None


def _build_states(strat_bars, htf_track, confirm_track, symbol,
                  sub_div_track=None, sub_window_us=0, op_atr_track=None):
    htimes = [r["as_of"] for r in htf_track]
    ctimes = [r["as_of"] for r in confirm_track]
    sub_times = [int(d["ts"]) for d in sub_div_track] if sub_div_track else []
    op_times = [t for t, _ in op_atr_track] if op_atr_track else []
    missing = SimpleNamespace(direction="flat", stand_down=False, insufficient=True, label="range")
    states = []
    for bar in strat_bars:
        ts = int(bar["bucket_ts"])
        h = _asof(htf_track, htimes, ts)
        c = _asof(confirm_track, ctimes, ts)
        hns = SimpleNamespace(**h) if h else missing
        cns = SimpleNamespace(**c) if c else missing
        # 区间套: the latest sub-level divergence that fired WITHIN this bar's window
        # (a nested confirmation), else None. Point-in-time (ts <= bar ts).
        sub_div = None
        if sub_div_track:
            j = bisect.bisect_right(sub_times, ts) - 1
            if j >= 0 and ts - sub_times[j] <= sub_window_us:
                sub_div = sub_div_track[j]
        # operating-level (confirm-TF) ATR, point-in-time — lets a strategy size its
        # stop/trail on the HOLDING timeframe instead of its tight entry-TF ATR.
        op_atr = None
        if op_atr_track:
            j = bisect.bisect_right(op_times, ts) - 1
            if j >= 0:
                op_atr = op_atr_track[j][1]
        states.append(SimpleNamespace(
            symbol=symbol,
            regime_label=(h["label"] if h else "range"),
            composite_bias=_compose_bias(hns, cns),
            stand_down=bool(hns.stand_down or cns.stand_down),
            sub_div=sub_div,
            op_atr=op_atr,
        ))
    return states


def run(strategy_id, symbol, *, root=None, start=None, end=None, htf="1d",
        confirm="4h", equity0=10000.0, cost=None, source="1s", report_path=None,
        live_from=None, sub=None):
    from quant import market_store
    root = root if root is not None else market_store.DEFAULT_ROOT
    strat = by_id(strategy_id)
    strat_bars = resample_mod.resample(symbol, strat.timeframe, root=root, source_interval=source, start=start, end=end)
    htf_bars = resample_mod.resample(symbol, htf, root=root, source_interval=source, start=start, end=end)
    confirm_bars = resample_mod.resample(symbol, confirm, root=root, source_interval=source, start=start, end=end)
    if not strat_bars:
        return None, {"error": f"no {source} data for {symbol} in range"}
    htf_track = regime.classify_series(htf_bars)
    confirm_track = regime.classify_series(confirm_bars)
    # 区间套 (nested-interval): a sub-level (< strategy tf) divergence track the
    # strategy can require as confirmation. None unless ``sub`` is given.
    sub_div_track = None
    sub_window_us = 0
    if sub is not None:
        sub_bars = resample_mod.resample(symbol, sub, root=root, source_interval=source, start=start, end=end)
        sub_div_track = [d for d in structure.divergence(sub_bars, left=3, right=3) if d["is_divergence"]]
        sub_window_us = market_store.freq_to_seconds(strat.timeframe) * 1_000_000
    # operating-level ATR track (the confirm TF, e.g. 4h) for HTF-anchored stops/trails
    from quant import features as _feat
    _catr = _feat.atr(confirm_bars, 14)
    op_atr_track = [(int(b["bucket_ts"]), _catr[i]) for i, b in enumerate(confirm_bars) if _catr[i] is not None]
    states = _build_states(strat_bars, htf_track, confirm_track, symbol, sub_div_track, sub_window_us, op_atr_track)
    res = engine.run_backtest(strat, strat_bars, state_for=lambda i: states[i],
                              equity0=equity0, cost=cost or CostModel(), symbol=symbol)
    m = metrics.summarize(res.equity_curve, res.trades, equity0=equity0,
                          prices=[float(b["close"]) for b in strat_bars])
    m["strategy"] = strategy_id
    m["symbol"] = symbol
    m["timeframe"] = strat.timeframe
    m["bars"] = len(strat_bars)
    if report_path is not None:
        from quant.backtest import report as report_mod
        track = [{"as_of": int(b["bucket_ts"]), "label": s.regime_label}
                 for b, s in zip(strat_bars, states)]
        lf = market_store.parse_ts(live_from) if live_from is not None else None
        report_mod.write_report(res, strat_bars, report_path, regime_track=track,
                                metrics_dict=m, title=f"{strategy_id} · {symbol}", live_from_us=lf)
        m["report"] = str(report_path)
    return res, m


def main(argv=None):
    p = argparse.ArgumentParser(prog="quant.backtest.harness", description=__doc__)
    p.add_argument("--strategy", required=True)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--root", default=None)
    p.add_argument("--from", dest="start", default=None)
    p.add_argument("--to", dest="end", default=None)
    p.add_argument("--htf", default="1d")
    p.add_argument("--confirm", default="4h")
    p.add_argument("--equity0", type=float, default=10000.0)
    p.add_argument("--source", default="1s")
    p.add_argument("--report", default=None, help="write a self-contained HTML report to this path")
    args = p.parse_args(argv)
    from pathlib import Path
    root = Path(args.root).expanduser() if args.root else None
    report_path = Path(args.report).expanduser() if args.report else None
    _, m = run(args.strategy, args.symbol, root=root, start=args.start, end=args.end,
               htf=args.htf, confirm=args.confirm, equity0=args.equity0, source=args.source,
               report_path=report_path)
    print(json.dumps(m, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
