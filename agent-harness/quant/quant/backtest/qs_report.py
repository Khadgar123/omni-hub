"""Optional QuantStats tear-sheet — the mature standard, as a SECOND artifact
beside the self-contained Plotly report.

Adopt-vs-build verdict (from the research): ADOPT QuantStats for the STANDARDIZED
risk/return tearsheet (rolling Sharpe, monthly heatmap, underwater, VaR/CVaR,
Kelly, tail ratio, risk-of-ruin) — battle-tested formulas you shouldn't re-derive —
and KEEP the hand-built Plotly report for the strategy-specific views (regime
bands, click-to-zoom bad-case, MAE/MFE) QuantStats can't produce. Two complementary
files, not one.

Guarded import: QuantStats drags IPython/scipy/matplotlib and is finicky on recent
pandas/numpy, so it is OPTIONAL — ``pip install quantstats-reloaded ipython``. The
self-contained Plotly report (quant.backtest.report) needs none of that.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path


def write_quantstats_report(result, path, *, title=None):
    """Emit a QuantStats HTML tearsheet from a BacktestResult's equity curve.
    Raises RuntimeError (with the install hint) if QuantStats isn't available."""
    try:
        import pandas as pd
        import quantstats as qs
    except Exception as e:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "QuantStats not installed — `pip install quantstats-reloaded ipython`") from e
    curve = result.equity_curve
    if len(curve) < 3:
        raise ValueError("need >= 3 equity points for a tearsheet")
    # tz-naive UTC index (QuantStats is fussy about tz-aware indices)
    idx = pd.DatetimeIndex([_dt.datetime.utcfromtimestamp(int(ts) / 1e6) for ts, _ in curve])
    eq = pd.Series([float(e) for _, e in curve], index=idx)
    eq = eq[~eq.index.duplicated(keep="last")].sort_index()
    # QuantStats is built for DAILY returns — resample intraday equity to daily
    # last (ffill gaps) so its plots/heatmaps don't choke on sparse intraday data.
    rets = eq.resample("1D").last().ffill().pct_change().dropna()
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        qs.reports.html(rets, output=str(p), title=title or "strategy tearsheet")
    except Exception:
        # QuantStats' full plot suite chokes on sparse / near-flat equity (a
        # barely-trading research strategy). Fall back to the metrics TABLE so an
        # artifact is always produced — and note: our pure tear-sheet
        # (quant.backtest.tearsheet) handles the same data without this fragility.
        try:
            mdf = qs.reports.metrics(returns=rets, mode="full", display=False)
            body = mdf.to_html()
        except Exception:
            body = "<p>returns too sparse for a QuantStats tearsheet</p>"
        p.write_text(f"<!doctype html><meta charset='utf-8'><h2>{title or 'tearsheet'}</h2>{body}",
                     encoding="utf-8")
    return p
