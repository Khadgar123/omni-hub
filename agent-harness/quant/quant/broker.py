"""Authenticated Binance broker — read balance/positions/orders, and EXECUTE an APPROVED order intent
(place / cancel / close) with an optional maker-follow re-peg. Testnet by default.

HARD BOUNDARY (do not weaken):
  * This module is deterministic CODE. The agent/LLM never runs it to place a real order — a human
    approves an intent and runs the CLI. ``execute_intent`` refuses to fire without ``yes=True``.
  * API key/secret come from ENV (you set them); they are NEVER hardcoded, logged, or printed.
  * READ endpoints (balance/positions/orders) need only a READ key. Trading needs a trade-permission
    key AND your explicit approval per action.

Auth: Binance signs the query string with HMAC-SHA256(secret); the key goes in the ``X-MBX-APIKEY``
header. Pure-stdlib ``urllib`` + ``hmac``; the HTTP getter is injectable so the signing and payload
construction are unit-testable with NO network and NO real keys.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# Multiple base hosts per venue — on timeout/5xx/429 we fail over to the next, so a single slow/blocked
# endpoint never stalls an order or a close (latency + resilience matter most for 下单/平仓).
FAPI_HOSTS = {"mainnet": ["https://fapi.binance.com", "https://fapi1.binance.com", "https://fapi2.binance.com"],
              "testnet": ["https://testnet.binancefuture.com"]}
SPOT_HOSTS = {"mainnet": ["https://api.binance.com", "https://api1.binance.com", "https://api-gcp.binance.com"],
              "testnet": ["https://testnet.binance.vision"]}
FAPI = {k: v[0] for k, v in FAPI_HOSTS.items()}      # primary host (public/unsigned calls, back-compat)
SPOT = {k: v[0] for k, v in SPOT_HOSTS.items()}
_UA = "omni-hub-quant-broker/0.1"
_TIME_OFFSET = {"mainnet": 0, "testnet": 0}          # serverTime - localTime, refreshed on -1021 drift


def sign(query: str, secret: str) -> str:
    """Binance request signature = HMAC-SHA256(query_string, secret), hex."""
    return hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()


_SECRET_CACHE: dict | None = None


def _secret_store() -> dict:
    """Read the omni-hub local secret store (the SANCTIONED key location) WITHOUT importing omni_hub —
    stdlib json only, so it resolves from the quant env. Search order: $OMNI_HUB_SECRET_FILE,
    $OMNI_HUB_HOME/secrets.json, the repo's .omni/secrets.json (via git, so it works from a worktree),
    .omni up the cwd tree, then ~/.omni. Format: {"version":1,"secrets":{name:value}}. Cached once found."""
    global _SECRET_CACHE
    if _SECRET_CACHE:
        return _SECRET_CACHE
    from pathlib import Path
    cands = []
    if (f := os.environ.get("OMNI_HUB_SECRET_FILE", "").strip()):
        cands.append(Path(f).expanduser())
    if (h := os.environ.get("OMNI_HUB_HOME", "").strip()):
        cands.append(Path(h).expanduser() / "secrets.json")
    cwd = Path.cwd()
    try:                                                     # git-common-dir -> main repo root (worktree-safe)
        import subprocess
        r = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=str(cwd), timeout=3,
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            cands.append((cwd / r.stdout.strip()).resolve().parent / ".omni" / "secrets.json")
    except Exception:  # noqa: BLE001
        pass
    cands += [d / ".omni" / "secrets.json" for d in (cwd, *cwd.parents)]
    cands.append(Path.home() / ".omni" / "secrets.json")
    for p in cands:
        try:
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                sec = data.get("secrets", data) if isinstance(data, dict) else {}
                if isinstance(sec, dict) and sec:
                    _SECRET_CACHE = {str(k): str(v) for k, v in sec.items()}
                    return _SECRET_CACHE
        except Exception:  # noqa: BLE001
            continue
    return {}


def _resolve_ref(ref: str) -> str:
    """Resolve a secret reference: ``env:VAR`` -> env var; ``local:`` / ``file:`` -> omni-hub store."""
    ref = (ref or "").strip()
    if not ref:
        return ""
    prefix, _, value = ref.partition(":")
    if prefix == "env":
        return os.environ.get(value, "")
    if prefix in ("local", "file"):
        return _secret_store().get(value, "")
    return ""


def _creds(key_env: str = "BINANCE_KEY", secret_env: str = "BINANCE_SECRET"):
    """Resolve key+secret, priority: (1) env vars you exported; (2) the omni-hub secret store via
    ``$BINANCE_KEY_REF`` / ``$BINANCE_SECRET_REF`` (default ``local:omni-hub/api/binance/{key,secret}``).
    Raw key/secret are never logged or printed — resolved at call time straight into the signer."""
    key, secret = os.environ.get(key_env), os.environ.get(secret_env)
    if not key:
        key = _resolve_ref(os.environ.get("BINANCE_KEY_REF", "local:omni-hub/api/binance/key"))
    if not secret:
        secret = _resolve_ref(os.environ.get("BINANCE_SECRET_REF", "local:omni-hub/api/binance/secret"))
    if not key or not secret:
        raise RuntimeError(f"no credentials: export {key_env}/{secret_env}, OR point $OMNI_HUB_SECRET_FILE at "
                           f"your omni-hub secrets.json (has local:omni-hub/api/binance/key+secret)")
    return key, secret


def creds_available() -> bool:
    """True if a key+secret resolve (env or omni-hub store) — for the dashboard gate. Never raises."""
    try:
        return bool(_creds()[0])
    except Exception:  # noqa: BLE001
        return False


def _signed(base: str, path: str, params: dict, *, key: str, secret: str, method: str = "GET",
            opener=None, timeout: float = 12.0, now_ms: int | None = None):
    p = dict(params or {})
    p["timestamp"] = now_ms if now_ms is not None else int(time.time() * 1000)
    p.setdefault("recvWindow", 5000)
    q = urllib.parse.urlencode(p)
    q = q + "&signature=" + sign(q, secret)
    req = urllib.request.Request(f"{base}{path}?{q}", headers={"X-MBX-APIKEY": key, "User-Agent": _UA},
                                 method=method)
    opener = opener or urllib.request.urlopen
    with opener(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _resync_time(market: str, net: str, *, opener=None) -> None:
    """Refresh the local↔server clock offset (Binance rejects requests >recvWindow out of sync, -1021)."""
    host = (SPOT if market == "spot" else FAPI)[net]
    path = "/api/v3/time" if market == "spot" else "/fapi/v1/time"
    try:
        req = urllib.request.Request(f"{host}{path}", headers={"User-Agent": _UA})
        with (opener or urllib.request.urlopen)(req, timeout=5) as r:
            srv = int(json.loads(r.read().decode("utf-8"))["serverTime"])
        _TIME_OFFSET[net] = srv - int(time.time() * 1000)
    except Exception:  # noqa: BLE001
        pass


def _call(market: str, net: str, path: str, params: dict, *, key: str, secret: str, method: str = "GET",
          opener=None, timeout: float = 6.0, retries: int = 1, now_ms=None):
    """Signed request with host fail-over + bounded retry — moves to the next base host immediately on a
    network error / 5xx / 429 (low latency), resyncs the clock on -1021, and surfaces auth/4xx errors at
    once. ``timeout`` is short so a stalled host fails fast; this is what keeps 下单/平仓 from hanging."""
    hosts = (SPOT_HOSTS if market == "spot" else FAPI_HOSTS).get(net) or [(SPOT if market == "spot" else FAPI)[net]]
    last = None
    for rnd in range(retries + 1):
        for host in hosts:
            ts = now_ms if now_ms is not None else int(time.time() * 1000) + _TIME_OFFSET.get(net, 0)
            try:
                return _signed(host, path, params, key=key, secret=secret, method=method, opener=opener,
                               timeout=timeout, now_ms=ts)
            except urllib.error.HTTPError as e:
                try:
                    txt = e.read().decode("utf-8")
                except Exception:  # noqa: BLE001
                    txt = ""
                last = RuntimeError(f"HTTP {e.code}: {txt[:160]}")
                if "-1021" in txt:                       # clock drift -> resync, try next host
                    _resync_time(market, net, opener=opener)
                    continue
                if e.code in (408, 418, 429) or 500 <= e.code < 600:
                    continue                             # transient -> next host
                raise last                               # auth / bad request -> surface immediately
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = RuntimeError(f"net: {e}")
                continue                                 # unreachable host -> next host
        if rnd < retries:
            time.sleep(0.2 * (rnd + 1))                  # brief backoff before re-looping the hosts
    raise last or RuntimeError(f"all hosts failed for {path}")


# ---------------------------------------------------------------- READ side
def read_balance(*, market: str = "futures", net: str = "testnet", key_env: str = "BINANCE_KEY",
                 secret_env: str = "BINANCE_SECRET", opener=None, now_ms=None) -> dict:
    """Account balance/equity. Futures: USDT/USDC wallet balance + available. Spot: nonzero free assets.
    READ-only (a read key suffices). Returns ``{equity, available, asset, market, net}`` (futures)."""
    key, secret = _creds(key_env, secret_env)
    if market == "futures":                                  # account-level totals (no per-asset row ambiguity)
        data = _call("futures", net, "/fapi/v2/account", {}, key=key, secret=secret, opener=opener, now_ms=now_ms)
        return {"equity": float(data.get("totalMarginBalance", 0) or 0),
                "available": float(data.get("availableBalance", 0) or 0),
                "wallet": float(data.get("totalWalletBalance", 0) or 0),
                "upnl": float(data.get("totalUnrealizedProfit", 0) or 0),
                "asset": "USDT", "market": "futures", "net": net}
    data = _call("spot", net, "/api/v3/account", {}, key=key, secret=secret, opener=opener, now_ms=now_ms)
    bals = {b["asset"]: float(b["free"]) for b in data.get("balances", []) if float(b.get("free", 0)) > 0}
    return {"balances": bals, "market": "spot", "net": net}


def read_positions(*, net: str = "testnet", key_env: str = "BINANCE_KEY", secret_env: str = "BINANCE_SECRET",
                   opener=None, now_ms=None) -> list:
    """Open futures positions (nonzero positionAmt). Returns ``[{symbol, qty, entry, mark, uPnl, leverage}]``."""
    key, secret = _creds(key_env, secret_env)
    data = _call("futures", net, "/fapi/v2/positionRisk", {}, key=key, secret=secret, opener=opener, now_ms=now_ms)
    out = []
    for p in data:
        amt = float(p.get("positionAmt", 0) or 0)
        if amt == 0:
            continue
        out.append({"symbol": p.get("symbol"), "qty": amt, "entry": float(p.get("entryPrice", 0) or 0),
                    "mark": float(p.get("markPrice", 0) or 0), "uPnl": float(p.get("unRealizedProfit", 0) or 0),
                    "leverage": float(p.get("leverage", 1) or 1)})
    return out


def read_open_orders(symbol=None, *, net: str = "testnet", key_env: str = "BINANCE_KEY",
                     secret_env: str = "BINANCE_SECRET", opener=None, now_ms=None) -> list:
    key, secret = _creds(key_env, secret_env)
    params = {"symbol": symbol} if symbol else {}
    data = _call("futures", net, "/fapi/v1/openOrders", params, key=key, secret=secret, opener=opener, now_ms=now_ms)
    return [{"symbol": o.get("symbol"), "side": o.get("side"), "type": o.get("type"),
             "price": float(o.get("price", 0) or 0), "qty": float(o.get("origQty", 0) or 0),
             "orderId": o.get("orderId"), "clientOrderId": o.get("clientOrderId")} for o in data]


# ---------------------------------------------------- EXECUTION (human-gated)
def place_order(*, symbol: str, side: str, otype: str, quantity: float, price=None, net: str = "testnet",
                reduce_only: bool = False, client_order_id=None, key_env: str = "BINANCE_KEY",
                secret_env: str = "BINANCE_SECRET", opener=None, now_ms=None) -> dict:
    """Place ONE futures order. ``side`` BUY/SELL, ``otype`` LIMIT/MARKET/STOP_MARKET/TAKE_PROFIT_MARKET.
    Idempotent via ``newClientOrderId`` (re-running won't double-place the same id)."""
    key, secret = _creds(key_env, secret_env)
    params = {"symbol": symbol, "side": side, "type": otype, "quantity": quantity}
    if otype == "LIMIT":
        params.update({"price": price, "timeInForce": "GTC"})
    elif otype in ("STOP_MARKET", "TAKE_PROFIT_MARKET"):
        params["stopPrice"] = price
    if reduce_only:
        params["reduceOnly"] = "true"
    if client_order_id:
        params["newClientOrderId"] = client_order_id
    return _call("futures", net, "/fapi/v1/order", params, key=key, secret=secret, method="POST",
                 opener=opener, now_ms=now_ms, timeout=8.0)


def maker_follow(*, symbol: str, side: str, quantity: float, net: str = "testnet", max_repegs: int = 5,
                 poll_sec: float = 1.0, key_env: str = "BINANCE_KEY", secret_env: str = "BINANCE_SECRET",
                 opener=None, sleep_fn=None, price_fn=None) -> dict:
    """Fill ``quantity`` as a MAKER by pegging a LIMIT to the best price and re-pricing (cancel+replace)
    as the market moves — so you join the maker side and pay 0 maker fee. After ``max_repegs`` un-filled
    re-pegs it falls back to a MARKET (taker) to GUARANTEE the fill (基础仓一定到手). ``price_fn`` /
    ``sleep_fn`` injectable for tests. Returns ``{filled, as, price, repegs}``."""
    import time as _t

    sleep_fn = sleep_fn or _t.sleep
    key, secret = _creds(key_env, secret_env)

    def best():
        if price_fn:
            return price_fn()
        d = _call("futures", net, "/fapi/v1/ticker/bookTicker", {"symbol": symbol}, key=key, secret=secret,
                  opener=opener)
        return float(d["bidPrice"]), float(d["askPrice"])

    def status(oid):
        return _call("futures", net, "/fapi/v1/order", {"symbol": symbol, "orderId": oid}, key=key, secret=secret,
                     opener=opener)

    def cancel(oid):
        _call("futures", net, "/fapi/v1/order", {"symbol": symbol, "orderId": oid}, key=key, secret=secret,
              method="DELETE", opener=opener)

    oid = None
    for i in range(max_repegs):
        bid, ask = best()
        px = round(bid if side == "BUY" else ask, 8)     # join the maker side at the current best
        if oid is not None:
            if status(oid).get("status") == "FILLED":
                return {"filled": True, "as": "maker", "price": px, "repegs": i}
            cancel(oid)                                   # price moved -> re-peg
        oid = place_order(symbol=symbol, side=side, otype="LIMIT", quantity=quantity, price=px, net=net,
                          key_env=key_env, secret_env=secret_env, opener=opener).get("orderId")
        sleep_fn(poll_sec)
    if oid is not None:
        if status(oid).get("status") == "FILLED":
            return {"filled": True, "as": "maker", "repegs": max_repegs}
        cancel(oid)
    resp = place_order(symbol=symbol, side=side, otype="MARKET", quantity=quantity, net=net,
                       key_env=key_env, secret_env=secret_env, opener=opener)
    return {"filled": True, "as": "taker(fallback)", "repegs": max_repegs, "resp": resp}


def cancel_all(symbol: str, *, net: str = "testnet", key_env: str = "BINANCE_KEY",
               secret_env: str = "BINANCE_SECRET", opener=None, now_ms=None) -> dict:
    key, secret = _creds(key_env, secret_env)
    return _call("futures", net, "/fapi/v1/allOpenOrders", {"symbol": symbol}, key=key, secret=secret,
                 method="DELETE", opener=opener, now_ms=now_ms)


def set_leverage(symbol: str, leverage: int, *, net: str = "testnet", key_env: str = "BINANCE_KEY",
                 secret_env: str = "BINANCE_SECRET", opener=None, now_ms=None) -> dict:
    """Set the futures leverage for a symbol (POST /fapi/v1/leverage)."""
    key, secret = _creds(key_env, secret_env)
    return _call("futures", net, "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(leverage)},
                 key=key, secret=secret, method="POST", opener=opener, now_ms=now_ms)


def close_position(symbol: str, *, net: str = "testnet", key_env: str = "BINANCE_KEY",
                   secret_env: str = "BINANCE_SECRET", opener=None, retries: int = 3) -> dict:
    """GUARANTEE the symbol is flat: read the live position; if nonzero, fire a MARKET reduceOnly order
    for the full size on the opposite side; re-check and retry up to ``retries``. Also cancels resting
    orders so no stray stop/TP remains. Returns ``{closed, remaining, attempts}``. This is 平仓 that
    does not silently half-fail."""
    def _pos():
        return next((p for p in read_positions(net=net, key_env=key_env, secret_env=secret_env, opener=opener)
                     if p["symbol"] == symbol), None)

    attempts = 0
    for attempts in range(1, retries + 1):
        p = _pos()
        if not p or p["qty"] == 0:
            try:
                cancel_all(symbol, net=net, key_env=key_env, secret_env=secret_env, opener=opener)
            except Exception:  # noqa: BLE001
                pass
            return {"closed": True, "remaining": 0.0, "attempts": attempts - 1}
        place_order(symbol=symbol, side=("SELL" if p["qty"] > 0 else "BUY"), otype="MARKET",
                    quantity=abs(p["qty"]), reduce_only=True, net=net, key_env=key_env,
                    secret_env=secret_env, opener=opener)
    p = _pos()
    return {"closed": bool(not p or p["qty"] == 0), "remaining": (p["qty"] if p else 0.0), "attempts": attempts}


def build_orders(intent: dict, equity: float) -> list:
    """Translate an APPROVED order_intent into the exact venue orders (no network) — entries as LIMIT
    (or MARKET for a follow base), stop as STOP_MARKET (reduceOnly), final target as TAKE_PROFIT_MARKET.
    Quantities sized off ``equity`` × size_cap_frac. Pure: returns the order dicts for review/placement."""
    plan = intent["plan"]
    sym = plan["symbol"]
    sign_d = 1 if plan["direction"] == "long" else -1
    eside = "BUY" if sign_d > 0 else "SELL"
    xside = "SELL" if sign_d > 0 else "BUY"               # exit side (opposite)
    notional = (plan.get("size_cap_frac", 0) or 0) * equity
    ref = plan.get("ref_price") or (plan["entries"][0]["price"] if plan.get("entries") else 0) or 1
    orders = []
    for e in plan.get("entries", []):
        qty = round(notional * e["size_frac"] / (e["price"] or ref), 6)
        f = bool(e.get("follow"))
        orders.append({"symbol": sym, "side": eside, "type": "FOLLOW" if f else "LIMIT", "follow": f,
                       "price": None if f else e["price"], "quantity": qty,
                       "note": "基础·maker跟价(没成交转市价)" if f else "埋伏限价"})
    tot_qty = round(notional / ref, 6)
    orders.append({"symbol": sym, "side": xside, "type": "STOP_MARKET", "price": plan["stop"],
                   "quantity": tot_qty, "reduceOnly": True, "note": "止损"})
    for g in plan.get("targets", []):
        orders.append({"symbol": sym, "side": xside, "type": "TAKE_PROFIT_MARKET", "price": g["price"],
                       "quantity": round(tot_qty * g["size_frac"], 6), "reduceOnly": True, "note": f"止盈{g['label']}"})
    return orders


def execute_intent(intent: dict, *, equity: float, net: str = "testnet", yes: bool = False,
                   key_env: str = "BINANCE_KEY", secret_env: str = "BINANCE_SECRET", opener=None) -> dict:
    """Place the orders for an APPROVED intent on the venue. REFUSES unless ``yes=True`` (the human's
    explicit go) — never auto-fires. Returns the placement receipts. Use testnet first."""
    if not yes:
        raise RuntimeError("execute_intent refuses to fire without yes=True (human approval). Never auto-trade.")
    receipts = []
    for o in build_orders(intent, equity):
        if o.get("follow"):                              # base: maker-follow re-peg (taker fallback)
            rec = maker_follow(symbol=o["symbol"], side=o["side"], quantity=o["quantity"], net=net,
                               key_env=key_env, secret_env=secret_env, opener=opener)
        else:
            rec = place_order(symbol=o["symbol"], side=o["side"], otype=o["type"], quantity=o["quantity"],
                              price=o.get("price"), reduce_only=o.get("reduceOnly", False), net=net,
                              client_order_id=f"{intent.get('id','x')[:24]}-{len(receipts)}",
                              key_env=key_env, secret_env=secret_env, opener=opener)
        receipts.append({"note": o["note"], "resp": rec})
    return {"intent": intent.get("id"), "net": net, "placed": len(receipts), "receipts": receipts}


def main(argv=None):
    import argparse
    import sys

    p = argparse.ArgumentParser(prog="quant.broker", description=__doc__)
    p.add_argument("command", choices=["balance", "positions", "orders", "preview", "execute", "close", "leverage"])
    p.add_argument("--net", default="testnet", choices=["testnet", "mainnet"])
    p.add_argument("--market", default="futures", choices=["futures", "spot"])
    p.add_argument("--symbol", default=None)
    p.add_argument("--intent", default=None, help="path to an approved order_intent JSON (preview/execute)")
    p.add_argument("--equity", type=float, default=None, help="override equity for sizing (else live balance)")
    p.add_argument("--leverage", type=int, default=None, help="leverage to set (leverage command)")
    p.add_argument("--yes", action="store_true", help="REQUIRED to actually place orders (execute/close)")
    args = p.parse_args(argv)

    def emit(o):
        json.dump(o, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")

    if args.command == "balance":
        emit(read_balance(market=args.market, net=args.net))
    elif args.command == "positions":
        emit(read_positions(net=args.net))
    elif args.command == "orders":
        emit(read_open_orders(args.symbol, net=args.net))
    elif args.command == "leverage":
        emit(set_leverage(args.symbol, args.leverage, net=args.net))
    elif args.command == "close":
        if not args.yes:
            raise SystemExit("close refuses without --yes (it places a real market reduceOnly order)")
        emit(close_position(args.symbol, net=args.net))
    else:
        intent = json.load(open(args.intent))
        eq = args.equity if args.equity is not None else read_balance(net=args.net).get("equity", 0)
        if args.command == "preview":
            emit({"equity": eq, "orders": build_orders(intent, eq), "note": "preview only — nothing placed"})
        else:
            emit(execute_intent(intent, equity=eq, net=args.net, yes=args.yes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
