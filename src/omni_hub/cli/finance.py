"""Finance CLI (v0.39) — read-only analysis + Proposal-gated order intents.

Hard rule: this CLI **never** places orders.  Every order goes through
``order-propose`` which lands ``Proposal(kind=order_intent)`` for human
review; the broker CLI in ``agent-harness/integrations/finance/`` executes
post-approval.
"""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    screen = subparsers.add_parser(
        "finance-screen",
        help="Read-only stock screen (no broker calls).",
    )
    screen.add_argument("--domain", default="finance")
    screen.add_argument("--ticker", action="append", default=None)
    screen.add_argument("--sector", default="")
    screen.add_argument("--market", default="", choices=["", "US", "A", "HK"])

    watch_create = subparsers.add_parser(
        "finance-watch-create",
        help="Create an alert rule (evaluated by external broker CLI).",
    )
    watch_create.add_argument("--user-id", required=True)
    watch_create.add_argument("--instrument", required=True)
    watch_create.add_argument("--rule", required=True,
                               dest="expression",
                               help='e.g. "price > 200" or "rsi(14) < 30"')
    watch_create.add_argument("--channel", default="email",
                               choices=["email", "feishu", "discord", "cli"])

    watch_list = subparsers.add_parser(
        "finance-watch-list", help="List active alert rules.",
    )
    watch_list.add_argument("--user-id", default="")

    portfolio = subparsers.add_parser(
        "finance-portfolio-stats",
        help="Read most-recent broker-pushed portfolio snapshot.",
    )
    portfolio.add_argument("--user-id", required=True)

    order = subparsers.add_parser(
        "order-propose",
        help="Emit an OrderIntent + RiskCheckResult as Proposal[T].  "
             "NEVER places the order; human approves; broker CLI executes.",
    )
    order.add_argument("--user-id", required=True)
    order.add_argument("--instrument", required=True)
    order.add_argument("--side", required=True, choices=["buy", "sell"])
    order.add_argument("--qty", type=float, required=True)
    order.add_argument("--order-type", default="market",
                        choices=["market", "limit", "stop", "stop_limit"])
    order.add_argument("--limit-price", type=float, default=None)
    order.add_argument("--stop-price", type=float, default=None)
    order.add_argument("--portfolio-value-usd", type=float, default=0.0,
                        help="For risk-sizing; auto-blocks positions over 25 pct.")
    order.add_argument("--estimated-price", type=float, default=None)
    order.add_argument("--rationale", default="")

    qfind = subparsers.add_parser(
        "quant-finding-propose",
        help="Quant finding JSON (strategy/hypothesis/backtest/risk) -> "
             "candidate claims -> Proposal[T] into the finance wiki. "
             "Never ingests raw OHLCV.",
    )
    qfind.add_argument("--finding-json", required=True,
                       help="path to a quant finding JSON file")
    qfind.add_argument("--domain", default="finance")
    qfind.add_argument("--title", default="")

    crypto = subparsers.add_parser(
        "crypto-read",
        help="Crypto edge-audit read (BTC/ETH/...): live regime+carry+order-flow+macro -> "
             "counterparty/fragility/triggers. Read-only; no orders; no prediction. "
             "See agent-harness/quant/FRAMEWORK.md.",
    )
    crypto.add_argument("--symbol", default="BTCUSDT")
    crypto.add_argument("--venue", default="binance", choices=["binance", "coinbase", "kraken"])
    crypto.add_argument("--no-macro", action="store_true")

    macro = subparsers.add_parser(
        "macro-read",
        help="Global macro daily dashboard: regime+structure across world assets (US/CN/JP/KR stocks, "
             "rates, FX, gold, oil, copper, BTC) + curve/credit/vol panel + cross-asset matrix. "
             "Read-only; no orders; no prediction; daily granularity.",
    )
    macro.add_argument("--period", default="2y")


def _finance_screen(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="finance_screen", action="screen",
            payload={
                "domain": args.domain,
                "tickers": list(args.ticker) if args.ticker else [],
                "sector": args.sector,
                "market": args.market,
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _finance_watch_create(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="finance_watch_create", action="create",
            payload={
                "user_id": args.user_id,
                "instrument": args.instrument,
                "expression": args.expression,
                "channel": args.channel,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _finance_watch_list(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="finance_watch_list", action="list",
            payload={"user_id": args.user_id or None},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _finance_portfolio(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="finance_portfolio_stats", action="stats",
            payload={"user_id": args.user_id},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _order_propose(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="order_propose", action="propose",
            payload={
                "user_id": args.user_id, "instrument": args.instrument,
                "side": args.side, "qty": args.qty,
                "order_type": args.order_type,
                "limit_price": args.limit_price,
                "stop_price": args.stop_price,
                "portfolio_value_usd": args.portfolio_value_usd,
                "estimated_price": args.estimated_price,
                "rationale": args.rationale,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _quant_finding_propose(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="quant_finding_propose", action="propose",
            payload={
                "finding_json": args.finding_json,
                "domain": args.domain,
                "title": args.title,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _crypto_read(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="crypto_read", action="read",
            payload={"symbol": args.symbol, "venue": args.venue, "no_macro": args.no_macro},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


def _macro_read(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="macro_read", action="read",
            payload={"period": args.period},
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "finance-screen": _finance_screen,
    "finance-watch-create": _finance_watch_create,
    "finance-watch-list": _finance_watch_list,
    "finance-portfolio-stats": _finance_portfolio,
    "order-propose": _order_propose,
    "quant-finding-propose": _quant_finding_propose,
    "crypto-read": _crypto_read,
    "macro-read": _macro_read,
}
