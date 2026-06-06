"""Triple-barrier labels + exit efficiency."""

import pytest

from quant import labeling


def _bars(ohlc):
    return [{"open": o, "high": h, "low": low, "close": c, "volume": 1.0,
             "bucket_ts": i * 60_000_000} for i, (o, h, low, c) in enumerate(ohlc)]


def test_triple_barrier_hits_profit_take():
    # rises; pt = 2·1% = 2% above 100 -> upper barrier 102 crossed early
    bars = _bars([(100, 100, 100, 100)] + [(100 + i, 102 + i, 99 + i, 101 + i) for i in range(1, 6)])
    lab = labeling.triple_barrier(bars, [0], pt=2.0, sl=1.0, max_bars=10, sigma=[0.01] * 6)
    assert lab[0]["label"] == 1 and lab[0]["touched"] == "pt"


def test_triple_barrier_hits_stop_checked_first():
    bars = _bars([(100, 100, 100, 100)] + [(100 - i, 101 - i, 98 - i, 99 - i) for i in range(1, 6)])
    lab = labeling.triple_barrier(bars, [0], pt=2.0, sl=1.0, max_bars=10, sigma=[0.01] * 6)
    assert lab[0]["label"] == -1 and lab[0]["touched"] == "sl"


def test_triple_barrier_vertical_timeout():
    bars = _bars([(100, 100.2, 99.8, 100)] * 8)   # flat: never hits ±wide barriers
    lab = labeling.triple_barrier(bars, [0], pt=2.0, sl=1.0, max_bars=3, sigma=[0.05] * 8)
    assert lab[0]["touched"] == "vertical" and lab[0]["exit_idx"] == 3


def test_exit_efficiency_long_short_and_undefined():
    assert labeling.exit_efficiency(100, 108, 110) == pytest.approx(0.8)      # captured 8 of 10
    assert labeling.exit_efficiency(100, 100, 100) is None                     # never favorable
    assert labeling.exit_efficiency(100, 95, 90, direction="short") == pytest.approx(0.5)


def test_max_favorable_excursion():
    bars = _bars([(100, 105, 99, 101), (101, 110, 100, 108)])
    assert labeling.max_favorable_excursion(bars, 0, 60_000_000, direction="long") == 110
    assert labeling.max_favorable_excursion(bars, 0, 60_000_000, direction="short") == 99


def test_mean_exit_efficiency_over_trades():
    from types import SimpleNamespace
    bars = _bars([(100, 110, 99, 101)] * 3)          # high 110 throughout
    t = SimpleNamespace(entry=100.0, exit=105.0, entry_ts=0, exit_ts=120_000_000)
    assert labeling.mean_exit_efficiency([t], bars) == pytest.approx(0.5)   # 5 of 10
