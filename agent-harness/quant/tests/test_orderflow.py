"""Order-flow primitives — real taker-delta vs tick-rule proxy, CVD, divergence."""

from quant import orderflow


def _bar(c, vol, taker_buy=None):
    return {"open": c, "high": c, "low": c, "close": c, "volume": vol, "taker_buy": taker_buy}


def test_real_taker_delta():
    bars = [_bar(100, 10, taker_buy=7), _bar(101, 10, taker_buy=3)]   # buy-heavy then sell-heavy
    d = orderflow.taker_delta(bars)
    assert d == [2 * 7 - 10, 2 * 3 - 10]      # +4, -4
    assert orderflow.has_real(bars) is True


def test_proxy_fallback_when_no_taker():
    bars = [_bar(100, 10), _bar(101, 10), _bar(100.5, 10)]            # up, then down
    d = orderflow.taker_delta(bars)
    assert d[0] == 0.0 and d[1] == 10.0 and d[2] == -10.0
    assert orderflow.has_real(bars) is False


def test_cvd_cumulative():
    bars = [_bar(100, 10, 7), _bar(101, 10, 8), _bar(102, 10, 9)]     # +4, +6, +8
    assert orderflow.cvd(bars) == [4.0, 10.0, 18.0]


def test_read_flow_and_bullish_divergence():
    # price falls but aggressive BUYING dominates -> bullish divergence (someone absorbing)
    bars = [_bar(100 - i * 0.1, 10, taker_buy=8) for i in range(25)]
    r = orderflow.read(bars, lookback=20)
    assert r["real"] is True
    assert r["flow"] == "buy" and r["delta_recent"] > 0
    assert r["divergence"] and "bullish" in r["divergence"]


def test_read_insufficient():
    assert orderflow.read([_bar(100, 1, 1)])["flow"] == "flat"
