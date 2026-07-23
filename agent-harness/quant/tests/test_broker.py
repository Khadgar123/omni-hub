"""Broker: HMAC signing, signed-request shape, order payloads, intent translation, execute gate.
All network injected; NO real keys, NO real orders."""

import hashlib
import hmac
import json
import urllib.error

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
    op = _opener({"totalMarginBalance": "1234.5", "availableBalance": "1000.0", "totalWalletBalance": "1234.5"}, cap)
    bal = broker.read_balance(market="futures", net="testnet", opener=op, now_ms=1)
    assert bal["equity"] == 1234.5 and bal["available"] == 1000.0 and bal["asset"] == "USDT"
    assert "signature=" in cap["url"] and "k" in cap["headers"].values()   # signed + key header
    assert "/fapi/v2/account" in cap["url"] and "testnet.binancefuture.com" in cap["url"]


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


def test_position_mode_detects_hedge(monkeypatch):
    monkeypatch.setenv("BINANCE_KEY", "k")
    monkeypatch.setenv("BINANCE_SECRET", "s")
    broker._POS_MODE.clear()
    assert broker.position_mode(net="mainnet", opener=_opener({"dualSidePosition": True})) == "hedge"
    broker._POS_MODE.clear()
    assert broker.position_mode(net="mainnet", opener=_opener({"dualSidePosition": False})) == "oneway"


def test_place_order_hedge_uses_position_side(monkeypatch):
    monkeypatch.setenv("BINANCE_KEY", "k")
    monkeypatch.setenv("BINANCE_SECRET", "s")
    cap = {}
    broker.place_order(symbol="BTCUSDC", side="BUY", otype="STOP_MARKET", quantity=0.01, price=68000,
                       position_side="SHORT", reduce_only=True, net="mainnet", opener=_opener({"orderId": 1}, cap))
    assert "positionSide=SHORT" in cap["url"] and "reduceOnly" not in cap["url"]   # hedge: positionSide, no reduceOnly


def test_execute_intent_hedge_adds_position_side(monkeypatch):
    monkeypatch.setenv("BINANCE_KEY", "k")
    monkeypatch.setenv("BINANCE_SECRET", "s")
    broker._POS_MODE.clear()
    broker._FILTERS.clear()
    order_urls = []

    def op(req, timeout=8.0):
        url, m = req.full_url, req.get_method()
        if "positionSide/dual" in url:
            return _Resp({"dualSidePosition": True})                  # account is in hedge mode
        if "/fapi/v1/order" in url and m == "POST":
            order_urls.append(url)
            return _Resp({"orderId": len(order_urls)})
        return _Resp({})
    intent = {"id": "i", "plan": {"symbol": "BTCUSDC", "direction": "short", "size_cap_frac": 0.1,
              "ref_price": 67000, "entries": [{"price": 68000, "size_frac": 1.0, "label": "埋伏"}],   # no follow base
              "stop": 69000, "targets": [{"price": 65000, "size_frac": 1.0, "label": "T"}]}}
    broker.execute_intent(intent, equity=10000, net="mainnet", yes=True, opener=op)
    assert order_urls and all("positionSide=SHORT" in u for u in order_urls)        # every order carries the side
    assert all("reduceOnly" not in u for u in order_urls)                           # hedge: reduceOnly omitted
    assert any("closePosition=true" in u for u in order_urls)                       # stop closes the whole position


def test_execute_intent_rounds_qty_and_skips_dust(monkeypatch):
    monkeypatch.setenv("BINANCE_KEY", "k")
    monkeypatch.setenv("BINANCE_SECRET", "s")
    broker._POS_MODE.clear()
    broker._FILTERS.clear()
    placed = []

    def op(req, timeout=8.0):
        url, m = req.full_url, req.get_method()
        if "positionSide/dual" in url:
            return _Resp({"dualSidePosition": False})                 # one-way
        if "exchangeInfo" in url:
            return _Resp({"symbols": [{"symbol": "BTCUSDC", "quantityPrecision": 3, "pricePrecision": 1,
                          "filters": [{"filterType": "LOT_SIZE", "minQty": "0.001"},
                                      {"filterType": "MIN_NOTIONAL", "notional": "50"}]}]})
        if "/fapi/v1/order" in url and m == "POST":
            placed.append(url)
            return _Resp({"orderId": len(placed)})
        return _Resp({})
    intent = {"id": "i", "plan": {"symbol": "BTCUSDC", "direction": "long", "size_cap_frac": 0.1, "ref_price": 1000,
              "entries": [{"price": 1000, "size_frac": 0.001, "label": "dust"},     # $1 notional -> skip
                          {"price": 1000, "size_frac": 0.999, "label": "ok"}],      # $999 -> place
              "stop": 900, "targets": []}}
    res = broker.execute_intent(intent, equity=10000, net="mainnet", yes=True, opener=op)
    assert any("skipped" in str(r.get("resp")) for r in res["receipts"])            # the $1 dust entry skipped
    import re
    for u in placed:
        mq = re.search(r"quantity=([0-9.]+)", u)
        if mq and "." in mq.group(1):
            assert len(mq.group(1).split(".")[-1]) <= 3                             # qty rounded to 3 decimals
    assert any("closePosition=true" in u for u in placed)                          # stop placed as closePosition


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


def test_call_fails_over_to_next_host(monkeypatch):
    monkeypatch.setenv("BINANCE_KEY", "k")
    monkeypatch.setenv("BINANCE_SECRET", "s")
    seen = []

    def op(req, timeout=6.0):
        seen.append(req.full_url)
        if "//fapi.binance.com" in req.full_url:              # primary host is down
            raise urllib.error.URLError("down")
        return _Resp({"totalMarginBalance": "100", "availableBalance": "100", "totalWalletBalance": "100"})
    bal = broker.read_balance(market="futures", net="mainnet", opener=op)
    assert bal["equity"] == 100.0                             # succeeded via fail-over
    assert any("//fapi.binance.com" in u for u in seen) and any("fapi1.binance.com" in u for u in seen)


def test_close_position_guarantees_flat(monkeypatch):
    monkeypatch.setenv("BINANCE_KEY", "k")
    monkeypatch.setenv("BINANCE_SECRET", "s")
    st = {"reads": 0, "closed": False}

    def op(req, timeout=6.0):
        url, m = req.full_url, req.get_method()
        if "positionRisk" in url:
            st["reads"] += 1
            amt = "0" if st["closed"] else "0.5"              # flat only AFTER the close order fires
            return _Resp([{"symbol": "BTCUSDC", "positionAmt": amt, "entryPrice": "100", "markPrice": "100",
                           "unRealizedProfit": "0", "leverage": "5"}])
        if "/fapi/v1/order" in url and m == "POST":
            st["closed"] = True
            return _Resp({"orderId": 1})                      # market reduceOnly close
        return _Resp({})                                      # allOpenOrders DELETE etc.
    res = broker.close_position("BTCUSDC", net="mainnet", opener=op)
    assert res["closed"] and res["remaining"] == 0.0 and st["closed"]


def test_cancel_order_payload(monkeypatch):
    monkeypatch.setenv("BINANCE_KEY", "k")
    monkeypatch.setenv("BINANCE_SECRET", "s")
    cap = {}
    broker.cancel_order("BTCUSDC", 123, net="mainnet", opener=_opener({"orderId": 123}, cap))
    assert "orderId=123" in cap["url"] and "symbol=BTCUSDC" in cap["url"] and cap["method"] == "DELETE"


def test_set_leverage_payload(monkeypatch):
    monkeypatch.setenv("BINANCE_KEY", "k")
    monkeypatch.setenv("BINANCE_SECRET", "s")
    cap = {}
    broker.set_leverage("BTCUSDC", 10, net="mainnet", opener=_opener({"leverage": 10}, cap))
    assert "leverage=10" in cap["url"] and "symbol=BTCUSDC" in cap["url"] and cap["method"] == "POST"


def test_execute_refuses_without_approval():
    intent = {"id": "i", "plan": {"symbol": "BTCUSDT", "direction": "short", "size_cap_frac": 0.1,
                                  "ref_price": 67000, "entries": [], "stop": 68000, "targets": []}}
    with pytest.raises(RuntimeError, match="approval"):
        broker.execute_intent(intent, equity=10000, yes=False)   # never auto-fires
