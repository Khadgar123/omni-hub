"""NautilusTrader integration SPIKE — our DuckDB 1s -> nautilus Bars -> backtest.

Proves the pivot: our market store feeds a real nautilus BacktestEngine on a spot
CASH account, running a strategy end-to-end. If this runs + produces fills/PnL on
real BTC, we adopt nautilus as the engine (and port our regime/validation on top).
"""

from __future__ import annotations

from decimal import Decimal

from quant import market_store
from quant import resample as rs


def load_bars_df(symbol, root, tf, start, end):
    import pandas as pd
    rows = rs.resample(symbol, tf, root=root, source_interval="1s", start=start, end=end)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["bucket_ts"], unit="us", utc=True)
    return df.set_index("timestamp")[["open", "high", "low", "close", "volume"]].astype(float)


def run(symbol="BTCUSDT", root=None, start=None, end=None, tf="1h",
        fast=10, slow=20, trade_size="0.01"):
    from nautilus_trader.adapters.binance import BINANCE_VENUE
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
    from nautilus_trader.examples.strategies.ema_cross import EMACross, EMACrossConfig
    from nautilus_trader.model import TraderId
    from nautilus_trader.model.currencies import BTC, USDT
    from nautilus_trader.model.data import BarType
    from nautilus_trader.model.enums import AccountType, BookType, OmsType
    from nautilus_trader.model.objects import Money
    from nautilus_trader.persistence.wranglers import BarDataWrangler
    from nautilus_trader.test_kit.providers import TestInstrumentProvider

    root = root if root is not None else market_store.DEFAULT_ROOT
    df = load_bars_df(symbol, root, tf, start, end)
    if df is None or df.empty:
        return {"error": f"no {tf} data for {symbol}"}

    inst = TestInstrumentProvider.btcusdt_binance()
    bar_type = BarType.from_str(f"{inst.id}-1-HOUR-LAST-EXTERNAL")
    bars = BarDataWrangler(bar_type, inst).process(df)

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("SPIKE-001"),
        logging=LoggingConfig(log_level="ERROR"),
    ))
    engine.add_venue(
        venue=BINANCE_VENUE, oms_type=OmsType.NETTING, book_type=BookType.L1_MBP,
        account_type=AccountType.CASH, base_currency=None,
        # seed a little BTC so the demo EMACross (which shorts) doesn't halt on a
        # spot CASH account; our real strategies are long-only and won't need this
        starting_balances=[Money(100_000, USDT), Money(2, BTC)],
        trade_execution=True,
    )
    engine.add_instrument(inst)
    engine.add_data(bars)
    engine.add_strategy(EMACross(config=EMACrossConfig(
        instrument_id=inst.id, bar_type=bar_type,
        fast_ema_period=fast, slow_ema_period=slow, trade_size=Decimal(trade_size),
    )))
    engine.run()

    closed = engine.cache.positions_closed()
    realized = sum(p.realized_pnl.as_double() for p in closed if p.realized_pnl is not None)
    acct = engine.portfolio.account(BINANCE_VENUE)
    usdt_bal = float(acct.balance_total(USDT).as_double()) if acct else None
    out = {
        "symbol": symbol, "tf": tf, "n_bars": len(bars),
        "orders": len(engine.cache.orders()),
        "positions_closed": len(closed),
        "realized_pnl_usdt": round(realized, 2),
        "final_usdt_balance": round(usdt_bal, 2) if usdt_bal is not None else None,
        "nautilus": True,
    }
    engine.dispose()
    return out


def main(argv=None):
    import argparse
    import json
    import sys
    from pathlib import Path

    p = argparse.ArgumentParser(prog="quant.nautilus_spike", description=__doc__)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--root", default=None)
    p.add_argument("--from", dest="start", default=None)
    p.add_argument("--to", dest="end", default=None)
    p.add_argument("--tf", default="1h")
    args = p.parse_args(argv)
    root = Path(args.root).expanduser() if args.root else None
    out = run(args.symbol, root=root, start=args.start, end=args.end, tf=args.tf)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
