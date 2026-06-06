"""Binance fapi funding/OI mappers + injected-fetch (no network)."""

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "agent-harness" / "integrations" / "finance" / "binance_fapi.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("binance_fapi", MODULE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


fapi = load_module()


def test_funding_to_row_ms_to_micros():
    row = fapi.funding_to_row({"symbol": "BTCUSDT", "fundingTime": 1716950400000, "fundingRate": "0.0001"}, "BTCUSDT")
    assert row["bucket_ts"] == 1716950400000 * 1000
    assert row["funding_rate"] == 0.0001


def test_oi_to_row():
    row = fapi.oi_to_row({"sumOpenInterest": "12345.6", "sumOpenInterestValue": "8.0e8",
                          "timestamp": 1716950400000}, "BTCUSDT")
    assert row["bucket_ts"] == 1716950400000 * 1000
    assert row["open_interest"] == 12345.6 and row["open_interest_value"] == 8.0e8


def test_funding_regime_thresholds():
    assert fapi.funding_regime(0.0008) == "crowded_long"
    assert fapi.funding_regime(-0.0008) == "crowded_short"
    assert fapi.funding_regime(0.0001) == "neutral"


def test_fetch_funding_builds_params_and_maps():
    seen = {}

    def fake(method, base_url, path, *, params=None, timeout=15.0):
        seen.update(method=method, path=path, params=params)
        return [{"symbol": "BTCUSDT", "fundingTime": 1, "fundingRate": "0.0002"}]

    out = fapi.fetch_funding_rate("BTCUSDT", start_ms=1, end_ms=2, request_fn=fake)
    assert seen["path"] == "/fapi/v1/fundingRate"
    assert seen["params"]["symbol"] == "BTCUSDT" and seen["params"]["startTime"] == 1
    assert len(out) == 1 and out[0]["funding_rate"] == 0.0002


def test_fetch_oi_builds_params():
    seen = {}

    def fake(method, base_url, path, *, params=None, timeout=15.0):
        seen.update(path=path, params=params)
        return [{"sumOpenInterest": "1", "sumOpenInterestValue": "2", "timestamp": 1}]

    fapi.fetch_open_interest_hist("BTCUSDT", "4h", request_fn=fake)
    assert seen["path"] == "/futures/data/openInterestHist"
    assert seen["params"]["period"] == "4h"
