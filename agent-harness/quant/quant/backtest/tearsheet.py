"""Pure-stdlib tear-sheet analytics — the depth a bare metrics table misses.

Extra risk/return scalars (Sortino / Calmar / VaR / CVaR / Ulcer), the drawdown
(underwater) series, monthly-return buckets, rolling Sharpe, and per-trade
MAE/MFE (Maximum Adverse / Favorable Excursion — the "stops too tight / targets
too greedy / does the setup even have edge" diagnostic). All consume the
backtest's equity_curve / trades / bars; no numpy/pandas/quantstats.

QuantStats is the mature alternative for the *standardized* tearsheet (see
``quant.backtest.qs_report``); this keeps the report a single self-contained file
with zero extra dependencies, and owns the strategy-specific views (MAE/MFE)
QuantStats can't produce from a returns series.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Sequence

from quant.backtest.metrics import _SECONDS_PER_YEAR


def drawdown_series(curve: Sequence[tuple]) -> list[tuple]:
    """[(ts, drawdown_fraction ≤ 0)] — equity vs its running peak (underwater)."""
    out = []
    peak = float("-inf")
    for ts, e in curve:
        peak = max(peak, e)
        out.append((int(ts), (e - peak) / peak if peak > 0 else 0.0))
    return out


def rolling_sharpe(returns: Sequence[float], window: int, ppy: float) -> list:
    """Annualized Sharpe over a trailing ``window``; None during warmup."""
    out: list = [None] * len(returns)
    for i in range(window - 1, len(returns)):
        w = returns[i - window + 1: i + 1]
        sd = statistics.pstdev(w)
        out[i] = (statistics.fmean(w) / sd) * math.sqrt(ppy) if sd > 0 else 0.0
    return out


def monthly_returns(curve: Sequence[tuple]) -> dict:
    """{'YYYY-MM': return} — month-over-month from end-of-month equities (UTC).
    The first month measures intra-month (first→last bar)."""
    if len(curve) < 2:
        return {}
    first: dict = {}
    last: dict = {}
    for ts, e in curve:
        ym = datetime.fromtimestamp(int(ts) / 1e6, timezone.utc).strftime("%Y-%m")
        first.setdefault(ym, e)
        last[ym] = e
    months = sorted(last)
    out: dict = {}
    prev_eq = None
    for m in months:
        base = prev_eq if prev_eq is not None else first[m]
        out[m] = (last[m] / base - 1.0) if base > 0 else 0.0
        prev_eq = last[m]
    return out


def mae_mfe(trades: Sequence, bars: Sequence[dict]) -> list[dict]:
    """Per-trade Maximum Adverse / Favorable Excursion (long-only).
    MAE = worst draw from entry during the hold (≤0 frac); MFE = best run-up
    (≥0 frac). The classic scatter: winners with big MAE ⇒ stop too tight;
    losers with big MFE ⇒ target too greedy / exit too late."""
    out: list[dict] = []
    for t in trades:
        e0, e1, entry = int(t.entry_ts), int(t.exit_ts), float(t.entry)
        if entry <= 0:
            continue
        seg = [b for b in bars if e0 <= int(b.get("bucket_ts", 0)) <= e1]
        if not seg:
            continue
        lo = min(float(b["low"]) for b in seg)
        hi = max(float(b["high"]) for b in seg)
        out.append({"entry_ts": e0, "exit_ts": e1, "ret": float(t.return_pct),
                    "win": t.pnl > 0, "mae": lo / entry - 1.0, "mfe": hi / entry - 1.0})
    return out


def sortino(returns: Sequence[float], ppy: float) -> float:
    """Annualized Sortino (downside-deviation-adjusted return)."""
    if len(returns) < 2:
        return 0.0
    m = statistics.fmean(returns)
    dd = math.sqrt(sum(r * r for r in returns if r < 0) / len(returns))
    return (m / dd) * math.sqrt(ppy) if dd > 0 else 0.0


def calmar(total_return: float, max_dd: float, years: float) -> float:
    """CAGR / |max drawdown|."""
    if years <= 0 or max_dd >= 0:
        return 0.0
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0
    return cagr / abs(max_dd)


def value_at_risk(returns: Sequence[float], alpha: float = 0.05) -> float:
    """Historical VaR at ``alpha`` (the per-period loss quantile, ≤0)."""
    if len(returns) < 2:
        return 0.0
    s = sorted(returns)
    return s[max(0, min(len(s) - 1, int(alpha * len(s))))]


def cvar(returns: Sequence[float], alpha: float = 0.05) -> float:
    """Conditional VaR (expected shortfall) — mean of the worst ``alpha`` tail."""
    if len(returns) < 2:
        return 0.0
    s = sorted(returns)
    k = max(1, int(alpha * len(s)))
    return statistics.fmean(s[:k])


def ulcer_index(curve: Sequence[tuple]) -> float:
    """Ulcer Index — RMS of the drawdown series (pain depth+duration), in %."""
    dd = drawdown_series(curve)
    if not dd:
        return 0.0
    return math.sqrt(sum((d * 100.0) ** 2 for _, d in dd) / len(dd))


def tearsheet(result, bars: Sequence[dict], *, ppy: float | None = None) -> dict:
    """Bundle the extra analytics for a report. Returns scalars + series."""
    from quant.backtest.metrics import _ppy_from_curve, max_drawdown, returns_from_curve
    curve = result.equity_curve
    rets = returns_from_curve(curve)
    ppy = ppy or _ppy_from_curve(curve) or 365.0
    mdd = max_drawdown(curve)
    yrs = ((curve[-1][0] - curve[0][0]) / 1e6 / _SECONDS_PER_YEAR) if len(curve) >= 2 else 0.0
    total_ret = (curve[-1][1] / result.equity0 - 1.0) if curve and result.equity0 else 0.0
    win = max(2, min(len(rets), int(ppy / 12) or 20))   # ~monthly rolling window
    return {
        "sortino": sortino(rets, ppy),
        "calmar": calmar(total_ret, mdd, yrs),
        "var_95": value_at_risk(rets, 0.05),
        "cvar_95": cvar(rets, 0.05),
        "ulcer_index": ulcer_index(curve),
        "drawdown": drawdown_series(curve),
        "rolling_sharpe": rolling_sharpe(rets, win, ppy),
        "monthly": monthly_returns(curve),
        "mae_mfe": mae_mfe(result.trades, bars),
    }
