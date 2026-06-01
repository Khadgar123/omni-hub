"""Live market-data (Coinbase / Kraken public REST) -> real-time MarketState + alerts.

Per the data-source decision: the strict Binance signed API is dropped; live
second/minute monitoring uses the fast, stable, US-friendly public feeds
(Coinbase, Kraken). Pure-stdlib ``urllib`` (dep-light, like binance_spot_live);
the HTTP getter is injectable so the candle mappers are unit-testable with NO
network. This is the always-on "盯盘" sensor for the NOTIFY+MANUAL surface: it
fetches recent candles, assembles the top-down regime, runs the gated strategies
from flat, and emits TradeAlert suggestions. It NEVER places an order.
"""

from __future__ import annotations

import json
import urllib.request
from types import SimpleNamespace

from quant import regime
from quant.market_state import _compose_bias

# store symbol -> venue product symbols
SYMBOL_MAP = {
    "BTCUSDT": {"coinbase": "BTC-USD", "kraken": "XBTUSD", "binance": "BTCUSDT"},
    "ETHUSDT": {"coinbase": "ETH-USD", "kraken": "ETHUSD", "binance": "ETHUSDT"},
    "BTC-USD": {"coinbase": "BTC-USD", "kraken": "XBTUSD", "binance": "BTCUSDT"},
    "ETH-USD": {"coinbase": "ETH-USD", "kraken": "ETHUSD", "binance": "ETHUSDT"},
}
# Coinbase has NO 4h granularity (only 1m/5m/15m/1h/6h/1d); Kraken + Binance do.
# Binance fapi is the venue reachable from CN/Asia where Coinbase/Kraken are geo-blocked,
# so it is tried FIRST by the scheduled indicator (quant_daily _SOURCES).
_COINBASE_GRAN = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}
_KRAKEN_INT = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
_BINANCE_INT = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "6h": "6h", "1d": "1d"}
# venue-appropriate mid "confirm" timeframe (Coinbase lacks 4h -> use 6h; Kraken/Binance have 4h)
_CONFIRM_TF = {"coinbase": "6h", "kraken": "4h", "binance": "4h"}
_UA = "omni-hub-quant-live/0.1"
_MICROS = 1_000_000


def _get_json(url, *, opener=None, timeout=15.0):
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with opener(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _product(symbol, venue):
    m = SYMBOL_MAP.get(symbol.upper())
    if not m or venue not in m:
        raise ValueError(f"no {venue} mapping for {symbol!r}")
    return m[venue]


def coinbase_candles(raw) -> list[dict]:
    """Coinbase ``[[time(s), low, high, open, close, volume], ...]`` (newest-first)
    -> ascending bar dicts."""
    out = []
    for row in raw:
        t, low, high, op, close, vol = row[0], row[1], row[2], row[3], row[4], row[5]
        out.append({"bucket_ts": int(t) * _MICROS, "open": float(op), "high": float(high),
                    "low": float(low), "close": float(close), "volume": float(vol),
                    "vwap": float(close), "trades": 0})
    out.sort(key=lambda b: b["bucket_ts"])
    return out


def kraken_ohlc(raw, pair) -> list[dict]:
    """Kraken OHLC ``result[pair] = [[time, o, h, l, c, vwap, volume, count], ...]``."""
    result = raw.get("result", {})
    key = next((k for k in result if k != "last"), None)
    rows = result.get(key, []) if key else []
    out = []
    for r in rows:
        out.append({"bucket_ts": int(r[0]) * _MICROS, "open": float(r[1]), "high": float(r[2]),
                    "low": float(r[3]), "close": float(r[4]), "vwap": float(r[5]),
                    "volume": float(r[6]), "trades": int(r[7])})
    out.sort(key=lambda b: b["bucket_ts"])
    return out


def binance_klines(raw) -> list[dict]:
    """Binance fapi klines ``[[openTime(ms), o, h, l, c, vol, closeTime, quoteVol,
    trades, ...], ...]`` -> ascending bar dicts (openTime ms -> µs)."""
    out = []
    for r in raw:
        out.append({"bucket_ts": int(r[0]) * 1000, "open": float(r[1]), "high": float(r[2]),
                    "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]),
                    "vwap": float(r[4]), "trades": int(r[8]) if len(r) > 8 else 0})
    out.sort(key=lambda b: b["bucket_ts"])
    return out


def fetch_candles(symbol, interval, *, venue="coinbase", opener=None, timeout=15.0) -> list[dict]:
    product = _product(symbol, venue)
    if venue == "coinbase":
        g = _COINBASE_GRAN.get(interval)
        if g is None:
            raise ValueError(f"coinbase has no native {interval} granularity")
        url = f"https://api.exchange.coinbase.com/products/{product}/candles?granularity={g}"
        return coinbase_candles(_get_json(url, opener=opener, timeout=timeout))
    if venue == "kraken":
        iv = _KRAKEN_INT.get(interval)
        if iv is None:
            raise ValueError(f"kraken has no {interval} interval")
        url = f"https://api.kraken.com/0/public/OHLC?pair={product}&interval={iv}"
        return kraken_ohlc(_get_json(url, opener=opener, timeout=timeout), product)
    if venue == "binance":
        iv = _BINANCE_INT.get(interval)
        if iv is None:
            raise ValueError(f"binance has no {interval} interval")
        url = (f"https://fapi.binance.com/fapi/v1/klines?symbol={product}"
               f"&interval={iv}&limit=300")
        return binance_klines(_get_json(url, opener=opener, timeout=timeout))
    raise ValueError(f"unknown venue {venue!r}")


def live_market_state(symbol, *, venue="coinbase", htf="1d", confirm=None, opener=None):
    """Assemble the top-down MarketState from live candles (no store needed)."""
    if confirm is None:
        confirm = _CONFIRM_TF.get(venue, "4h")
    htf_bars = fetch_candles(symbol, htf, venue=venue, opener=opener)
    confirm_bars = fetch_candles(symbol, confirm, venue=venue, opener=opener)
    h = regime.classify(htf_bars)
    c = regime.classify(confirm_bars)
    return SimpleNamespace(
        symbol=symbol,
        regime_label=h.label,
        composite_bias=_compose_bias(h, c),
        stand_down=bool(h.stand_down or c.stand_down),
        htf=h.to_dict(),
        confirm=c.to_dict(),
        venue=venue,
    )


def live_alerts(symbol, *, venue="coinbase", tf="1h", strategies=None, opener=None, emit_path=None):
    """Fetch live candles + regime, run gated strategies from flat, emit suggestions."""
    from quant import alert as alert_mod
    from quant.strategy.base import gated_evaluate
    from quant.strategy.registry import default_strategies

    strategies = strategies if strategies is not None else default_strategies()
    state = live_market_state(symbol, venue=venue, opener=opener)
    bars = fetch_candles(symbol, tf, venue=venue, opener=opener)
    alerts = []
    for strat in strategies:
        intent = gated_evaluate(strat, bars, state, position_qty=0.0)
        if intent is not None and intent.direction != "flat":
            a = alert_mod.intent_to_alert(intent, state)
            a.source = f"live:{venue}"
            alerts.append(a)
            if emit_path:
                alert_mod.emit(a, emit_path)
    return alerts, state


def watch_loop(symbols, *, venue="coinbase", interval=300, emit_path=None, max_iters=None,
               sleep_fn=None, on_tick=None):
    """Always-on sensor for launchd: poll live alerts, emit TradeAlerts, and log a
    line per (symbol, tick) flagging regime/bias/stand_down CHANGES (so a notifier
    can alert on transitions, not every tick). Never places an order.

    ``max_iters``/``sleep_fn`` make it bounded + testable; default runs forever.
    """
    import time as _time

    sleep_fn = sleep_fn or _time.sleep
    last = {}
    i = 0
    while max_iters is None or i < max_iters:
        for symbol in symbols:
            try:
                alerts, st = live_alerts(symbol, venue=venue, emit_path=emit_path)
                key = (st.regime_label, st.composite_bias, st.stand_down)
                changed = symbol in last and last[symbol] != key
                last[symbol] = key
                rec = {"symbol": symbol, "venue": venue, "regime": st.regime_label,
                       "bias": st.composite_bias, "stand_down": st.stand_down,
                       "n_suggestions": len(alerts), "regime_changed": changed}
            except Exception as exc:  # never let one bad poll kill the watcher
                rec = {"symbol": symbol, "venue": venue, "error": str(exc)}
            (on_tick or (lambda r: print(json.dumps(r, ensure_ascii=False, default=str), flush=True)))(rec)
        i += 1
        if max_iters is not None and i >= max_iters:
            break
        sleep_fn(interval)
    return i


def main(argv=None):
    import argparse
    import sys
    from pathlib import Path

    p = argparse.ArgumentParser(prog="quant.live", description=__doc__)
    p.add_argument("command", choices=["state", "alerts", "watch"])
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--symbols", default=None, help="comma-separated (watch); default --symbol")
    p.add_argument("--venue", default="coinbase", choices=["coinbase", "kraken", "binance"])
    p.add_argument("--interval", type=int, default=300, help="watch poll seconds")
    p.add_argument("--emit", dest="emit_path", default=None)
    args = p.parse_args(argv)

    if args.command == "watch":
        syms = [s.strip() for s in (args.symbols or args.symbol).split(",") if s.strip()]
        emit_path = Path(args.emit_path).expanduser() if args.emit_path else None
        watch_loop(syms, venue=args.venue, interval=args.interval, emit_path=emit_path)
        return 0

    if args.command == "state":
        st = live_market_state(args.symbol, venue=args.venue)
        out = {"symbol": st.symbol, "venue": st.venue, "regime": st.regime_label,
               "composite_bias": st.composite_bias, "stand_down": st.stand_down}
    else:
        emit_path = Path(args.emit_path).expanduser() if args.emit_path else None
        alerts, st = live_alerts(args.symbol, venue=args.venue, emit_path=emit_path)
        out = {"symbol": args.symbol, "venue": args.venue, "regime": st.regime_label,
               "composite_bias": st.composite_bias, "stand_down": st.stand_down,
               "n_suggestions": len(alerts), "suggestions": [a.to_dict() for a in alerts]}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
