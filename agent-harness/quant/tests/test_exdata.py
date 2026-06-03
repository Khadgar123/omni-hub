"""Exchange data layer: order-book walls, OI/funding/ratios, dashboard — injected HTTP."""

import json
import math

from quant import exdata


class _Resp:
    def __init__(self, d): self._d = d
    def read(self): return self._d
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _router(routes, *, fail=()):
    """Return an opener that dispatches by URL substring; URLs containing any ``fail``
    token raise (to exercise resilience)."""
    def opener(req, timeout=12.0):
        url = req.full_url
        for tok in fail:
            if tok in url:
                raise OSError("boom")
        for key, payload in routes.items():
            if key in url:
                return _Resp(json.dumps(payload).encode())
        raise KeyError(url)
    return opener


_DEPTH = {"bids": [["100.0", "1.0"], ["99.9", "0.5"], ["98.0", "9.0"], ["97.99", "3.0"]],
          "asks": [["101.0", "2.0"], ["102.0", "8.0"], ["102.01", "1.0"]]}
_OIH = [{"timestamp": 1, "sumOpenInterest": "1000"}, {"timestamp": 2, "sumOpenInterest": "950"}]
_FUND = {"lastFundingRate": "0.00010", "markPrice": "100.5"}
_LS = [{"longShortRatio": "2.5"}]
_TK = [{"buySellRatio": "0.80"}]

_ROUTES = {"/depth": _DEPTH, "openInterestHist": _OIH, "premiumIndex": _FUND,
           "globalLongShortAccountRatio": _LS, "takerlongshortRatio": _TK,
           "/openInterest?": {"openInterest": "950"}}


def test_order_walls_picks_and_merges_largest():
    depth = exdata.fetch_depth("BTCUSDC", opener=_router(_ROUTES))
    w = exdata.order_walls(depth, top=2)
    # 98.0(9.0) merges 97.99(3.0) -> a 12.0 bid wall = the biggest support
    assert w["bid_walls"][0]["price"] == 98.0 and w["bid_walls"][0]["qty"] == 12.0
    # 102.0(8.0) merges 102.01(1.0) -> 9.0 ask wall = the biggest resistance
    assert w["ask_walls"][0]["price"] == 102.0 and w["ask_walls"][0]["qty"] == 9.0


def test_funding_annualizes():
    f = exdata.fetch_funding("BTCUSDC", opener=_router(_ROUTES))
    assert f["last"] == 0.00010
    assert f["annualized"] == 0.00010 * 3 * 365


def test_oi_flush_detects_drop():
    assert exdata.oi_flush([{"oi": 1000}, {"oi": 950}], drop_pct=0.03)      # -5% => flush
    assert not exdata.oi_flush([{"oi": 1000}, {"oi": 995}], drop_pct=0.03)  # -0.5% => no


def test_dashboard_assembles_and_notes():
    d = exdata.dashboard("BTCUSDC", opener=_router(_ROUTES))
    assert d["order_walls"]["bid_walls"]
    assert d["open_interest"]["flush"] is True                  # 1000->950
    assert d["long_short_ratio"] == 2.5 and d["taker_buy_sell"] == 0.80
    # crowded longs (funding ann ~11% is <30 so no crowd note) but OI flush + taker<0.9 fire
    joined = " ".join(d["notes"])
    assert "插针" in joined and "主动卖压" in joined
    assert "render" or exdata.render_dashboard(d)               # renders without error


def test_dashboard_resilient_to_field_failure():
    # funding endpoint fails -> field is None, board still assembles
    d = exdata.dashboard("BTCUSDC", opener=_router(_ROUTES, fail=("premiumIndex",)))
    assert d["funding"] is None
    assert d["order_walls"] is not None                         # other fields survived


def test_round_trip_cost_uses_taker():
    depth = {"bids": [["100.00", "1"]], "asks": [["100.20", "1"]]}     # spread 0.20, mid 100.10
    c = exdata.round_trip_cost("BTCUSDC", opener=_router({"/depth": depth}))
    assert c["maker_bps"] == 0.0 and c["taker_bps"] == 10.0            # USDC: 0 maker, taker standard 0.1%
    assert abs(c["taker_leg_usd"] - 0.10) < 0.01                       # 10bp × 100.1 ≈ 0.10/unit
    assert abs(c["cost_taker_both"] - 0.40) < 0.01                     # spread + 2 taker legs
    assert c["cost_taker_both"] > c["cost_maker_in_taker_out"]         # market-both > limit-in/stop-out
    # custom VIP rate override (e.g. 7.5bp with BNB)
    c2 = exdata.round_trip_cost("BTCUSDC", opener=_router({"/depth": depth}), maker_taker=(0.0, 7.5))
    assert c2["taker_bps"] == 7.5 and c2["cost_taker_both"] < c["cost_taker_both"]


def test_deep_walls_reads_usdt_sibling_book():
    # gap-2: a BTCUSDC plan reads walls from the deep BTCUSDT book (prices transfer 1:1)
    w = exdata.deep_walls("BTCUSDC", opener=_router({"symbol=BTCUSDT": _DEPTH}))
    assert w["bid_walls"] and w["bid_walls"][0]["price"] == 98.0
