"""Statistical validation — the moat (López de Prado / Bailey).

NONE of the surveyed OSS engines (freqtrade/nautilus/jesse/vectorbt/backtrader/
hummingbot) ship this; all three research streams (papers, OSS, practitioners)
converged on it being THE differentiator and the antidote to "don't trust the
backtest". Reimplemented clean from the public formulas (pypbo is AGPL — not
copied; skfolio BSD CombinatorialPurgedCV used only as a structural reference).

Contents:
  * split generators — purged K-fold, combinatorial purged CV, walk-forward
    (all with purge + embargo to kill label leakage);
  * Deflated Sharpe Ratio + expected-max-Sharpe under N trials (Bailey & LdP);
  * Probability of Backtest Overfitting via CSCV (Bailey, Borwein, LdP, Zhu);
  * Quant-Arb robustness checks — event-concentration, OOS Sharpe degradation.

All pure-stdlib (math/statistics/itertools); no numpy needed at these sizes.
"""

from __future__ import annotations

import itertools
import math
import statistics

from quant.backtest.metrics import _moments

_N = statistics.NormalDist()
_EULER_GAMMA = 0.5772156649015329


# --------------------------------------------------------------------------
# Split generators (purge = drop train obs whose labels overlap the test set;
# embargo = also drop train obs just AFTER the test set). Indices are integer
# bar positions.
# --------------------------------------------------------------------------

def _fold_bounds(n_obs: int, n_folds: int) -> list[int]:
    return [round(i * n_obs / n_folds) for i in range(n_folds + 1)]


def purged_kfold_splits(n_obs, n_folds, *, purge=0, embargo=0):
    """K contiguous test folds; train = the rest minus a purge gap before and an
    embargo gap after each test fold. Returns ``[(train_idx, test_idx), ...]``.
    """
    b = _fold_bounds(n_obs, n_folds)
    out = []
    for k in range(n_folds):
        lo, hi = b[k], b[k + 1]
        test = list(range(lo, hi))
        train = [i for i in range(n_obs) if i < lo - purge or i >= hi + embargo]
        out.append((train, test))
    return out


def combinatorial_purged_splits(n_obs, n_folds, n_test_folds, *, purge=0, embargo=0):
    """López de Prado CPCV: choose ``n_test_folds`` of ``n_folds`` groups as test
    (all C(n_folds, n_test_folds) combinations), purge+embargo around each test
    group. Returns ``[(train_idx, [test_group_idx, ...]), ...]``.
    """
    b = _fold_bounds(n_obs, n_folds)
    groups = [list(range(b[i], b[i + 1])) for i in range(n_folds)]
    out = []
    for combo in itertools.combinations(range(n_folds), n_test_folds):
        excluded = set()
        for g in combo:
            lo, hi = b[g], b[g + 1]
            excluded.update(range(lo, hi))
            excluded.update(range(max(0, lo - purge), lo))
            excluded.update(range(hi, min(n_obs, hi + embargo)))
        train = [i for i in range(n_obs) if i not in excluded]
        out.append((train, [groups[g] for g in combo]))
    return out


def n_combinatorial_splits(n_folds, n_test_folds):
    return math.comb(n_folds, n_test_folds)


def walk_forward_splits(n_obs, train_size, test_size, *, anchored=False, embargo=0):
    """Rolling (or anchored) walk-forward train->test windows."""
    out = []
    t0 = 0
    while True:
        tr_lo = 0 if anchored else t0
        tr_hi = t0 + train_size
        te_lo = tr_hi + embargo
        te_hi = te_lo + test_size
        if te_hi > n_obs:
            break
        out.append((list(range(tr_lo, tr_hi)), list(range(te_lo, te_hi))))
        t0 += test_size
    return out


# --------------------------------------------------------------------------
# Deflated Sharpe Ratio (Bailey & López de Prado 2014).
# All Sharpes here are PER-PERIOD (not annualized).
# --------------------------------------------------------------------------

def _psr(sr, *, sr_benchmark, n_obs, skew, kurt):
    """Probabilistic Sharpe Ratio: P(true SR > benchmark) given skew/kurt/T."""
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr))
    z = (sr - sr_benchmark) * math.sqrt(max(1, n_obs - 1)) / denom
    return _N.cdf(z)


def expected_max_sharpe(n_trials, sr_variance):
    """E[max SR] across ``n_trials`` independent trials whose SRs have variance
    ``sr_variance`` (the selection-bias benchmark a deflated Sharpe must beat).
    """
    if n_trials < 2 or sr_variance <= 0:
        return 0.0
    z1 = _N.inv_cdf(1.0 - 1.0 / n_trials)
    z2 = _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(sr_variance) * ((1.0 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2)


def deflated_sharpe_ratio(sr, *, n_trials, sr_variance, n_obs, skew=0.0, kurt=3.0):
    """DSR = PSR against the expected-max-Sharpe benchmark from ``n_trials``.

    ``sr`` per-period. The more configs you tried (``n_trials``) and the more
    they varied (``sr_variance``), the higher the bar — so a Sharpe that looked
    great after a big sweep deflates toward 0.5 or below.
    """
    sr_star = expected_max_sharpe(n_trials, sr_variance)
    return _psr(sr, sr_benchmark=sr_star, n_obs=n_obs, skew=skew, kurt=kurt)


def deflated_sharpe_from_returns(returns, trial_sharpes):
    """Convenience: DSR for a chosen config's ``returns`` given the per-period
    Sharpes of all ``trial_sharpes`` configs tried in the sweep.
    """
    if len(returns) < 3 or len(trial_sharpes) < 2:
        return None
    m, sd, skew, kurt = _moments(returns)
    if sd == 0:
        return None
    sr = m / sd
    sr_var = statistics.pvariance(trial_sharpes)
    return deflated_sharpe_ratio(sr, n_trials=len(trial_sharpes), sr_variance=sr_var,
                                 n_obs=len(returns), skew=skew, kurt=kurt)


# --------------------------------------------------------------------------
# Probability of Backtest Overfitting — CSCV (Bailey, Borwein, LdP, Zhu 2017).
# --------------------------------------------------------------------------

def _sharpe(rows):
    if len(rows) < 2:
        return 0.0
    m = statistics.fmean(rows)
    sd = statistics.pstdev(rows)
    return m / sd if sd > 0 else 0.0


def probability_of_backtest_overfitting(perf_matrix, n_groups=10):
    """PBO via Combinatorially-Symmetric CV.

    ``perf_matrix``: ``N`` configs x ``T`` per-observation returns (equal T).
    Partition T into ``n_groups`` contiguous groups; over every split of the
    groups into IS/OOS halves, pick the IS-best config and find its OOS rank.
    PBO = fraction of splits where the IS-best lands below the OOS median —
    i.e. P(selection doesn't generalize). >0.5 means the process is overfit.
    """
    n_configs = len(perf_matrix)
    if n_configs < 2:
        return {"pbo": None, "n_combinations": 0}
    t = len(perf_matrix[0])
    if n_groups % 2:
        n_groups -= 1
    n_groups = max(2, min(n_groups, t))
    b = _fold_bounds(t, n_groups)
    groups = [list(range(b[i], b[i + 1])) for i in range(n_groups)]
    half = n_groups // 2
    lambdas = []
    for is_combo in itertools.combinations(range(n_groups), half):
        is_set = set(is_combo)
        is_rows = [r for g in is_combo for r in groups[g]]
        oos_rows = [r for g in range(n_groups) if g not in is_set for r in groups[g]]
        is_sr = [_sharpe([perf_matrix[c][r] for r in is_rows]) for c in range(n_configs)]
        oos_sr = [_sharpe([perf_matrix[c][r] for r in oos_rows]) for c in range(n_configs)]
        best = max(range(n_configs), key=lambda c: is_sr[c])
        rank = sum(1 for c in range(n_configs) if oos_sr[c] <= oos_sr[best])  # 1..N
        omega = min(max(rank / (n_configs + 1), 1e-9), 1 - 1e-9)
        lambdas.append(math.log(omega / (1.0 - omega)))
    if not lambdas:
        return {"pbo": None, "n_combinations": 0}
    pbo = sum(1 for x in lambdas if x <= 0) / len(lambdas)
    return {"pbo": pbo, "n_combinations": len(lambdas)}


# --------------------------------------------------------------------------
# Quant-Arb robustness checks ("the validation IS the product").
# --------------------------------------------------------------------------

def event_concentration(trades, top_k=3):
    """Share of gross PROFIT from the top-k winning trades.

    Quant Arb: if ~3 trades make all the PnL, the edge is fragile regardless of
    the headline Sharpe. Returns None if there's no gross profit.
    """
    profits = sorted((t.pnl for t in trades if t.pnl > 0), reverse=True)
    gross = sum(profits)
    if gross <= 0:
        return None
    return sum(profits[:top_k]) / gross


def oos_sharpe_degradation(is_sharpe, oos_sharpe):
    """Fractional Sharpe drop from in-sample to out-of-sample (LdP overfit flag:
    a drop > ~0.4, or a sign flip, is a red flag)."""
    if is_sharpe <= 0:
        return None
    return (is_sharpe - oos_sharpe) / is_sharpe
