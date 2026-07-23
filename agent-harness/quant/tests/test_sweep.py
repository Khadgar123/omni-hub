"""Parameter sweep + CSCV/OOS gate (sweep_configs on synthetic, data-free)."""

from types import SimpleNamespace

from quant.backtest import sweep
from quant.backtest.costs import ZERO_COST
from quant.strategy.trend_donchian import TrendDonchian

_H = 3_600_000_000
_BASE = 1_704_067_200_000_000


def _uptrend(n=260, rate=0.008):
    bars, prev = [], 100.0
    for i in range(n):
        c = 100.0 * (1.0 + rate) ** i
        o = prev * 1.0003 if i else prev
        bars.append({"bucket_ts": _BASE + i * _H, "open": o, "high": max(o, c) * 1.002,
                     "low": min(o, c) * 0.998, "close": c, "volume": 1.0})
        prev = c
    return bars


def _states(n):
    return [SimpleNamespace(symbol="BTCUSDT", regime_label="up", composite_bias="long",
                            stand_down=False) for _ in range(n)]


def test_config_grid_cartesian():
    g = sweep.config_grid({"a": [1, 2], "b": [3, 4]})
    assert len(g) == 4
    assert {"a": 1, "b": 3} in g and {"a": 2, "b": 4} in g


def test_sweep_configs_structure_and_gate():
    bars = _uptrend()
    states = _states(len(bars))
    configs = sweep.config_grid({"entry_lookback": [10, 20, 30]})
    out = sweep.sweep_configs(TrendDonchian, configs, bars, states, cost=ZERO_COST, n_groups=6)
    assert out["n_configs"] == 3
    assert set(out) >= {"best_config", "pbo", "deflated_sharpe", "viable",
                        "reject_reasons", "oos_degradation", "event_concentration",
                        "best_sr_is", "best_sr_oos", "best_n_trades"}
    assert out["best_config"] in configs
    assert out["best_config"]["entry_lookback"] in {10, 20, 30}
    assert out["pbo"] is None or 0.0 <= out["pbo"] <= 1.0
    assert isinstance(out["viable"], bool)
    assert isinstance(out["reject_reasons"], list)


def test_sweep_configs_single_config_no_pbo():
    bars = _uptrend(120)
    out = sweep.sweep_configs(TrendDonchian, [{"entry_lookback": 20}], bars,
                              _states(120), cost=ZERO_COST)
    assert out["n_configs"] == 1
    assert out["pbo"] is None          # PBO needs >= 2 configs
    assert out["viable"] is False      # can't pass the gate without PBO/DSR
