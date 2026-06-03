"""Broker: HMAC signing, signed-request shape, order payloads, intent translation, execute gate.
All network injected; NO real keys, NO real orders."""

import hashlib
import hmac
import json

import pytest

from quant import broker


class _Resp:
    def __init__(self, d): self._d = json.dumps(d).encode()
    def read(self): return self._d
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _opener(payload, cap=None):
    def op(req, timeout=12.0):
        if cap is not None:
            cap["url"] = req.full_url
            cap["headers"] = dict(req.headers)
            cap["method"] = req.get_method()
        return _Resp(payload)
    return op


def test_sign_is_hmac_sha256():
    s = broker.sign("a=1&b=2", "secret")
    assert s == hmac.new(b"secret", b"a=1&b=2", hashlib.sha256).hexdigest()
    assert len(s) == 64


def test_read_balance_futures_signed(monkeypatch):
    monkeypatch.setenv("BINANCE_KEY", "k")
    monkeypatch.setenv("BINANCE_SECRET", "s")
    cap = {}
    op = _opener([{"asset": "USDT", "balance": "1234.5", "availableBalance": "1000.0"}], cap)
    bal = broker.read_balance(market="futures", net="testnet", opener=op, now_ms=1)
    assert bal["equity"] == 1234.5 and bal["available"] == 1000.0 and bal["asset"] == "USDT"
    assert "signature=" in cap["url"] and "k" in cap["headers"].values()   # signed + key header
    assert "testnet.binancefuture.com" in cap["url"]


def test_read_balance_requires_keys(monkeypatch):
    monkeypatch.delenv("BINANCE_KEY", raising=False)
    monkeypatch.delenv("BINANCE_SECRET", raising=False)
    monkeypatch.setattr(broker, "_secret_store", lambda: {})   # isolate from the real omni-hub store
    with pytest.raises(RuntimeError):
        broker.read_balance(opener=_opener([]))


def test_creds_resolve_from_omni_hub_store(monkeypatch):
    """No env vars — key/secret come straight from the omni-hub secret store (the sanctioned location)."""
    monkeypatch.delenv("BINANCE_KEY", raising=False)
    monkeypatch.delenv("BINANCE_SECRET", raising=False)
    monkeypatch.setattr(broker, "_secret_store",
                        lambda: {"omni-hub/api/binance/key": "K", "omni-hub/api/binance/secret": "S"})
    assert broker._creds() == ("K", "S")                       # resolved from store, no env
    assert broker.creds_available() is True
    # env still wins when present
    monkeypatch.setenv("BINANCE_KEY", "envK")
    monkeypatch.setenv("BINANCE_SECRET", "envS")
    assert broker._creds() == ("envK", "envS")


def test_place_order_payloads(monkeypatch):
    monkeypatch.setenv("BINANCE_KEY", "k")
    monkeypatch.setenv("BINANCE_SECRET", "s")
    cap = {}
    broker.place_order(symbol="BTCUSDT", side="SELL", otype="LIMIT", quantity=0.1, price=68000,
                       opener=_opener({"orderId": 1}, cap), now_ms=1)
    assert "type=LIMIT" in cap["url"] and "timeInForce=GTC" in cap["url"] and "price=68000" in cap["url"]
    assert cap["method"] == "POST"
    cap2 = {}
    broker.place_order(symbol="BTCUSDT", side="BUY", otype="STOP_MARKET", quantity=0.1, price=69000,
                       reduce_only=True, opener=_opener({"orderId": 2}, cap2), now_ms=1)
    assert "stopPrice=69000" in cap2["url"] and "reduceOnly=true" in cap2["url"]


def test_build_orders_translates_intent():
    intent = {"id": "i1", "plan": {"symbol": "BTCUSDT", "direction": "short", "size_cap_frac": 0.5,
              "ref_price": 67000,
              "entries": [{"price": 67000, "size_frac": 0.4, "follow": True, "label": "基础"},
                          {"price": 68000, "size_frac": 0.6, "label": "埋伏"}],
              "stop": 69000, "targets": [{"price": 65000, "size_frac": 0.5, "label": "T1"},
                                         {"price": 63000, "size_frac": 0.5, "label": "T2"}]}}
    orders = broker.build_orders(intent, equity=10000)
    assert len(orders) == 5                                  # 2 entries + 1 stop + 2 targets
    assert orders[0]["type"] == "FOLLOW" and orders[0]["follow"] and orders[0]["side"] == "SELL"  # base = maker-follow
    assert orders[1]["type"] == "LIMIT" and orders[1]["price"] == 68000
    stop = next(o for o in orders if o["type"] == "STOP_MARKET")
    assert stop["side"] == "BUY" and stop["price"] == 69000 and stop["reduceOnly"]   # exit side, reduceOnly
    tps = [o for o in orders if o["type"] == "TAKE_PROFIT_MARKET"]
    assert len(tps) == 2 and all(o["side"] == "BUY" and o["reduceOnly"] for o in tps)


def _broker_opener(status_seq=None):
    """Route by URL+method: POST /order -> orderId, GET /order -> next status, DELETE -> ok."""
    stt = {"oid": 0, "i": 0}

    def op(req, timeout=12.0):
        url, m = req.full_url, req.get_method()
        if "/order" in url and m == "POST":
            stt["oid"] += 1
            return _Resp({"orderId": stt["oid"]})
        if "/order" in url and m == "GET":
            seq = status_seq or ["NEW"]
            s = seq[min(stt["i"], len(seq) - 1)]
            stt["i"] += 1
            return _Resp({"status": s, "avgPrice": "100"})
        return _Resp({})
    return op


def test_maker_follow_fills_as_maker(monkeypatch):
    monkeypatch.setenv("BINANCE_KEY", "k")
    monkeypatch.setenv("BINANCE_SECRET", "s")
    r = broker.maker_follow(symbol="BTCUSDT", side="BUY", quantity=0.1, max_repegs=3,
                            opener=_broker_opener(status_seq=["FILLED"]),
                            sleep_fn=lambda s: None, price_fn=lambda: (100.0, 100.2))
    assert r["filled"] and r["as"] == "maker"


def test_maker_follow_taker_fallback(monkeypatch):
    monkeypatch.setenv("BINANCE_KEY", "k")
    monkeypatch.setenv("BINANCE_SECRET", "s")
    r = broker.maker_follow(symbol="BTCUSDT", side="SELL", quantity=0.1, max_repegs=2,
                            opener=_broker_opener(status_seq=["NEW", "NEW", "NEW"]),
                            sleep_fn=lambda s: None, price_fn=lambda: (100.0, 100.2))
    assert r["filled"] and r["as"] == "taker(fallback)"      # never filled -> guaranteed via market


def test_execute_refuses_without_approval():
    intent = {"id": "i", "plan": {"symbol": "BTCUSDT", "direction": "short", "size_cap_frac": 0.1,
                                  "ref_price": 67000, "entries": [], "stop": 68000, "targets": []}}
    with pytest.raises(RuntimeError, match="approval"):
        broker.execute_intent(intent, equity=10000, yes=False)   # never auto-fires
