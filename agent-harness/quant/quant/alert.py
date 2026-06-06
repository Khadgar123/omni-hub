"""TradeAlert — the notify+manual execution surface (NO auto-orders).

A regime-gated strategy intent + the current MarketState becomes a SUGGESTED
MANUAL ACTION delivered to the human, who decides and trades on their exchange.
No code path here places an order — this is autonomy level L0 by construction
(the practitioner + paper consensus: keep the machine off the trade/risk path).

``current_alerts`` answers "what would the strategies suggest right now, given
the latest stored data and the top-down regime?" — the thing a watcher/cron
runs to produce notifications. Delivery to email/Feishu/Discord is the Interface
Plane's job (Session A); here we produce the alert + append it to a JSONL feed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_ACTION = {"long": "buy", "flat": "exit", "short": "sell"}


@dataclass(slots=True)
class TradeAlert:
    asof: int                 # signal-bar bucket_ts (epoch µs, UTC)
    symbol: str
    action: str               # buy | exit | sell (SUGGESTION — human executes)
    strategy_id: str
    timeframe: str
    regime: str
    composite_bias: str
    conviction: float
    ref_price: float
    suggested_stop: float
    rationale: str
    stand_down: bool
    kind: str = "trade_suggestion"
    source: str = "quant"
    schema_version: str = "alert-v1"

    def to_dict(self) -> dict:
        return asdict(self)


def intent_to_alert(intent, state) -> TradeAlert:
    """Map a StrategyIntent + MarketState into a human-facing suggestion."""
    return TradeAlert(
        asof=intent.asof,
        symbol=intent.symbol,
        action=_ACTION.get(intent.direction, "hold"),
        strategy_id=intent.strategy_id,
        timeframe=intent.timeframe,
        regime=getattr(state, "regime_label", "?"),
        composite_bias=getattr(state, "composite_bias", "?"),
        conviction=round(float(intent.conviction), 3),
        ref_price=float(intent.entry_ref),
        suggested_stop=float(intent.stop_price),
        rationale=intent.rationale,
        stand_down=bool(getattr(state, "stand_down", False)),
    )


def emit(alert: TradeAlert, path) -> Path:
    """Append one alert as a JSONL line (the notification feed)."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(alert.to_dict(), ensure_ascii=False) + "\n")
    return p


def _default_feed():
    return Path("~/quant/alerts.jsonl").expanduser()


def current_alerts(symbol, *, root=None, lookback_days=160, htf="1d", confirm="4h",
                   source="1s", strategies=None, emit_path=None, now=None):
    """Produce trade suggestions as of the latest stored bar (entry-from-flat).

    Resamples the recent ``lookback_days`` of ``source`` bars, assembles the
    top-down MarketState at the last bar, and runs each strategy's gated entry
    check from a flat position. Returns ``(alerts, state)``. ``stand_down`` /
    flat bias naturally yields no suggestions (stand aside).
    """
    from quant import market_store, regime
    from quant import resample as rs
    from quant.backtest.harness import _build_states
    from quant.strategy.base import gated_evaluate
    from quant.strategy.registry import default_strategies

    root = root if root is not None else market_store.DEFAULT_ROOT
    strategies = strategies if strategies is not None else default_strategies()
    now = now or datetime.now(UTC)
    start = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    tf = strategies[0].timeframe

    bars = rs.resample(symbol, tf, root=root, source_interval=source, start=start)
    if not bars:
        return [], None
    htf_bars = rs.resample(symbol, htf, root=root, source_interval=source, start=start)
    confirm_bars = rs.resample(symbol, confirm, root=root, source_interval=source, start=start)
    states = _build_states(bars, regime.classify_series(htf_bars),
                           regime.classify_series(confirm_bars), symbol)
    state = states[-1]
    window = bars[-500:]

    alerts = []
    for strat in strategies:
        intent = gated_evaluate(strat, window, state, position_qty=0.0)  # entry-from-flat
        if intent is not None and intent.direction != "flat":
            a = intent_to_alert(intent, state)
            alerts.append(a)
            if emit_path:
                emit(a, emit_path)
    return alerts, state


def main(argv=None):
    import argparse
    import sys

    p = argparse.ArgumentParser(prog="quant.alert", description=__doc__)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--root", default=None)
    p.add_argument("--lookback-days", type=int, default=160)
    p.add_argument("--emit", dest="emit_path", nargs="?", const=str(_default_feed()), default=None,
                   help="append alerts to this JSONL (default ~/quant/alerts.jsonl)")
    args = p.parse_args(argv)
    root = Path(args.root).expanduser() if args.root else None
    alerts, state = current_alerts(args.symbol, root=root, lookback_days=args.lookback_days,
                                   emit_path=args.emit_path)
    out = {
        "symbol": args.symbol,
        "regime": getattr(state, "regime_label", None),
        "composite_bias": getattr(state, "composite_bias", None),
        "stand_down": getattr(state, "stand_down", None),
        "n_suggestions": len(alerts),
        "suggestions": [a.to_dict() for a in alerts],
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
