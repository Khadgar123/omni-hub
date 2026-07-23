"""Pure-logic tests for quant.etf (NO network) — Farside table parse + summarize + injected fetch.

``fetch()`` hits Farside live (smoke-covered by ``python -m quant.etf`` / ``crypto-read``); the
deterministic core (_num / _parse_table / summarize) and the never-raise contract are locked here."""
import json

from quant import etf


# ---- injected opener (returns the fixture HTML; mirrors test_framework's _Resp) ----
class _Resp:
    def __init__(self, data): self._d = data
    def read(self): return self._d
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _opener(html):
    def op(req, timeout=20.0):
        return _Resp(html.encode("utf-8"))
    return op


# A nav decoy table FIRST (must be ignored), then the real data table.  '02 Jun' is not-yet-reported
# (all issuer cells '-') and must be skipped so asof = the last REAL row.
_HTML = """
<table><tr><td>Fund ETF Flows Bitcoin Ethereum</td></tr></table>
<table>
<tr><td></td><td></td><td></td><td></td><td>Total</td></tr>
<tr><td></td><td>IBIT</td><td>FBTC</td><td>GBTC</td><td></td></tr>
<tr><td>Fee</td><td>0.25%</td><td>0.25%</td><td>1.50%</td><td></td></tr>
<tr><td>29 May 2026</td><td>100.0</td><td>20.0</td><td>(10.0)</td><td>110.0</td></tr>
<tr><td>30 May 2026</td><td>(200.0)</td><td>(30.0)</td><td>(5.0)</td><td>(235.0)</td></tr>
<tr><td>01 Jun 2026</td><td>(440.3)</td><td>(37.3)</td><td>0.0</td><td>(483.8)</td></tr>
<tr><td>02 Jun 2026</td><td>-</td><td>-</td><td>-</td><td>0.0</td></tr>
<tr><td>Total</td><td>63,366</td><td>10,567</td><td>(26,619)</td><td>55,230</td></tr>
</table>
"""


def test_num_handles_parens_commas_dash():
    assert etf._num("100.0") == 100.0
    assert etf._num("(9.5)") == -9.5            # parens = negative
    assert etf._num("1,119.9") == 1119.9        # thousands comma
    assert etf._num("-") is None and etf._num("") is None
    assert etf._num("0.0") == 0.0               # a real zero, not None


def test_parse_table_picks_data_table_and_latest_reported_row():
    p = etf._parse_table(_HTML)
    assert p["tickers"] == ["IBIT", "FBTC", "GBTC"]           # decoy nav table ignored
    assert p["asof"] == "01 Jun 2026"                         # '02 Jun' (all '-') skipped
    assert p["net"] == -483.8
    assert p["by_issuer"] == {"IBIT": -440.3, "FBTC": -37.3, "GBTC": 0.0}
    assert [n for _d, n in p["history"]] == [110.0, -235.0, -483.8]   # Total/Average rows excluded


def test_summarize_trend_streak_and_note():
    s = etf.summarize(etf._parse_table(_HTML))
    assert s["trend"] == "outflow"                            # net -483.8 < -flat_band
    assert s["net_usd_m"] == -483.8
    assert s["streak"] == 2                                   # 01 Jun & 30 May both outflow; 29 May was in
    assert s["last5_sum_usd_m"] == -608.8                     # 110 - 235 - 483.8
    assert "Farside T+1" in s["note"] and s["source"] == "farside"


def test_summarize_flat_band():
    parsed = {"net": 5.0, "history": [("01 Jun 2026", 5.0)], "by_issuer": {"IBIT": 5.0}, "asof": "01 Jun 2026"}
    assert etf.summarize(parsed, flat_band=20.0)["trend"] == "flat"     # tiny net = flat, not inflow


def test_fetch_with_injected_opener_is_available():
    r = etf.fetch("btc", opener=_opener(_HTML))
    assert r["available"] is True and r["asset"] == "btc"
    assert r["trend"] == "outflow" and r["net_usd_m"] == -483.8
    assert r["top_issuers"][0]["ticker"] == "IBIT"           # ranked by |flow|


def test_fetch_never_raises_on_garbage():
    r = etf.fetch("btc", opener=_opener("<html>no table here</html>"))
    assert r["available"] is False and "reason" in r         # graceful, not an exception


def test_fetch_unknown_asset_unavailable():
    r = etf.fetch("doge")
    assert r["available"] is False and "no Farside page" in r["reason"]
