"""Unified framework — synthesis logic + carry/read via injected HTTP (no network)."""

import json

from quant import framework


# ---- injected opener (routes Binance fapi URLs to fixtures) ----
class _Resp:
    def __init__(self, data): self._d = data
    def read(self): return self._d
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _klines(n=260, start=100.0, rate=0.003, taker_frac=0.55):
    rows = []
    c = start
    for i in range(n):
        o = c; c = o * (1 + rate); h = max(o, c) * 1.002; l = min(o, c) * 0.998; vol = 1000.0
        rows.append([1700000000000 + i * 900000, f"{o}", f"{h}", f"{l}", f"{c}", f"{vol}",
                     1700000000000 + i * 900000 + 899999, "0", 10, f"{vol * taker_frac}", "0", "0"])
    return rows


def _opener(routes):
    def op(req, timeout=15.0):
        url = req.full_url
        for sub, payload in routes:
            if sub in url:
                return _Resp(json.dumps(payload).encode("utf-8"))
        raise ValueError(f"no route for {url}")
    return op


_BINANCE_ROUTES = [
    ("premiumIndex", {"markPrice": "70000", "indexPrice": "70010", "lastFundingRate": "0.0001"}),
    ("fundingRate", [{"fundingRate": "0.00003"} for _ in range(90)]),   # current 0.0001 > history -> high pctile
    ("openInterest", {"openInterest": "100000"}),
    ("klines", _klines()),
]


def test_carry_crowd_long():
    c = framework.carry("BTCUSDT", opener=_opener(_BINANCE_ROUTES))
    assert c["crowd"] == "long"                       # current funding above its 30d history
    assert c["funding_pctile_30d"] >= 80
    assert c["basis_pct"] < 0                          # mark < index


def test_synthesize_crowded_long_into_weak_regime():
    reg = {"composite_bias": "short", "per_tf": {"4h": {"stand_down": True, "label": "down", "direction": "down"}}}
    car = {"crowd": "long", "funding_pctile_30d": 93, "basis_pct": -0.05}
    ofl = {"flow": "sell", "real": True, "divergence": None}
    mac = {"risk": "on"}
    s = framework.synthesize(reg, car, ofl, mac, {"res": 74000, "sup": 70000})
    assert "多头拥挤" in s["counterparty"]
    joined = " ".join(s["fragility"])
    assert "踩踏" in joined and "stand_down" in joined and "脱钩" in joined
    assert s["lean_mechanical"] == "short"
    assert any("74,000" in t for t in s["watch"]) and any("70,000" in t for t in s["watch"])


def test_read_integration_no_macro():
    r = framework.read("BTCUSDT", "binance", opener=_opener(_BINANCE_ROUTES), with_macro=False)
    assert set(r) >= {"carry", "regime", "orderflow", "macro", "levels", "synthesis"}
    assert r["carry"]["crowd"] == "long"
    assert r["regime"]["composite_bias"] in {"long", "short", "flat"}
    assert r["orderflow"]["real"] is True                 # binance klines carry taker_buy
    assert r["orderflow"]["flow"] == "buy"                 # taker_frac 0.55 -> net buy
    assert r["macro"] == {}                                # disabled
    assert r["synthesis"]["counterparty"]
    assert "_bars" not in r["regime"]                      # internal bars stripped from output
    assert isinstance(r["narrative"], str) and len(r["narrative"]) > 30


def test_read_includes_per_level_sr_and_detailed_report():
    r = framework.read("BTCUSDT", "binance", opener=_opener(_BINANCE_ROUTES), with_macro=False,
                       etf={"trend": "outflow", "note": "test"})
    # per-TF S/R map present, one entry per timeframe, each with the full shape
    assert isinstance(r["sr"], dict) and r["sr"]
    one = next(iter(r["sr"].values()))
    assert {"support", "resistance", "pos_in_range", "support_flow",
            "sup_dist_pct", "res_dist_pct"} <= set(one)
    assert one["resistance"] >= one["support"]
    # the detailed report renders the sections the narrative omits
    rep = r["report"]
    assert isinstance(rep, str)
    assert "各级别 S/R" in rep and "支撑" in rep and "对手盘" in rep and "收回压力" in rep
    assert rep != r["narrative"] and len(rep) > len(r["narrative"])    # report ⊋ narrative


# ---- regression for the 2026-06-02 miss: framework was blind to swing structure, so it called
#      ETH "weaker" while ETH 4h was a double-bottom basing above BTC's lower-low downtrend. ----
def _ramp(a, b, n):
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _mk(prices):
    # high/low straddle the CLOSE (not the open) so a trough bar is a STRICT local min — an
    # open=prev-close scheme ties a trough's low with its neighbour's and swings() finds nothing.
    bars = []
    for i, cl in enumerate(prices):
        cl = float(cl)
        bars.append({"open": cl, "high": cl * 1.002, "low": cl * 0.998, "close": cl,
                     "volume": 1000.0, "taker_buy": 500.0, "bucket_ts": (i + 1) * 14_400_000_000})
    return bars


def test_structure_flags_double_bottom_but_not_a_lower_low_downtrend():
    # W — two ~equal troughs (90 / 90.5) with a peak between, price recovered to mid-range:
    W = _ramp(100, 90, 8)[:-1] + _ramp(90, 95, 8)[:-1] + _ramp(95, 90.5, 8)[:-1] + _ramp(90.5, 92.5, 6)
    sb = framework._structure_by_tf({"4h": _mk(W)})["4h"]
    assert sb["pattern"] and "双底" in sb["pattern"]          # the structure layer that was missing
    assert sb["base_low"] <= 91                               # dominant base, not a stray micro-swing

    # Descending — lower-lows (96 -> 93 -> 89), price sits near the low = NOT a base:
    DN = (_ramp(100, 96, 6)[:-1] + _ramp(96, 99, 4)[:-1] + _ramp(99, 93, 6)[:-1]
          + _ramp(93, 96, 4)[:-1] + _ramp(96, 89, 7)[:-1] + _ramp(89, 90.5, 4))
    sd = framework._structure_by_tf({"4h": _mk(DN)})["4h"]
    assert not (sd["pattern"] and "双底" in sd["pattern"])     # must NOT call a downtrend a double-bottom
    assert sd["trend"] == "down"                              # market_structure: BOS/CHoCH down


def test_read_carries_structure_and_per_level_flow():
    r = framework.read("BTCUSDT", "binance", opener=_opener(_BINANCE_ROUTES), with_macro=False)
    assert isinstance(r["structure"], dict) and isinstance(r["flow_by_tf"], dict)
    assert "②b 摆动结构" in r["report"] and "逐级别订单流" in r["report"]


def test_synthesize_folds_etf_and_absorption():
    reg = {"composite_bias": "short", "per_tf": {"4h": {"stand_down": False, "label": "down", "direction": "down"}}}
    car = {"crowd": "long", "funding_pctile_30d": 90, "basis_pct": -0.05}
    ofl = {"flow": "sell", "real": True, "divergence": None}
    s = framework.synthesize(reg, car, ofl, {}, {"sup": 70000}, etf={"trend": "outflow"}, absorption="broke_down")
    j = " ".join(s["fragility"])
    assert "ETF 持续流出" in j and "已跌破 4h 支撑" in j


def test_narrate_is_readable_prose_not_metrics():
    r = framework.read("BTCUSDT", "binance", opener=_opener(_BINANCE_ROUTES), with_macro=False,
                       etf={"trend": "outflow", "note": "test"})
    n = r["narrative"]
    assert "BTCUSDT" in n and "拐点" in n                  # the action sentence is always present
    assert "①" not in n and "funding=" not in n            # prose, NOT a metrics dump
