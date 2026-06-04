"""Engine SYMMETRIC long/short: a short profits as price falls, signed accounting
balances, stops trigger on the correct side, and friction is recorded."""

from quant.backtest.costs import CostModel, ZERO_COST
from quant.backtest.engine import run_backtest
from quant.strategy.base import SHORT, StrategyIntent


def _bars(prices):
    return [{"open": p, "high": p + 1, "low": p - 1, "close": p, "volume": 1.0,
             "bucket_ts": i * 60_000_000} for i, p in enumerate(prices)]


class _ShortOnce:
    id = "shortonce"
    timeframe = "1m"
    eligible_regimes = frozenset({"up", "down", "range"})
    requires_bias = None

    def __init__(self, stop):
        self.stop = stop
        self.fired = False

    def evaluate(self, bars, state, position_qty):
        if not self.fired and len(bars) >= 3 and position_qty == 0:
            self.fired = True
            c = float(bars[-1]["close"])
            return StrategyIntent(self.id, "BTCUSDT", "1m", int(bars[-1]["bucket_ts"]),
                                  SHORT, 1.0, c, self.stop, "down", "short test", {})
        return None


def test_short_profits_when_price_falls():
    res = run_backtest(_ShortOnce(stop=150), _bars([100, 100, 100] + list(range(100, 80, -1))),
                       cost=ZERO_COST)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.direction == "short" and t.qty < 0
    assert t.pnl > 0 and res.final_equity > res.equity0     # short made money on the fall


def test_short_stopped_when_price_rises_through_stop():
    res = run_backtest(_ShortOnce(stop=105), _bars([100, 100, 100] + list(range(100, 120))),
                       cost=ZERO_COST)
    t = res.trades[0]
    assert t.exit_reason == "stop" and t.pnl < 0             # stopped above, a loss
    assert t.exit <= 105 + 1                                 # covered near the stop


def test_short_records_friction():
    res = run_backtest(_ShortOnce(stop=150), _bars([100, 100, 100] + list(range(100, 80, -1))),
                       cost=CostModel())                     # default 10bps taker + 2bps slip
    assert res.trades[0].cost > 0                            # round-trip friction captured
