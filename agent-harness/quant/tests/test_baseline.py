"""Directionless baseline: momentum signal, basket selection, causal backtest, daily decision."""

import json
import math

from quant import baseline
from quant.baseline import BaselineConfig


def _prices(n_coins=6, n_days=220):
    """Persistent cross-sectional dispersion: coin 0 trends strongest, coin N weakest,
    with a small oscillation so realized vol is non-zero (for vol-target)."""
    prices = {}
    for i in range(n_coins):
        drift = 0.015 - 0.006 * i                       # coin0 fastest, last coin declines (wide spread)
        m = {}
        for d in range(n_days):
            m[d] = 100.0 * ((1 + drift) ** d) * (1 + 0.015 * math.sin(d / 4.0 + i))  # small wobble = vol
        prices[f"C{i}USDT"] = m
    return prices


def test_momentum_scores_rank_strongest_first():
    p = _prices()
    sc = baseline.momentum_scores(p, asof=219, lookback_day=14)
    assert sc[0][0] == "C0USDT"                          # strongest coin first
    assert sc[-1][0] == "C5USDT"                         # weakest last
    assert sc[0][1] > sc[-1][1]


def test_select_basket_top_and_bottom():
    sc = [("a", 0.5), ("b", 0.3), ("c", 0.0), ("d", -0.2), ("e", -0.4)]
    longs, shorts = baseline.select_basket(sc, 2)
    assert [s for s, _ in longs] == ["a", "b"]
    assert [s for s, _ in shorts] == ["d", "e"]
    assert baseline.select_basket(sc, 3) and baseline.select_basket([("a", 1)], 2) == ([], [])


def test_backtest_profits_on_persistent_dispersion():
    # mechanism check on synthetic data (β/Sharpe realism is validated on real data separately)
    res = baseline.backtest(_prices(), BaselineConfig(k=2))
    assert res.n_days > 100
    assert res.cagr > 0                                  # long-strong/short-weak profits
    assert res.final_equity > 1.0
    assert math.isfinite(res.beta) and math.isfinite(res.sharpe)


def test_daily_decision_longs_strongest():
    dec = baseline.daily_decision(_prices(), BaselineConfig(k=2))
    assert dec.longs[0][0] == "C0USDT"                   # long the strongest
    assert dec.shorts[-1][0] == "C5USDT"                 # short the weakest
    assert dec.gross_scale > 0 and dec.regime in ("normal", "high_vol")
    assert "相对强弱" in dec.note


def test_daily_decision_no_lookahead():
    p = _prices(n_days=180)
    dec_now = baseline.daily_decision(p, BaselineConfig(k=2), asof=160)
    # appending FUTURE days must not change the decision made at asof=160
    p2 = {s: dict(m) for s, m in p.items()}
    for s in p2:
        for d in range(180, 220):
            p2[s][d] = p2[s][179] * 1.5                  # wild future moves
    dec_later = baseline.daily_decision(p2, BaselineConfig(k=2), asof=160)
    assert dec_now.to_dict() == dec_later.to_dict()      # causal


def test_load_live_injected_no_network():
    rows = [[1700000000000 + i * 86400000, "100", "101", "99", str(100 + i), "5",
             0, "0", 3, "0", "0", "0"] for i in range(60)]

    class _Resp:
        def __init__(self, d): self._d = d
        def read(self): return self._d
        def __enter__(self): return self
        def __exit__(self, *a): return False

    payload = json.dumps(rows).encode()

    def opener(req, timeout=15.0):
        return _Resp(payload)

    prices = baseline.load_live(["BTCUSDT", "ETHUSDT"], opener=opener)
    assert set(prices) == {"BTCUSDT", "ETHUSDT"}
    assert all(isinstance(d, int) and v > 0 for d, v in prices["BTCUSDT"].items())


def test_basket_to_intent_is_dollar_neutral():
    dec = baseline.BasketDecision(asof=100, longs=[("A", 0.2), ("B", 0.1)],
                                  shorts=[("Y", -0.1), ("Z", -0.3)], gross_scale=2.0, regime="normal")
    legs = baseline.basket_to_intent(dec, equity=10000.0)
    buys = sum(l["notional"] for l in legs if l["side"] == "buy")
    sells = sum(l["notional"] for l in legs if l["side"] == "sell")
    assert buys == sells                                 # delta-neutral
    assert abs(buys + sells - 10000.0 * 2.0) < 1.0       # total gross = equity × scale


def test_emit_basket_intent_jsonl(tmp_path):
    import json as _json
    dec = baseline.daily_decision(_prices(), BaselineConfig(k=2))
    path = baseline.emit_basket_intent(dec, 10000.0, tmp_path / "b.jsonl")
    rec = _json.loads(open(path).read().splitlines()[-1])
    assert rec["kind"] == "order_intent" and rec["strategy"] == "baseline_xsect"
    assert len(rec["legs"]) == 4                         # 2 long + 2 short


def test_carry_and_combined_sleeves():
    p = _prices()
    days = sorted(set().union(*[set(d) for d in p.values()]))
    funding = {s: {d: 0.0003 for d in days} for s in p}  # steady positive funding
    cs = baseline.carry_scores(funding, asof=days[-1], window=7)
    assert all(abs(v - 0.0003) < 1e-9 for v in cs.values())
    res = baseline.backtest_combined(p, funding, BaselineConfig(k=2), carry_weight=0.4)
    assert set(res) == {"momentum", "carry", "combined", "corr_mom_carry"}
    assert res["carry"][0] > 0                            # positive funding -> positive carry CAGR
    assert math.isfinite(res["corr_mom_carry"])


def test_load_store_is_graceful_on_missing(tmp_path):
    # no partitions for these symbols under an empty root -> empty map, no crash
    prices = baseline.load_store(["ZZZ_NONE"], root=tmp_path)
    assert prices == {}
