"""FinanceAnalyst — read-only screens, watches, portfolio stats (v0.36).

Builds on already-registered retrieval sources (``edgar``, ``fred``,
``tushare``, plus the cn_finance / business_intel cousins) so analysis
inherits cascade fail-soft + caching.

No prices / fills land here — anything that requires live market data
or order routing belongs in
``agent-harness/integrations/finance/`` (ccxt / alpaca-py) and reaches
us through ``Proposal(kind=order_intent)``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class ScreenCriteria:
    """Lightweight screening criteria — extend as connectors grow."""

    domain: str = "finance"
    tickers: list[str] = field(default_factory=list)
    sector: str = ""
    market: str = ""                            # "US" | "A" | "HK" | ...
    min_market_cap_usd: float | None = None
    max_pe_ratio: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StockSignal:
    """One screening result — a candidate stock + reason."""

    ticker: str
    name: str = ""
    market: str = ""
    sector: str = ""
    summary: str = ""
    sources: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    surfaced_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AlertRule:
    """Watchlist alert — evaluated by external broker CLI / cron."""

    rule_id: str
    user_id: str
    instrument: str                         # "NVDA" | "BTC-USD"
    expression: str                         # "price > 200" | "rsi(14) < 30" | etc.
    channel: str = "email"
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PortfolioSnapshot:
    """Read-only snapshot of holdings.

    Live values come from broker CLI; this dataclass is the
    omni-hub-side typed projection.
    """

    user_id: str
    snapshot_id: str
    holdings: list[dict[str, Any]] = field(default_factory=list)
    cash_usd: float = 0.0
    total_value_usd: float = 0.0
    captured_at: str = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FinanceAnalyst:
    """Read-only analysis surface.

    For v0.36 every method is a thin wrapper that:
      * uses existing retrieval connectors (cascade) for the read,
      * returns typed dataclasses,
      * never touches money.
    """

    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()

    def screen(self, criteria: ScreenCriteria) -> list[StockSignal]:
        """Return candidate stocks matching ``criteria``.

        v0.43.4 — connects EDGAR (filings) + FRED (macro) cascade so
        screening returns real candidates instead of an empty list.

        Behaviour:
        * If ``tickers`` is set → EDGAR full-text for each (latest 10-K /
          10-Q / 8-K context) → emit one StockSignal per ticker.
        * Else if ``sector`` is set → EDGAR full-text by sector → top N
          tickers extracted.
        * Else → no filters → return empty.

        Read-only.  No prices, no orders.  Pipes into ``order-propose``
        skill if user wants to act on a signal.
        """

        signals: list[StockSignal] = []
        try:
            from ..retrieval.finance import EdgarSource
        except ImportError:                                       # pragma: no cover
            return signals
        edgar = EdgarSource()

        # Path 1: explicit ticker list — one EDGAR look-up each.
        if criteria.tickers:
            for ticker in criteria.tickers[:10]:
                try:
                    records = edgar.retrieve(ticker, limit=3, domain="finance")
                except Exception:                                 # noqa: BLE001
                    continue
                if not records:
                    continue
                head = records[0]
                signals.append(StockSignal(
                    ticker=ticker.upper(),
                    name=head.title[:120],
                    market=criteria.market or "US",
                    sector=criteria.sector,
                    summary=head.snippet[:400],
                    sources=["edgar"],
                    metadata={
                        "edgar_url": head.url,
                        "edgar_filings_seen": len(records),
                        "criteria": criteria.to_dict(),
                    },
                ))
            return signals

        # Path 2: sector keyword — broad EDGAR search.
        if criteria.sector:
            try:
                records = edgar.retrieve(criteria.sector, limit=10, domain="finance")
            except Exception:                                     # noqa: BLE001
                return signals
            for rec in records[:10]:
                # Try to extract ticker from title / URL (heuristic: first
                # capitalised 2-5 letter token).
                import re
                m = re.search(r"\b([A-Z]{2,5})\b", rec.title)
                ticker = m.group(1) if m else ""
                signals.append(StockSignal(
                    ticker=ticker,
                    name=rec.title[:120],
                    market=criteria.market or "US",
                    sector=criteria.sector,
                    summary=rec.snippet[:400],
                    sources=["edgar"],
                    metadata={"edgar_url": rec.url},
                ))
            return signals

        # Path 3: no filters → empty.
        return signals

    def watch_create(self, rule: AlertRule) -> AlertRule:
        """Persist a watchlist rule.  Evaluation happens externally
        (cron + broker CLI) and emits Channel inbound messages."""

        target = self.workspace / ".omni" / "alerts.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        import json
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rule.to_dict(), ensure_ascii=False) + "\n")
        return rule

    def list_alerts(
        self, *, user_id: str | None = None,
    ) -> list[AlertRule]:
        target = self.workspace / ".omni" / "alerts.jsonl"
        if not target.exists():
            return []
        import json
        out: list[AlertRule] = []
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if user_id and data.get("user_id") != user_id:
                continue
            out.append(AlertRule(**data))
        return out

    def portfolio_stats(self, user_id: str) -> PortfolioSnapshot:
        """Return the most-recent persisted snapshot, or an empty one.

        v0.36 stores broker-pushed snapshots under
        ``.omni/portfolios/<user_id>.jsonl``.  No live fetch.
        """

        path = self.workspace / ".omni" / "portfolios" / f"{user_id}.jsonl"
        if not path.exists():
            return PortfolioSnapshot(user_id=user_id, snapshot_id="empty-0")
        import json
        last_line = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                last_line = line
        if not last_line:
            return PortfolioSnapshot(user_id=user_id, snapshot_id="empty-0")
        data = json.loads(last_line)
        return PortfolioSnapshot(**data)


__all__ = [
    "AlertRule",
    "FinanceAnalyst",
    "PortfolioSnapshot",
    "ScreenCriteria",
    "StockSignal",
]
