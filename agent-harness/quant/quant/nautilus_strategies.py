"""Our strategies ported to NautilusTrader Strategy subclasses (long-only spot).

Migration step 1: trend_donchian as a native nautilus ``Strategy`` — long-only
(buy on a Donchian upper breakout, close on a lower-channel break; never shorts,
so a spot CASH account never goes negative). The regime gate becomes a separate
Actor (next step); here the pure-TA entry/exit is reproduced against nautilus's
``on_bar`` API so it runs in the real engine + sandbox/live with no code changes.
"""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from nautilus_trader.config import PositiveInt, StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class DonchianTrendConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    entry_lookback: PositiveInt = 20
    exit_lookback: PositiveInt = 10


class DonchianTrend(Strategy):
    """Long-only Donchian breakout: buy when close > prior N-high; close when
    close < prior M-low. Spot-safe (no shorting)."""

    def __init__(self, config: DonchianTrendConfig) -> None:
        super().__init__(config)
        self.instrument = None
        self._highs: deque[float] = deque(maxlen=config.entry_lookback)
        self._lows: deque[float] = deque(maxlen=config.exit_lookback)

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"no instrument {self.config.instrument_id}")
            self.stop()
            return
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        h, low, c = bar.high.as_double(), bar.low.as_double(), bar.close.as_double()
        entry_ready = len(self._highs) == self.config.entry_lookback
        exit_ready = len(self._lows) == self.config.exit_lookback
        upper = max(self._highs) if entry_ready else None
        lower = min(self._lows) if exit_ready else None

        if self.portfolio.is_flat(self.config.instrument_id):
            if entry_ready and c > upper:
                self.submit_order(self.order_factory.market(
                    instrument_id=self.config.instrument_id, order_side=OrderSide.BUY,
                    quantity=self.instrument.make_qty(self.config.trade_size),
                    time_in_force=TimeInForce.GTC,
                ))
        elif self.portfolio.is_net_long(self.config.instrument_id):
            if exit_ready and c < lower:
                self.close_all_positions(self.config.instrument_id)

        # update windows AFTER the signal so the breakout compares vs PRIOR bars
        self._highs.append(h)
        self._lows.append(low)

    def on_stop(self) -> None:
        self.close_all_positions(self.config.instrument_id)
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self._highs.clear()
        self._lows.clear()


def run(symbol="BTCUSDT", root=None, start=None, end=None, tf="1h",
        entry_lookback=20, exit_lookback=10, trade_size="0.02"):
    """Backtest the ported DonchianTrend on our DuckDB data via nautilus."""
    from nautilus_trader.adapters.binance import BINANCE_VENUE
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
    from nautilus_trader.model import TraderId
    from nautilus_trader.model.currencies import BTC, USDT
    from nautilus_trader.model.data import BarType
    from nautilus_trader.model.enums import AccountType, BookType, OmsType
    from nautilus_trader.model.objects import Money
    from nautilus_trader.persistence.wranglers import BarDataWrangler
    from nautilus_trader.test_kit.providers import TestInstrumentProvider

    from quant import market_store
    from quant.nautilus_spike import load_bars_df

    root = root if root is not None else market_store.DEFAULT_ROOT
    df = load_bars_df(symbol, root, tf, start, end)
    if df is None or df.empty:
        return {"error": f"no {tf} data for {symbol}"}

    inst = TestInstrumentProvider.btcusdt_binance()
    bar_type = BarType.from_str(f"{inst.id}-1-HOUR-LAST-EXTERNAL")
    bars = BarDataWrangler(bar_type, inst).process(df)

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("DONCHIAN-001"), logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(
        venue=BINANCE_VENUE, oms_type=OmsType.NETTING, book_type=BookType.L1_MBP,
        account_type=AccountType.CASH, base_currency=None,
        starting_balances=[Money(100_000, USDT), Money(0, BTC)],  # long-only: no BTC seed needed
        trade_execution=True,
    )
    engine.add_instrument(inst)
    engine.add_data(bars)
    engine.add_strategy(DonchianTrend(config=DonchianTrendConfig(
        instrument_id=inst.id, bar_type=bar_type, trade_size=Decimal(trade_size),
        entry_lookback=entry_lookback, exit_lookback=exit_lookback)))
    engine.run()

    closed = engine.cache.positions_closed()
    realized = sum(p.realized_pnl.as_double() for p in closed if p.realized_pnl is not None)
    acct = engine.portfolio.account(BINANCE_VENUE)
    out = {
        "strategy": "donchian_trend (nautilus)", "symbol": symbol, "tf": tf, "n_bars": len(bars),
        "orders": len(engine.cache.orders()), "positions_closed": len(closed),
        "realized_pnl_usdt": round(realized, 2),
        "final_usdt_balance": round(float(acct.balance_total(USDT).as_double()), 2) if acct else None,
    }
    engine.dispose()
    return out


def main(argv=None):
    import argparse
    import json
    import sys
    from pathlib import Path

    p = argparse.ArgumentParser(prog="quant.nautilus_strategies")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--root", default=None)
    p.add_argument("--from", dest="start", default=None)
    p.add_argument("--to", dest="end", default=None)
    p.add_argument("--tf", default="1h")
    a = p.parse_args(argv)
    out = run(a.symbol, root=Path(a.root).expanduser() if a.root else None,
              start=a.start, end=a.end, tf=a.tf)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
