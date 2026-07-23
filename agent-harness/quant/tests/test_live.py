"""Live candle fetch + mappers (Coinbase / Kraken) — injected HTTP, no network."""

import io
import json

import pytest

from quant import live


class _Resp:
    def __init__(self, data: bytes):
        self._d = data

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(payload):
    data = json.dumps(payload).encode("utf-8")
    def op(req, timeout=15.0):
        return _Resp(data)
    return op


_CB_RAW = [  # Coinbase: [time(s), low, high, open, close, volume], newest-first
    [1700003600, 99.0, 101.5, 100.0, 100.5, 5.0],
    [1700000000, 98.0, 100.0, 99.0, 100.0, 4.0],
]
_KR_RAW = {
    "error": [],
    "result": {"XXBTZUSD": [[1700000000, "99", "101", "98", "100", "99.5", "4", 7]],
               "last": 1700003600},
}
_BN_RAW = [  # Binance fapi klines: [openTime(ms), o, h, l, c, vol, closeTime, quoteVol, trades, ...]
    [1700000000000, "99", "101", "98", "100", "4", 1700003599999, "400", 7, "2", "200", "0"],
    [1700003600000, "100", "101.5", "99", "100.5", "5", 1700007199999, "500", 9, "3", "300", "0"],
]


def test_coinbase_candles_mapper_sorts_ascending():
    bars = live.coinbase_candles(_CB_RAW)
    assert [b["bucket_ts"] for b in bars] == [1700000000 * 1_000_000, 1700003600 * 1_000_000]
    b0 = bars[0]
    assert (b0["open"], b0["high"], b0["low"], b0["close"], b0["volume"]) == (99.0, 100.0, 98.0, 100.0, 4.0)


def test_kraken_ohlc_mapper():
    bars = live.kraken_ohlc(_KR_RAW, "XBTUSD")
    assert len(bars) == 1
    b = bars[0]
    assert b["bucket_ts"] == 1700000000 * 1_000_000
    assert (b["open"], b["high"], b["low"], b["close"]) == (99.0, 101.0, 98.0, 100.0)
    assert b["vwap"] == 99.5 and b["trades"] == 7


def test_fetch_candles_coinbase_dispatch():
    bars = live.fetch_candles("BTCUSDT", "1h", venue="coinbase", opener=_opener(_CB_RAW))
    assert len(bars) == 2 and bars[0]["bucket_ts"] < bars[1]["bucket_ts"]


def test_fetch_candles_kraken_dispatch():
    bars = live.fetch_candles("BTCUSDT", "1h", venue="kraken", opener=_opener(_KR_RAW))
    assert len(bars) == 1 and bars[0]["close"] == 100.0


def test_binance_klines_mapper():
    bars = live.binance_klines(_BN_RAW)
    assert [b["bucket_ts"] for b in bars] == [1700000000 * 1_000_000, 1700003600 * 1_000_000]
    b0 = bars[0]
    assert (b0["open"], b0["high"], b0["low"], b0["close"], b0["volume"]) == (99.0, 101.0, 98.0, 100.0, 4.0)
    assert b0["trades"] == 7


def test_fetch_candles_binance_dispatch():
    bars = live.fetch_candles("BTCUSDT", "4h", venue="binance", opener=_opener(_BN_RAW))
    assert len(bars) == 2 and bars[0]["bucket_ts"] < bars[1]["bucket_ts"]
    assert live._BINANCE_INT["4h"] == "4h" and live._CONFIRM_TF["binance"] == "4h"
    assert live._product("BTCUSDT", "binance") == "BTCUSDT"


def test_symbol_and_interval_validation():
    assert live._product("BTCUSDT", "coinbase") == "BTC-USD"
    assert live._product("ETHUSDT", "kraken") == "ETHUSD"
    with pytest.raises(ValueError):
        live._product("DOGEUSDT", "coinbase")
    with pytest.raises(ValueError):
        live.fetch_candles("BTCUSDT", "3h", venue="coinbase", opener=_opener(_CB_RAW))
    assert "4h" not in live._COINBASE_GRAN  # Coinbase has no 4h granularity
    assert live._COINBASE_GRAN["6h"] == 21600 and live._KRAKEN_INT["4h"] == 240


def test_watch_loop_flags_regime_change(monkeypatch):
    from types import SimpleNamespace
    seq = [
        SimpleNamespace(regime_label="range", composite_bias="flat", stand_down=False),
        SimpleNamespace(regime_label="up", composite_bias="long", stand_down=False),
    ]
    calls = {"i": 0}

    def fake_live_alerts(symbol, *, venue="coinbase", emit_path=None):
        st = seq[min(calls["i"], len(seq) - 1)]
        calls["i"] += 1
        return [], st

    monkeypatch.setattr(live, "live_alerts", fake_live_alerts)
    recs = []
    n = live.watch_loop(["BTCUSDT"], max_iters=2, sleep_fn=lambda s: None, on_tick=recs.append)
    assert n == 2 and len(recs) == 2
    assert recs[0]["regime_changed"] is False   # first tick: no prior state
    assert recs[1]["regime_changed"] is True     # range -> up transition flagged
    assert recs[1]["regime"] == "up"
