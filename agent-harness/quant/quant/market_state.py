"""MarketState — multi-timeframe regime assembly (the cross-plane read contract).

Reads bars from the market store (``quant.market_store``), classifies the regime
on each timeframe (``quant.regime``), and fuses them STRICTLY top-down into one
``MarketState``:

  * the **HTF (default 1d) is the sole bias source**;
  * the **confirm timeframe (default 4h) may only veto a bias to flat** — never
    flip it (a perfect 4h long inside a 1d downtrend does not create a long);
  * a change-point ``stand_down`` on EITHER timeframe forces ``flat``.

This is the deterministic "slow-clock brain": no LLM, no orders, fully
replayable. The strategy layer reads ``composite_bias`` + ``stand_down``; the
agent layer reads ``regime_label`` + components for its narrative. Numerics stay
in ``~/quant``; nothing here writes to the knowledge vault.

CLI seam (shelled to by the stdlib-only main repo, see SCHEMA.md §7):

    python -m quant.market_state --symbol BTCUSDT [--asof YYYY-MM-DD] \
        [--htf 1d] [--confirm 4h] [--root R]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from quant import market_store, regime
from quant.market_store import DEFAULT_ROOT


@dataclass(slots=True)
class MarketState:
    symbol: str
    as_of: int                  # HTF last bar bucket_ts (epoch micros, UTC)
    htf_tf: str
    confirm_tf: str
    composite_bias: str         # long | short | flat
    regime_label: str           # the HTF label (headline regime)
    direction: str              # HTF direction: up | down | flat
    vol_bucket: str             # low | normal | high (HTF)
    stand_down: bool            # change-point veto on either timeframe
    per_tf: dict                # {tf: label}
    htf: dict                   # full RegimeResult for the HTF
    confirm: dict               # full RegimeResult for the confirm timeframe
    schema_version: str = "ms-v1"

    def to_dict(self) -> dict:
        return asdict(self)


def _compose_bias(htf: regime.RegimeResult, confirm: regime.RegimeResult) -> str:
    """Top-down bias: HTF decides; confirm may only veto to flat; CP => flat."""
    # Need both timeframes informative. Insufficient data on EITHER (e.g. a
    # missing/too-short confirm series) is "unknown", not "range" — stand aside.
    if htf.insufficient or confirm.insufficient:
        return "flat"
    if htf.stand_down or confirm.stand_down:
        return "flat"
    base = {"up": "long", "down": "short"}.get(htf.direction, "flat")
    if base == "flat":
        return "flat"
    # the confirm timeframe can stand us aside, never flip the bias
    if base == "long" and confirm.direction == "down":
        return "flat"
    if base == "short" and confirm.direction == "up":
        return "flat"
    return base


def build_market_state(
    symbol: str,
    *,
    root: Path | str = DEFAULT_ROOT,
    asof=None,
    htf: str = "1d",
    confirm: str = "4h",
    live: bool = False,
    venue: str = "coinbase",
) -> MarketState:
    """Assemble the MarketState for ``symbol`` — two data paths, IDENTICAL shape.

    * **stored** (default): read materialized ``bars_<freq>`` from the market
      store. ``asof`` is point-in-time — bars after it are dropped
      (``market_store.bars``) — so this is safe inside a backtest loop.
    * **live** (``live=True``): fetch the most recent candles straight from a
      public venue (``quant.live``; Coinbase/Kraken) and classify those. This is
      the CURRENT read for the scheduled indicator when the store is stale; it
      ignores ``asof``/``root``. Coinbase has no native 4h granularity, so a
      default ``4h`` confirm is mapped to ``6h`` for that venue (mirrors
      ``quant.live._CONFIRM_TF``).
    """
    if live:
        from quant import live as live_mod  # lazy: keep urllib off the stored path
        if venue == "coinbase" and confirm == "4h":
            confirm = "6h"
        htf_bars = live_mod.fetch_candles(symbol, htf, venue=venue)
        confirm_bars = live_mod.fetch_candles(symbol, confirm, venue=venue)
    else:
        htf_bars = market_store.bars(symbol, htf, "1970-01-01", "2100-01-01", root=root, asof=asof)
        confirm_bars = market_store.bars(symbol, confirm, "1970-01-01", "2100-01-01", root=root, asof=asof)
    htf_r = regime.classify(htf_bars)
    confirm_r = regime.classify(confirm_bars)
    return MarketState(
        symbol=symbol,
        as_of=htf_r.as_of,
        htf_tf=htf,
        confirm_tf=confirm,
        composite_bias=_compose_bias(htf_r, confirm_r),
        regime_label=htf_r.label,
        direction=htf_r.direction,
        vol_bucket=htf_r.vol_bucket,
        stand_down=bool(htf_r.stand_down or confirm_r.stand_down),
        per_tf={htf: htf_r.label, confirm: confirm_r.label},
        htf=htf_r.to_dict(),
        confirm=confirm_r.to_dict(),
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="quant.market_state",
        description="Multi-timeframe regime read; emits a MarketState JSON to stdout "
        "(the omni-hub shell-out seam). Deterministic, point-in-time, no LLM.",
    )
    p.add_argument("--symbol", required=True)
    p.add_argument("--root", default=str(DEFAULT_ROOT))
    p.add_argument("--asof", default=None, help="point-in-time cutoff (default: latest)")
    p.add_argument("--htf", default="1d", help="bias timeframe (default 1d)")
    p.add_argument("--confirm", default="4h", help="confirm timeframe (default 4h)")
    p.add_argument("--live", action="store_true",
                   help="fetch current candles from a public venue (quant.live) instead of "
                        "the stored bars — the fresh read for the scheduled indicator")
    p.add_argument("--venue", default="coinbase", choices=["coinbase", "kraken", "binance"],
                   help="live venue (default coinbase; only with --live)")
    p.add_argument("--format", choices=["json"], default="json")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ms = build_market_state(
        args.symbol,
        root=Path(args.root).expanduser(),
        asof=args.asof,
        htf=args.htf,
        confirm=args.confirm,
        live=args.live,
        venue=args.venue,
    )
    json.dump(ms.to_dict(), sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # `python -m quant.market_state ...`
    raise SystemExit(main())
