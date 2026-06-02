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
