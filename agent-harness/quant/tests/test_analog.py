"""analog: MASS z-normalized matching + top-K + analog-ensemble forward. Network-free, deterministic."""

import numpy as np

from quant import analog


def _motif():
    return np.sin(np.linspace(0, 3.0, 20))


def test_dist_profile_finds_exact_motif():
    rs = np.random.RandomState(0)
    ts = np.concatenate([rs.randn(40) * 0.1, _motif(), rs.randn(40) * 0.1])
    d = analog.dist_profile(_motif(), ts)
    assert int(d.argmin()) == 40 and d.min() < 1e-6           # planted at index 40, exact match


def test_znorm_scale_shift_invariance():
    q = _motif()
    ts = np.concatenate([np.zeros(10), 5 * q + 100.0, np.zeros(10)])   # scaled x5, shifted +100
    d = analog.dist_profile(q, ts)
    assert int(d.argmin()) == 10 and d.min() < 1e-6           # z-norm matches shape regardless of level


def test_top_k_nonoverlapping_finds_all_copies():
    rs = np.random.RandomState(2)
    m = _motif()
    ts = np.concatenate([m, rs.randn(30), m, rs.randn(30), m])  # copies at 0, 50, 100
    got = sorted(p for p, _ in analog.top_k(m, ts, k=3, excl=20))
    assert len(got) == 3
    for p in got:
        assert min(abs(p - x) for x in (0, 50, 100)) <= 1


def test_analog_forward_fan_ordered():
    rs = np.random.RandomState(4)
    closes = np.cumsum(rs.randn(200)) + 100.0
    f = analog.analog_forward(closes, [(10, 0.1), (60, 0.2), (110, 0.3)], win=10, horizon=20)
    assert f["n"] == 3 and len(f["median"]) == 20
    assert np.all(f["p10"] <= f["median"] + 1e-9) and np.all(f["median"] <= f["p90"] + 1e-9)


def test_match_level_on_injected_bars():
    # build synthetic bars with a repeated up-then-down shape; query = last window matches earlier ones
    base = list(np.sin(np.linspace(0, 6.28, 30)))
    seq = base * 6
    bars = [{"open": v + 100, "high": v + 101, "low": v + 99, "close": v + 100,
             "volume": 1.0, "bucket_ts": i * 3600_000000} for i, v in enumerate(seq)]
    r = analog.match_level("X", "1h", win=30, k=3, bars=bars)
    assert r["matches"] and r["matches"][0][1] < 1e-3         # near-exact match exists in history
