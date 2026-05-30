"""Run OUR strategy library inside NautilusTrader via one adapter (the pivot core).

Instead of rewriting each strategy in nautilus's API, ``NautilusStrategyAdapter``
drives our existing, unit-tested ``Strategy`` objects (``quant.strategy``) + the
regime gate (``gated_evaluate``) from nautilus's ``on_bar``: it keeps a rolling
window of our bar-dicts, computes a regime state (``quant.regime``), calls
``gated_evaluate``, and translates the resulting ``StrategyIntent`` into long-only
spot orders. So all 7 strategies + the gate run in the real engine with zero
reimplementation, and ``backtest_strategy`` feeds nautilus's returns into OUR
validation (PSR now; PBO/DSR in the sweep) — the moat, on the mature base.

Note: regime here is single-timeframe (classify on the strategy-tf window); the
strict 1d/4h MTF gate is a refinement (multi-bar subscription) tracked separately.
"""

from __future__ import annotations

from collections import deque
from decimal import Decimal
from types import SimpleNamespace

from nautilus_trader.config import PositiveInt, StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from quant import regime as regime_mod
from quant.strategy.base import gated_evaluate
from quant.strategy.registry import by_id

_BIAS = {"up": "long", "strong_up": "long", "down": "short", "strong_down": "short"}


class AdapterConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    strategy_id: str
    symbol: str
    trade_size: Decimal
    warmup: PositiveInt = 300


class NautilusStrategyAdapter(Strategy):
    """Drives a quant.strategy ``Strategy`` (by id) + regime gate inside nautilus."""

    def __init__(self, config: AdapterConfig) -> None:
        super().__init__(config)
        self.instrument = None
        self.strat = by_id(config.strategy_id)
        self.window: deque[dict] = deque(maxlen=config.warmup)

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"no instrument {self.config.instrument_id}")
            self.stop()
            return
        self.subscribe_bars(self.config.bar_type)

    def _state(self):
        bars = list(self.window)
        r = regime_mod.classify(bars)
        bias = _BIAS.get(r.direction, "flat")
        if r.stand_down or r.insufficient:
            bias = "flat"
        return SimpleNamespace(symbol=self.config.symbol, regime_label=r.label,
                               composite_bias=bias, stand_down=r.stand_down)

    def on_bar(self, bar: Bar) -> None:
        self.window.append({
            "bucket_ts": bar.ts_event // 1000,  # ns -> µs
            "open": bar.open.as_double(), "high": bar.high.as_double(),
            "low": bar.low.as_double(), "close": bar.close.as_double(),
            "volume": bar.volume.as_double(),
        })
        if len(self.window) < self.config.warmup:
            return

        pos_qty = float(self.portfolio.net_position(self.config.instrument_id))
        intent = gated_evaluate(self.strat, list(self.window), self._state(), pos_qty)
        if intent is None:
            return
        if intent.direction == "long" and self.portfolio.is_flat(self.config.instrument_id):
            self.submit_order(self.order_factory.market(
                instrument_id=self.config.instrument_id, order_side=OrderSide.BUY,
                quantity=self.instrument.make_qty(self.config.trade_size),
                time_in_force=TimeInForce.GTC))
        elif intent.direction == "flat" and self.portfolio.is_net_long(self.config.instrument_id):
            self.close_all_positions(self.config.instrument_id)

    def on_stop(self) -> None:
        self.close_all_positions(self.config.instrument_id)
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self.window.clear()


def backtest_strategy(strategy_id, symbol="BTCUSDT", *, root=None, start=None, end=None,
                      tf="1h", trade_size="0.02", warmup=300, equity0=100_000):
    """Run our strategy (via the adapter) in nautilus on our data; return a
    normalized result with nautilus stats + OUR PSR on the returns."""
    from nautilus_trader.adapters.binance import BINANCE_VENUE
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
    from nautilus_trader.model import TraderId
    from nautilus_trader.model.currencies import BTC, USDT
    from nautilus_trader.model.enums import AccountType, BookType, OmsType
    from nautilus_trader.model.objects import Money
    from nautilus_trader.persistence.wranglers import BarDataWrangler
    from nautilus_trader.test_kit.providers import TestInstrumentProvider

    from quant import market_store
    from quant.backtest import metrics as M
    from quant.nautilus_spike import load_bars_df

    root = root if root is not None else market_store.DEFAULT_ROOT
    df = load_bars_df(symbol, root, tf, start, end)
    if df is None or df.empty:
        return {"error": f"no {tf} data for {symbol}", "strategy": strategy_id}

    inst = TestInstrumentProvider.btcusdt_binance()
    bar_type = BarType.from_str(f"{inst.id}-1-HOUR-LAST-EXTERNAL")
    bars = BarDataWrangler(bar_type, inst).process(df)
    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("ADAPT-001"), logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(venue=BINANCE_VENUE, oms_type=OmsType.NETTING, book_type=BookType.L1_MBP,
                     account_type=AccountType.CASH, base_currency=None,
                     starting_balances=[Money(equity0, USDT), Money(0, BTC)], trade_execution=True)
    engine.add_instrument(inst)
    engine.add_data(bars)
    engine.add_strategy(NautilusStrategyAdapter(config=AdapterConfig(
        instrument_id=inst.id, bar_type=bar_type, strategy_id=strategy_id,
        symbol=symbol, trade_size=Decimal(trade_size), warmup=warmup)))
    engine.run()

    an = engine.portfolio.analyzer
    try:
        rets = [float(x) for x in an.returns().tolist()]
    except Exception:
        rets = []
    closed = engine.cache.positions_closed()
    out = {
        "strategy": strategy_id, "symbol": symbol, "tf": tf, "n_bars": len(bars),
        "orders": len(engine.cache.orders()), "positions": len(closed),
        "total_pnl_usdt": round(float(an.total_pnl(USDT)), 2) if an.total_pnl(USDT) is not None else None,
        "total_return_pct": round(float(an.total_pnl_percentage(USDT)), 3) if an.total_pnl_percentage(USDT) is not None else None,
        "psr": M.probabilistic_sharpe(rets) if len(rets) >= 3 else None,
        "n_returns": len(rets),
        "returns": rets,
    }
    engine.dispose()
    return out


def main(argv=None):
    import argparse
    import json
    import sys
    from pathlib import Path

    p = argparse.ArgumentParser(prog="quant.nautilus_run")
    p.add_argument("--strategy", default="trend_donchian_v1")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--root", default=None)
    p.add_argument("--from", dest="start", default=None)
    p.add_argument("--to", dest="end", default=None)
    p.add_argument("--tf", default="1h")
    a = p.parse_args(argv)
    out = backtest_strategy(a.strategy, a.symbol,
                            root=Path(a.root).expanduser() if a.root else None,
                            start=a.start, end=a.end, tf=a.tf)
    out.pop("returns", None)  # don't dump the full series to stdout
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
