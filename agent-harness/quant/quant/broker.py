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
import urllib.parse
import urllib.request

FAPI = {"mainnet": "https://fapi.binance.com", "testnet": "https://testnet.binancefuture.com"}
SPOT = {"mainnet": "https://api.binance.com", "testnet": "https://testnet.binance.vision"}
_UA = "omni-hub-quant-broker/0.1"


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


# ---------------------------------------------------------------- READ side
def read_balance(*, market: str = "futures", net: str = "testnet", key_env: str = "BINANCE_KEY",
                 secret_env: str = "BINANCE_SECRET", opener=None, now_ms=None) -> dict:
    """Account balance/equity. Futures: USDT/USDC wallet balance + available. Spot: nonzero free assets.
    READ-only (a read key suffices). Returns ``{equity, available, asset, market, net}`` (futures)."""
    key, secret = _creds(key_env, secret_env)
    if market == "futures":
        data = _signed(FAPI[net], "/fapi/v2/balance", {}, key=key, secret=secret, opener=opener, now_ms=now_ms)
        row = next((x for x in data if x.get("asset") in ("USDT", "USDC")), data[0] if data else {})
        return {"equity": float(row.get("balance", 0) or 0), "available": float(row.get("availableBalance", 0) or 0),
                "asset": row.get("asset"), "market": "futures", "net": net}
    data = _signed(SPOT[net], "/api/v3/account", {}, key=key, secret=secret, opener=opener, now_ms=now_ms)
    bals = {b["asset"]: float(b["free"]) for b in data.get("balances", []) if float(b.get("free", 0)) > 0}
    return {"balances": bals, "market": "spot", "net": net}


def read_positions(*, net: str = "testnet", key_env: str = "BINANCE_KEY", secret_env: str = "BINANCE_SECRET",
                   opener=None, now_ms=None) -> list:
    """Open futures positions (nonzero positionAmt). Returns ``[{symbol, qty, entry, mark, uPnl, leverage}]``."""
    key, secret = _creds(key_env, secret_env)
    data = _signed(FAPI[net], "/fapi/v2/positionRisk", {}, key=key, secret=secret, opener=opener, now_ms=now_ms)
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
    data = _signed(FAPI[net], "/fapi/v1/openOrders", params, key=key, secret=secret, opener=opener, now_ms=now_ms)
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
    return _signed(FAPI[net], "/fapi/v1/order", params, key=key, secret=secret, method="POST",
                   opener=opener, now_ms=now_ms)


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
        d = _signed(FAPI[net], "/fapi/v1/ticker/bookTicker", {"symbol": symbol}, key=key, secret=secret, opener=opener)
        return float(d["bidPrice"]), float(d["askPrice"])

    def status(oid):
        return _signed(FAPI[net], "/fapi/v1/order", {"symbol": symbol, "orderId": oid}, key=key, secret=secret,
                       opener=opener)

    def cancel(oid):
        _signed(FAPI[net], "/fapi/v1/order", {"symbol": symbol, "orderId": oid}, key=key, secret=secret,
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
    return _signed(FAPI[net], "/fapi/v1/allOpenOrders", {"symbol": symbol}, key=key, secret=secret,
                   method="DELETE", opener=opener, now_ms=now_ms)


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
    p.add_argument("command", choices=["balance", "positions", "orders", "preview", "execute"])
    p.add_argument("--net", default="testnet", choices=["testnet", "mainnet"])
    p.add_argument("--market", default="futures", choices=["futures", "spot"])
    p.add_argument("--symbol", default=None)
    p.add_argument("--intent", default=None, help="path to an approved order_intent JSON (preview/execute)")
    p.add_argument("--equity", type=float, default=None, help="override equity for sizing (else live balance)")
    p.add_argument("--yes", action="store_true", help="REQUIRED to actually place orders (execute)")
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
