"""Backtest read-path + nautilus_trader record-shape compatibility."""

from __future__ import annotations

import pytest

from quant import backtest  # noqa: E402  (pure mappers need no heavy deps)


def test_aggressor_side_mapping():
    assert backtest.to_aggressor_side("buy") == "BUYER"
    assert backtest.to_aggressor_side("sell") == "SELLER"
    assert backtest.to_aggressor_side("") == "NO_AGGRESSOR"
    assert backtest.to_aggressor_side(None) == "NO_AGGRESSOR"


def test_bar_type_string():
    assert backtest.bar_type_str("NVDA", "XNAS", "1d") == "NVDA.XNAS-1-DAY-LAST-EXTERNAL"
    assert backtest.bar_type_str("BTCUSDT", "BINANCE", "5m") == "BTCUSDT.BINANCE-5-MINUTE-LAST-EXTERNAL"
    assert backtest.bar_type_str("X", "V", "1h") == "X.V-1-HOUR-LAST-EXTERNAL"


def test_trade_tick_dict_nanos_and_fields():
    row = {
        "symbol": "BTCUSDT", "exchange_ts": 1_000_000, "receive_ts": 2_000_000,
        "price": 10.0, "size": 1.5, "side": "sell", "trade_id": "x", "venue": "binance",
    }
    tt = backtest.to_nautilus_trade_tick_dict(row)
    assert tt["instrument_id"] == "BTCUSDT.binance"
    assert tt["ts_event"] == 1_000_000 * 1_000   # micros -> nanos
    assert tt["ts_init"] == 2_000_000 * 1_000
    assert tt["aggressor_side"] == "SELLER"
    assert tt["price"] == 10.0 and tt["size"] == 1.5


def test_bar_dict_nanos():
    bar = {"bucket_ts": 1_767_312_000_000_000, "open": 1, "high": 2, "low": 0.5,
           "close": 1.5, "volume": 9.0}
    nb = backtest.to_nautilus_bar_dict(bar, symbol="DEMO", venue="SIM", freq="1d")
    assert nb["bar_type"] == "DEMO.SIM-1-DAY-LAST-EXTERNAL"
    assert nb["ts_event"] == nb["ts_init"] == 1_767_312_000_000_000 * 1_000
    assert nb["open"] == 1.0 and nb["close"] == 1.5


def test_nautilus_available_is_bool():
    assert isinstance(backtest.nautilus_available(), bool)


# ---- integration read-path (needs duckdb/pyarrow + a store) ---------------


def test_read_for_backtest_pit_adjusted(store):
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    from quant import market_store as ms

    tr = ms.trades("DEMO", "2026-01-02", "2026-01-03", root=store)
    ms.write_bars(ms.bars_from_trades(tr, freq="1d", symbol="DEMO"),
                  symbol="DEMO", freq="1d", root=store)
    out = backtest.read_for_backtest("DEMO", "1d", "2026-01-01", "2026-01-10",
                                     root=store, asof="2026-01-03", adjust=True, venue="SIM")
    assert out["bar_type"] == "DEMO.SIM-1-DAY-LAST-EXTERNAL"
    assert out["bars"][0]["close"] == pytest.approx(50.25)  # split-adjusted
    assert out["bars"][0]["ts_event"] == out["bars"][0]["ts_init"]
    assert len(out["bars"]) == 2
