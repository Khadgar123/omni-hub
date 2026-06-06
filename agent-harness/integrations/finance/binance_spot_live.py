#!/usr/bin/env python3
"""Minimal Binance Spot live API check.

This shim is for connectivity and permission testing only. It supports
Binance's signed account endpoint and signed test-order endpoint; it does not
implement the real order endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from getpass import getpass
from typing import Any, Callable


DEFAULT_BASE_URL = "https://api.binance.com"
DEFAULT_KEY_REF = "local:omni-hub/api/binance/key"
DEFAULT_SECRET_REF = "local:omni-hub/api/binance/secret"
IP_CHECK_URLS = (
    "https://checkip.amazonaws.com",
    "https://ipv4.icanhazip.com",
    "https://ifconfig.me/ip",
    "https://api64.ipify.org",
)


class BinanceLiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class BinanceCredentials:
    api_key: str
    api_secret: str

    @property
    def present(self) -> bool:
        return bool(self.api_key and self.api_secret)


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def resolve_secret_ref(secret_ref: str) -> str:
    if not secret_ref:
        return ""
    try:
        from omni_hub.secrets import resolve_secret_ref as resolve
    except Exception:
        return ""
    try:
        return resolve(secret_ref)
    except Exception:
        return ""


def load_credentials(args: argparse.Namespace) -> BinanceCredentials:
    api_key = (
        args.api_key
        or os.environ.get("BINANCE_API_KEY", "")
        or resolve_secret_ref(args.api_key_ref)
    ).strip()
    api_secret = (
        args.api_secret
        or os.environ.get("BINANCE_API_SECRET", "")
        or resolve_secret_ref(args.api_secret_ref)
    ).strip()
    return BinanceCredentials(api_key=api_key, api_secret=api_secret)


def validate_credentials_shape(creds: BinanceCredentials) -> list[str]:
    issues: list[str] = []
    if not creds.api_key:
        issues.append("API key is empty")
    if not creds.api_secret:
        issues.append("API secret is empty")
    if any(ch.isspace() for ch in creds.api_key):
        issues.append("API key contains whitespace")
    if any(ch.isspace() for ch in creds.api_secret):
        issues.append("API secret contains whitespace")
    return issues


def store_credentials(creds: BinanceCredentials) -> dict[str, str]:
    from omni_hub.secrets import store_api_key

    return {
        "api_key_ref": store_api_key("api/binance/key", creds.api_key),
        "api_secret_ref": store_api_key("api/binance/secret", creds.api_secret),
    }


def sign_params(params: dict[str, Any], api_secret: str) -> str:
    query = urllib.parse.urlencode(params, doseq=True)
    signature = hmac.new(
        api_secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{query}&signature={signature}"


def request_json(
    method: str,
    base_url: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    api_key: str = "",
    timeout: float = 15.0,
    opener: Callable[[urllib.request.Request, float], Any] | None = None,
) -> Any:
    params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    query = urllib.parse.urlencode(params, doseq=True)
    url = base_url.rstrip("/") + path
    data = None
    if method.upper() == "GET":
        if query:
            url = f"{url}?{query}"
    else:
        data = query.encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method.upper())
    req.add_header("User-Agent", "omni-hub-binance-live-check/0.1")
    if api_key:
        req.add_header("X-MBX-APIKEY", api_key)
    if method.upper() != "GET":
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise BinanceLiveError(
            f"HTTP {exc.code} from Binance {path}: {body[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise BinanceLiveError(f"network error for Binance {path}: {exc}") from exc
    except TimeoutError as exc:
        raise BinanceLiveError(f"timeout for Binance {path}") from exc


def request_text(url: str, *, timeout: float = 5.0) -> str:
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", "omni-hub-binance-live-check/0.1")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def detect_public_ips(timeout: float = 5.0) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for url in IP_CHECK_URLS:
        value = request_text(url, timeout=timeout).strip()
        if not value:
            continue
        family = "ipv6" if ":" in value else "ipv4"
        key = (family, value)
        if key in seen:
            continue
        seen.add(key)
        out.append({"family": family, "ip": value, "source": url})
    return out


def signed_request(
    method: str,
    base_url: str,
    path: str,
    creds: BinanceCredentials,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> Any:
    if not creds.present:
        raise BinanceLiveError(
            "BINANCE_API_KEY/BINANCE_API_SECRET not configured; "
            "or store local:omni-hub/api/binance/{key,secret}."
        )
    payload = dict(params or {})
    payload.setdefault("recvWindow", 5000)
    payload["timestamp"] = int(time.time() * 1000)
    signed = sign_params(payload, creds.api_secret)
    signed_params = dict(urllib.parse.parse_qsl(signed, keep_blank_values=True))
    return request_json(
        method,
        base_url,
        path,
        params=signed_params,
        api_key=creds.api_key,
        timeout=timeout,
    )


def summarize_account(account: dict[str, Any]) -> dict[str, Any]:
    balances = account.get("balances", [])
    nonzero_assets = []
    for row in balances:
        try:
            free = float(row.get("free", 0) or 0)
            locked = float(row.get("locked", 0) or 0)
        except (TypeError, ValueError):
            continue
        if free or locked:
            nonzero_assets.append(row.get("asset", ""))
    return {
        "can_trade": account.get("canTrade"),
        "can_withdraw": account.get("canWithdraw"),
        "can_deposit": account.get("canDeposit"),
        "account_type": account.get("accountType"),
        "permissions": account.get("permissions", []),
        "nonzero_asset_count": len(nonzero_assets),
        "nonzero_assets": nonzero_assets[:30],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("BINANCE_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-secret", default="")
    parser.add_argument("--api-key-ref", default=os.environ.get("BINANCE_API_KEY_REF", DEFAULT_KEY_REF))
    parser.add_argument("--api-secret-ref", default=os.environ.get("BINANCE_API_SECRET_REF", DEFAULT_SECRET_REF))
    parser.add_argument("--timeout", type=float, default=15.0)

    sub = parser.add_subparsers(dest="command", required=True)
    configure = sub.add_parser(
        "configure",
        help="Prompt for Binance credentials, validate account access, then store locally.",
    )
    configure.add_argument(
        "--store-even-if-invalid",
        action="store_true",
        help="Overwrite local credentials even if Binance account validation fails.",
    )
    sub.add_parser("ip", help="Show public IPs seen from this machine.")
    sub.add_parser("ping", help="Public ping/time check; no credentials needed.")
    sub.add_parser("account", help="Signed read-only account permission check.")

    order = sub.add_parser("order-test", help="Signed test order; does not place a live order.")
    order.add_argument("--symbol", required=True)
    order.add_argument("--side", required=True, choices=["BUY", "SELL"])
    order.add_argument("--type", required=True, choices=["MARKET", "LIMIT"])
    order.add_argument("--quantity", required=True)
    order.add_argument("--price", default="")
    order.add_argument("--time-in-force", default="GTC")
    order.add_argument(
        "--i-understand-this-is-a-live-api-test",
        action="store_true",
        help="Required for order-test. Calls Binance test-order endpoint only.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    creds = load_credentials(args)

    try:
        if args.command == "ip":
            print(json.dumps({
                "ok": True,
                "public_ips": detect_public_ips(timeout=min(args.timeout, 8.0)),
            }, ensure_ascii=False, indent=2))
            return 0

        if args.command == "configure":
            public_ips = detect_public_ips(timeout=min(args.timeout, 8.0))
            print(json.dumps({
                "base_url": args.base_url,
                "public_ips_before_validation": public_ips,
            }, ensure_ascii=False, indent=2))
            api_key = getpass("Paste Binance API Key: ").strip()
            api_secret = getpass("Paste Binance API Secret: ").strip()
            api_secret_again = getpass("Paste Binance API Secret again: ").strip()
            if api_secret != api_secret_again:
                raise BinanceLiveError("API secret entries did not match; not stored.")
            candidate = BinanceCredentials(api_key=api_key, api_secret=api_secret)
            issues = validate_credentials_shape(candidate)
            if issues:
                raise BinanceLiveError("; ".join(issues) + "; not stored.")
            validation: dict[str, Any]
            try:
                account = signed_request(
                    "GET",
                    args.base_url,
                    "/api/v3/account",
                    candidate,
                    params={},
                    timeout=args.timeout,
                )
                validation = {
                    "ok": True,
                    "account": summarize_account(account),
                }
            except BinanceLiveError as exc:
                validation = {"ok": False, "error": str(exc)}
                if not args.store_even_if_invalid:
                    print(json.dumps({
                        "ok": False,
                        "base_url": args.base_url,
                        "api_key": mask_secret(candidate.api_key),
                        "public_ips_before_validation": public_ips,
                        "stored": False,
                        "validation": validation,
                        "hint": (
                            "Not stored. Add the public IP to Binance trusted IPs, "
                            "enable Reading, or recheck the secret. Use "
                            "--store-even-if-invalid only when you explicitly want "
                            "to replace the local old key for later retry."
                        ),
                    }, ensure_ascii=False, indent=2), file=sys.stderr)
                    return 2
            refs = store_credentials(candidate)
            print(json.dumps({
                "ok": True,
                "base_url": args.base_url,
                "api_key": mask_secret(candidate.api_key),
                "public_ips_before_validation": public_ips,
                "stored": refs,
                "validation": validation,
            }, ensure_ascii=False, indent=2))
            return 0

        if args.command == "ping":
            ping = request_json("GET", args.base_url, "/api/v3/ping", timeout=args.timeout)
            server_time = request_json("GET", args.base_url, "/api/v3/time", timeout=args.timeout)
            print(json.dumps({
                "ok": True,
                "base_url": args.base_url,
                "ping": ping,
                "server_time": server_time,
            }, ensure_ascii=False, indent=2))
            return 0

        if args.command == "account":
            account = signed_request(
                "GET",
                args.base_url,
                "/api/v3/account",
                creds,
                params={},
                timeout=args.timeout,
            )
            print(json.dumps({
                "ok": True,
                "base_url": args.base_url,
                "api_key": mask_secret(creds.api_key),
                "account": summarize_account(account),
            }, ensure_ascii=False, indent=2))
            return 0

        if args.command == "order-test":
            if not args.i_understand_this_is_a_live_api_test:
                raise BinanceLiveError(
                    "order-test requires --i-understand-this-is-a-live-api-test"
                )
            params = {
                "symbol": args.symbol,
                "side": args.side,
                "type": args.type,
                "quantity": args.quantity,
            }
            if args.type == "LIMIT":
                if not args.price:
                    raise BinanceLiveError("LIMIT order-test requires --price")
                params["price"] = args.price
                params["timeInForce"] = args.time_in_force
            result = signed_request(
                "POST",
                args.base_url,
                "/api/v3/order/test",
                creds,
                params=params,
                timeout=args.timeout,
            )
            print(json.dumps({
                "ok": True,
                "base_url": args.base_url,
                "api_key": mask_secret(creds.api_key),
                "test_order": {
                    "symbol": args.symbol,
                    "side": args.side,
                    "type": args.type,
                    "quantity": args.quantity,
                    "price": args.price,
                },
                "result": result,
            }, ensure_ascii=False, indent=2))
            return 0

    except BinanceLiveError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
