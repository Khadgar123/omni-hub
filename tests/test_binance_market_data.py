"""Binance market-data ingestion: pure mappers + injected-fetch + write path.

The mapping/fetch tests are network-free (request_fn is injected).  The write
test needs the quant venv (duckdb/pyarrow + the installed ``quant`` package)
and is skipped otherwise.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "agent-harness" / "integrations" / "finance" / "binance_market_data.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("binance_market_data", MODULE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bmd = load_module()


# ---- pure mappers (no network, no heavy deps) ------------------------------


def test_agg_trade_to_row_seller_aggressor():
    raw = {"a": 12345, "p": "100.5", "q": "2.0", "T": 1767312005000, "m": True}
    row = bmd.agg_trade_to_row(raw, "BTCUSDT", receive_ts_us=999)
    assert row["symbol"] == "BTCUSDT"
    assert row["exchange_ts"] == 1767312005000 * 1000   # ms -> us
    assert row["receive_ts"] == 999
    assert row["sequence"] == 12345 and row["trade_id"] == "12345"
    assert row["price"] == 100.5 and row["size"] == 2.0
    assert row["side"] == "sell"  # m=True => buyer is maker => aggressor is seller
    assert row["venue"] == "binance"
    assert row["fee"] == 0.0 and row["order_state"] == ""


def test_agg_trade_to_row_buyer_aggressor_default_receive():
    raw = {"a": 1, "p": "1", "q": "1", "T": 1000, "m": False}
    row = bmd.agg_trade_to_row(raw, "X")
    assert row["side"] == "buy"
    assert row["receive_ts"] == row["exchange_ts"]  # defaults to event time


def test_kline_to_bar_row():
    k = [1767312000000, "100", "110", "90", "105", "10",
         1767312059999, "1050", "7", "0", "0", "0"]
    bar = bmd.kline_to_bar_row(k, "BTC")
    assert bar["bucket_ts"] == 1767312000000 * 1000
    assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (100.0, 110.0, 90.0, 105.0)
    assert bar["volume"] == 10.0 and bar["trades"] == 7
    assert bar["vwap"] == pytest.approx(1050 / 10)  # quoteVol / volume


# ---- fetch with injected request_fn (no network) ---------------------------


def test_fetch_agg_trades_builds_params():
    seen = {}

    def fake(method, base_url, path, *, params=None, timeout=15.0):
        seen.update(method=method, path=path, params=params)
        return [{"a": 1, "p": "1", "q": "1", "T": 1000, "m": False}]

    out = bmd.fetch_agg_trades("BTC", start_ms=1000, end_ms=2000, request_fn=fake)
    assert seen["method"] == "GET" and seen["path"] == "/api/v3/aggTrades"
    assert seen["params"]["symbol"] == "BTC"
    assert seen["params"]["startTime"] == 1000 and seen["params"]["endTime"] == 2000
    assert len(out) == 1


def test_fetch_agg_trades_from_id_precedence():
    seen = {}

    def fake(method, base_url, path, *, params=None, timeout=15.0):
        seen.update(params=params)
        return []

    bmd.fetch_agg_trades("BTC", start_ms=1000, from_id=42, request_fn=fake)
    assert seen["params"]["fromId"] == 42 and "startTime" not in seen["params"]


# ---- live default-fetch wiring (no network) --------------------------------


def test_load_sibling_registers_module_for_dataclass():
    """Regression: the default-fetch path (`_default_request_json` ->
    `_load_sibling`) must register ``sys.modules[name]`` BEFORE ``exec_module``,
    or ``binance_spot_live``'s ``@dataclass`` raises under py3.12
    (``sys.modules.get(cls.__module__)`` -> None). This path is unexercised by
    the injected-``request_fn`` tests above, which is how the bug hid.
    """
    sib = bmd._load_sibling("binance_spot_live")
    assert hasattr(sib, "request_json")
    creds = sib.BinanceCredentials(api_key="k", api_secret="s")  # @dataclass must build
    assert creds.present is True


# ---- ingestion write path (quant venv only) --------------------------------


def test_ingest_agg_trades_writes_parquet(tmp_path):
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    pytest.importorskip("quant")

    page = [{"a": i, "p": "10", "q": "1", "T": 1767312000000 + i * 1000, "m": False}
            for i in range(1, 4)]
    pages = iter([page])

    def fake(method, base_url, path, *, params=None, timeout=15.0):
        try:
            return next(pages)
        except StopIteration:
            return []

    root = tmp_path / "market"
    summ = bmd.ingest_agg_trades("BTCUSDT", start="2026-01-02", end="2026-01-03",
                                 root=root, request_fn=fake, limit=1000)
    assert summ["ingested"] == 3 and summ["pages"] == 1

    from quant import market_store as ms

    got = ms.trades("BTCUSDT", "2026-01-02", "2026-01-03", root=root)
    assert len(got) == 3
    assert {r["trade_id"] for r in got} == {"1", "2", "3"}
    assert all(r["venue"] == "binance" for r in got)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
