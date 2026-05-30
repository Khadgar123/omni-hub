"""Backtest engine: parity, no-look-ahead (shift-1), costs, regime gate, metrics."""

import math
from types import SimpleNamespace

import pytest

from quant.backtest import engine, metrics
from quant.backtest.costs import ZERO_COST, CostModel
from quant.strategy.trend_donchian import TrendDonchian

_H = 3_600_000_000          # 1h in µs
_BASE = 1_704_067_200_000_000  # 2024-01-01 in µs


def _uptrend(n=200, start=100.0, rate=0.01):
    # open gaps slightly above the prior close so opens and closes are DISJOINT
    # sets — lets the no-look-ahead test distinguish a next-open fill from a
    # signal-close fill.
    bars, prev = [], start
    for i in range(n):
        c = start * (1.0 + rate) ** i
        o = prev * 1.0005 if i > 0 else prev
        bars.append({"bucket_ts": _BASE + i * _H, "open": o,
                     "high": max(o, c) * 1.003, "low": min(o, c) * 0.997,
                     "close": c, "volume": 1.0})
        prev = c
    return bars


def test_trend_profits_on_uptrend_zero_cost():
    res = engine.run_backtest(TrendDonchian(), _uptrend(), cost=ZERO_COST)
    assert len(res.trades) >= 1
    assert res.final_equity > res.equity0  # uptrend + trend-follower => profit


def test_costs_reduce_equity():
    bars = _uptrend()
    free = engine.run_backtest(TrendDonchian(), bars, cost=ZERO_COST)
    costed = engine.run_backtest(TrendDonchian(), bars, cost=CostModel())
    assert costed.final_equity <= free.final_equity


def test_regime_gate_blocks_trend_in_range():
    state = lambda i: SimpleNamespace(symbol="BTCUSDT", regime_label="range",
                                      composite_bias="flat", stand_down=False)
    res = engine.run_backtest(TrendDonchian(), _uptrend(), state_for=state)
    assert len(res.trades) == 0  # trend not eligible in range => never enters


def test_no_lookahead_entry_fills_at_an_open_not_signal_close():
    bars = _uptrend()
    res = engine.run_backtest(TrendDonchian(), bars, cost=ZERO_COST)
    opens = {round(b["open"], 6) for b in bars}
    closes = {round(b["close"], 6) for b in bars}
    assert len(res.trades) >= 1
    entry = round(res.trades[0].entry, 6)
    assert entry in opens          # filled at a bar OPEN (shift-1)...
    assert entry not in closes     # ...not at the signal bar's close


def test_metrics_summarize_shape():
    res = engine.run_backtest(TrendDonchian(), _uptrend(), cost=CostModel())
    m = metrics.summarize(res.equity_curve, res.trades, equity0=res.equity0)
    for k in ["n_trades", "total_return", "cagr", "sharpe", "psr", "max_drawdown",
              "win_rate", "profit_factor", "final_equity", "periods_per_year"]:
        assert k in m
    assert math.isfinite(m["sharpe"])
    assert m["psr"] is None or 0.0 <= m["psr"] <= 1.0
    assert m["periods_per_year"] == pytest.approx(365 * 24, rel=0.01)  # 1h bars


def test_metrics_buy_hold_benchmark():
    curve = [(0, 10000.0), (1, 10100.0), (2, 10050.0)]   # strategy +0.5%
    m = metrics.summarize(curve, [], equity0=10000.0, prices=[100.0, 110.0])  # HODL +10%
    assert m["buy_hold_return"] == pytest.approx(0.10)
    assert m["beat_hold"] is False
    assert m["excess_return"] == pytest.approx(m["total_return"] - 0.10)
