"""Forward (paper) validation — split at live_from, verdict, persistence."""

from types import SimpleNamespace

from quant import paper

_H = 3_600_000_000          # 1h in µs
LF = 1_700_000_000_000_000  # a live_from boundary (µs)


def _t(ts, pnl):
    return SimpleNamespace(entry_ts=ts, pnl=pnl)


def _curve(equities, *, t0):
    return [(t0 + i * _H, e) for i, e in enumerate(equities)]


def test_forward_split_holds_when_forward_generalizes():
    base = _curve([100, 101, 100.5, 101.5, 102], t0=LF - 5 * _H)      # all ts < LF
    fwd = _curve([102, 104, 106, 108, 110, 112], t0=LF)              # ts >= LF, strong up
    trades = [_t(LF - 300, 1.0)] * 3 + [_t(LF + 100, 1.0)] * 6        # 3 baseline + 6 forward
    r = paper.forward_split(trades, base + fwd, live_from_us=LF, equity0=100.0)
    assert r["n_baseline_trades"] == 3 and r["n_forward_trades"] == 6
    assert r["forward"]["total_return"] > 0 and r["baseline"]["sharpe"] > 0
    assert r["verdict"] == "holds"
    assert r["live_from"].startswith("2023-")  # 1.7e15 µs ≈ Nov 2023


def test_forward_split_fails_when_forward_loses():
    base = _curve([100, 101, 100.5, 101.5, 102], t0=LF - 5 * _H)
    fwd = _curve([102, 101, 100.5, 100, 99.5, 99], t0=LF)            # forward declines
    trades = [_t(LF - 300, 1.0)] * 3 + [_t(LF + 100, -1.0)] * 6
    r = paper.forward_split(trades, base + fwd, live_from_us=LF, equity0=100.0)
    assert r["forward"]["total_return"] < 0
    assert r["verdict"] == "fails"


def test_forward_split_insufficient_forward_data():
    base = _curve([100, 101, 102, 103, 104], t0=LF - 5 * _H)
    fwd = _curve([104, 105, 106], t0=LF)
    trades = [_t(LF - 300, 1.0)] * 3 + [_t(LF + 100, 1.0)] * 2        # only 2 forward < min 5
    r = paper.forward_split(trades, base + fwd, live_from_us=LF, equity0=100.0)
    assert r["verdict"] == "insufficient_forward_data"


def test_create_save_load_list_roundtrip(tmp_path):
    t = paper.create("divergence_reversal_v1", "BTCUSDT", live_from="2025-06",
                     inception="2024-01", note="overfit check", root=tmp_path)
    assert t.id == "divergence_reversal_v1.BTCUSDT.2025-06"
    back = paper.load(t.id, root=tmp_path)
    assert back.strategy_id == "divergence_reversal_v1" and back.live_from == "2025-06"
    assert back.inception == "2024-01" and back.note == "overfit check"
    assert [x.id for x in paper.list_tests(root=tmp_path)] == [t.id]


def test_load_missing_raises(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        paper.load("nope.NONE.2099-01", root=tmp_path)
