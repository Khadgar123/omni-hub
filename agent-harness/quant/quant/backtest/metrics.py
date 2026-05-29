"""Backtest performance metrics — incl. the Probabilistic Sharpe Ratio.

Single-run metrics here. The Deflated Sharpe + Probability of Backtest
Overfitting (which need the full multi-config TRIAL set) live in the
sweep/validation layer — that, plus PSR, is the "don't trust a raw Sharpe"
discipline (no OSS engine ships it; it's our differentiator).
"""

from __future__ import annotations

import math
import statistics

_SECONDS_PER_YEAR = 365.0 * 24 * 3600


def _moments(x):
    """(mean, sample-stdev, sample-skew, non-excess-kurtosis)."""
    n = len(x)
    m = statistics.fmean(x)
    if n < 2:
        return m, 0.0, 0.0, 3.0
    var = sum((v - m) ** 2 for v in x) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return m, 0.0, 0.0, 3.0
    z = [(v - m) / sd for v in x]
    skew = (sum(t ** 3 for t in z) * n / ((n - 1) * (n - 2))) if n > 2 else 0.0
    kurt = sum(t ** 4 for t in z) / n  # Pearson (non-excess); normal ≈ 3
    return m, sd, skew, kurt


def returns_from_curve(curve):
    eq = [e for _, e in curve]
    return [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1] > 0]


def sharpe(returns, periods_per_year):
    if len(returns) < 2:
        return 0.0
    m, sd, _, _ = _moments(returns)
    return (m / sd) * math.sqrt(periods_per_year) if sd > 0 else 0.0


def probabilistic_sharpe(returns, sr_benchmark_periodic=0.0):
    """PSR: P(true SR > benchmark) given skew/kurtosis/T (Bailey & López de Prado).

    Per-period SR vs a per-period benchmark. Accounts for non-normal returns —
    a high Sharpe on few, fat-tailed, skewed observations gets a low PSR.
    """
    n = len(returns)
    if n < 3:
        return None
    m, sd, skew, kurt = _moments(returns)
    if sd == 0:
        return None
    sr = m / sd
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr))
    z = (sr - sr_benchmark_periodic) * math.sqrt(n - 1) / denom
    return statistics.NormalDist().cdf(z)


def max_drawdown(curve):
    peak = float("-inf")
    mdd = 0.0
    for _, e in curve:
        if e > peak:
            peak = e
        if peak > 0:
            mdd = min(mdd, (e - peak) / peak)
    return mdd  # <= 0


def _ppy_from_curve(curve):
    if len(curve) < 3:
        return None
    dts = sorted(curve[i][0] - curve[i - 1][0] for i in range(1, len(curve)))
    dts = [d for d in dts if d > 0]
    if not dts:
        return None
    med_us = dts[len(dts) // 2]
    sec = med_us / 1e6
    return _SECONDS_PER_YEAR / sec if sec > 0 else None


def summarize(curve, trades, *, equity0, periods_per_year=None):
    rets = returns_from_curve(curve)
    ppy = periods_per_year or _ppy_from_curve(curve) or 365.0
    final = curve[-1][1] if curve else equity0
    total_ret = (final / equity0 - 1.0) if equity0 else 0.0
    cagr = 0.0
    if len(curve) >= 2 and curve[-1][0] > curve[0][0] and final > 0 and equity0 > 0:
        yrs = (curve[-1][0] - curve[0][0]) / 1e6 / _SECONDS_PER_YEAR
        if yrs > 0:
            cagr = (final / equity0) ** (1.0 / yrs) - 1.0
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    return {
        "n_trades": len(trades),
        "total_return": total_ret,
        "cagr": cagr,
        "sharpe": sharpe(rets, ppy),
        "psr": probabilistic_sharpe(rets),
        "max_drawdown": max_drawdown(curve),
        "win_rate": (len(wins) / len(trades)) if trades else 0.0,
        "profit_factor": pf,
        "final_equity": final,
        "periods_per_year": ppy,
    }
