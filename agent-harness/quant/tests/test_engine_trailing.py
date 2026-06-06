"""Engine trailing-stop: the stop ratchets up below the running peak (Chandelier),
banks a winner that would otherwise round-trip, and stays causal (no look-ahead)."""

from quant.backtest.costs import ZERO_COST
from quant.backtest.engine import run_backtest
from quant.strategy.base import LONG, StrategyIntent


class _EnterOnceLong:
    """Go long once on the first bar with an initial stop + a trailing distance,
    then hold (the trailing stop does the exiting)."""
    id = "trail_stub"
    timeframe = "1h"
    eligible_regimes = frozenset({"up"})
    requires_bias = None

    def __init__(self, stop, trail):
        self.stop, self.trail, self.done = stop, trail, False

    def evaluate(self, bars, state, position_qty):
        if self.done or position_qty > 0:
            return None
        self.done = True
        c = float(bars[-1]["close"]); ts = int(bars[-1]["bucket_ts"])
        return StrategyIntent("trail_stub", "X", "1h", ts, LONG, 1.0, c, self.stop,
                              "up", "enter", {}, self.trail)


def _bars(closes):
    return [{"open": c, "high": c, "low": c - 1.0, "close": c, "volume": 1.0,
             "bucket_ts": i * 3_600_000_000} for i, c in enumerate(closes)]


def test_trailing_stop_banks_the_move():
    # enter ~100, run to 120, fall back: trail=8 -> stop ratchets to 120-8=112,
    # the pullback's low (108-1) trips it and banks ~+12 instead of round-tripping to 100.
    bars = _bars([100, 100, 110, 120, 115, 108, 100])
    res = run_backtest(_EnterOnceLong(stop=90.0, trail=8.0), bars, equity0=10_000.0, cost=ZERO_COST)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.exit_reason == "stop"
    assert t.exit == 112.0          # trailed to peak(120) - trail(8), not the initial 90
    assert t.pnl > 0                # banked the winner


def test_no_trail_round_trips_to_eod():
    # same path, no trailing: initial stop 90 never hit, rides back to 100 -> ~flat at eod
    bars = _bars([100, 100, 110, 120, 115, 108, 100])
    res = run_backtest(_EnterOnceLong(stop=90.0, trail=0.0), bars, equity0=10_000.0, cost=ZERO_COST)
    assert len(res.trades) == 1
    assert res.trades[0].exit_reason == "eod"
    assert res.trades[0].exit == 100.0
