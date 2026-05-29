"""Validation moat: splits (no leakage), Deflated Sharpe, PBO (CSCV), robustness."""

import math
import random
from types import SimpleNamespace

import pytest

from quant.backtest import validation as V


# --- split generators -------------------------------------------------------

def test_purged_kfold_no_overlap_and_gaps():
    splits = V.purged_kfold_splits(100, 5, purge=3, embargo=2)
    assert len(splits) == 5
    for train, test in splits:
        assert set(train).isdisjoint(test)
        lo, hi = min(test), max(test) + 1
        # no train index inside the purge-before / embargo-after gap
        assert all(i < lo - 3 or i >= hi + 2 for i in train)


def test_combinatorial_purged_count_and_disjoint():
    n_folds, n_test = 6, 2
    splits = V.combinatorial_purged_splits(120, n_folds, n_test, purge=2, embargo=2)
    assert len(splits) == V.n_combinatorial_splits(n_folds, n_test) == math.comb(6, 2)
    for train, test_groups in splits:
        test = [i for g in test_groups for i in g]
        assert set(train).isdisjoint(test)


def test_walk_forward_windows():
    splits = V.walk_forward_splits(100, train_size=40, test_size=20)
    assert len(splits) == 3
    for train, test in splits:
        assert len(train) == 40 and len(test) == 20
        assert max(train) < min(test)          # train strictly before test
    # rolling (not anchored): second window's train starts later
    assert min(splits[1][0]) > min(splits[0][0])


def test_walk_forward_anchored_and_embargo():
    splits = V.walk_forward_splits(100, train_size=40, test_size=20, anchored=True, embargo=5)
    assert all(min(tr) == 0 for tr, _ in splits)        # anchored: always from 0
    tr, te = splits[0]
    assert min(te) == max(tr) + 1 + 5                    # embargo gap before test


# --- Deflated Sharpe --------------------------------------------------------

def test_expected_max_sharpe_increases_with_trials():
    assert V.expected_max_sharpe(2, 0.04) < V.expected_max_sharpe(10, 0.04) < V.expected_max_sharpe(100, 0.04)


def test_deflated_sharpe_monotonic_in_trials():
    kw = dict(sr_variance=0.04, n_obs=500, skew=0.0, kurt=3.0)
    d2 = V.deflated_sharpe_ratio(0.15, n_trials=2, **kw)
    d10 = V.deflated_sharpe_ratio(0.15, n_trials=10, **kw)
    d100 = V.deflated_sharpe_ratio(0.15, n_trials=100, **kw)
    assert d2 > d10 > d100                  # more trials => higher bar => lower DSR
    assert all(0.0 <= d <= 1.0 for d in (d2, d10, d100))


# --- PBO (CSCV) -------------------------------------------------------------

def test_pbo_random_matrix_near_half():
    rng = random.Random(0)
    n_configs, t = 8, 240
    perf = [[rng.gauss(0, 1) for _ in range(t)] for _ in range(n_configs)]
    pbo = V.probability_of_backtest_overfitting(perf, n_groups=8)["pbo"]
    assert 0.3 <= pbo <= 0.7   # pure noise => selection doesn't generalize ~ coin flip


def test_pbo_low_when_one_config_truly_dominates():
    rng = random.Random(1)
    n_configs, t = 8, 240
    perf = [[rng.gauss(0, 1) for _ in range(t)] for _ in range(n_configs)]
    perf[0] = [x + 0.8 for x in perf[0]]   # config 0 has a real, persistent edge
    pbo = V.probability_of_backtest_overfitting(perf, n_groups=8)["pbo"]
    assert pbo < 0.25          # the genuine winner generalizes => low overfit prob


# --- robustness checks ------------------------------------------------------

def test_event_concentration():
    trades = [SimpleNamespace(pnl=p) for p in (10.0, 1.0, 1.0, 1.0, -5.0)]
    assert V.event_concentration(trades, top_k=3) == pytest.approx(12.0 / 13.0)


def test_event_concentration_no_profit_is_none():
    trades = [SimpleNamespace(pnl=p) for p in (-1.0, -2.0)]
    assert V.event_concentration(trades) is None


def test_oos_sharpe_degradation():
    assert V.oos_sharpe_degradation(1.0, 0.5) == pytest.approx(0.5)
    assert V.oos_sharpe_degradation(1.0, 1.2) == pytest.approx(-0.2)
    assert V.oos_sharpe_degradation(0.0, 0.5) is None
