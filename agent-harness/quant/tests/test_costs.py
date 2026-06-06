"""Cost model: sqrt market-impact, half-spread, maker/taker, round-trip hurdle.
Engine parity (flat defaults) preserved."""

import pytest

from quant.backtest.costs import ZERO_COST, CostModel


def test_impact_bps_square_root_law():
    c = CostModel(impact_y=0.5, adv=1000.0, daily_sigma_bps=400.0)
    i1 = c.impact_bps(10.0)
    i2 = c.impact_bps(40.0)
    assert i1 == pytest.approx(0.5 * 400 * (10 / 1000) ** 0.5)
    assert i2 / i1 == pytest.approx(2.0)          # 4x size -> 2x impact (concave, δ=0.5)
    assert CostModel().impact_bps(10.0) == 0.0    # off by default


def test_fill_price_adds_half_spread():
    c = CostModel(slippage_bps=0.0, spread_bps=4.0)   # half-spread = 2 bps
    assert c.fill_price(100.0, "buy") == pytest.approx(100.0 * (1 + 2 / 1e4))
    assert c.fill_price(100.0, "sell") == pytest.approx(100.0 * (1 - 2 / 1e4))


def test_fill_price_default_matches_old_flat_model():
    # parity: with spread/impact off, fill == ref*(1±slippage) as before
    c = CostModel(slippage_bps=2.0)
    assert c.fill_price(100.0, "buy") == pytest.approx(100.0 * (1 + 2 / 1e4))


def test_fee_maker_vs_taker():
    c = CostModel(taker_bps=10.0, maker_bps=2.0)
    assert c.fee(1000.0, maker=False) == pytest.approx(1.0)
    assert c.fee(1000.0, maker=True) == pytest.approx(0.2)
    assert CostModel(maker=True).fee(1000.0) == pytest.approx(0.75)   # default uses self.maker


def test_round_trip_hurdle():
    c = CostModel(taker_bps=10.0, slippage_bps=2.0, spread_bps=0.0)
    assert c.round_trip_bps() == pytest.approx(2 * (10 + 2))          # 24 bps to break even


def test_zero_cost_frictionless():
    assert ZERO_COST.fill_price(100.0, "buy") == 100.0
    assert ZERO_COST.fee(1000.0) == 0.0
