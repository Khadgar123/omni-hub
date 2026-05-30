"""Quant findings → ClaimLedger bridge (the quant→knowledge seam).

The quant module (``agent-harness/quant``) is a separate plane: raw tick/OHLCV
lives in Parquet, NOT in wiki/claims (per its README — "wiki only stores
hypotheses / conclusions / risk").  This module is the sanctioned path that
folds a quant FINDING — a strategy hypothesis, a backtest conclusion, a risk
disclosure — into the parent ClaimLedger via ``Proposal[T]``, exactly like
``research_assets.py`` does for ResearchFlow.

omni-hub stays stdlib-only: it never imports ``quant``.  A finding is a plain
dict (produced by the quant CLI / strategy run, handed over the process seam).
Raw OHLCV/tick numerics are deliberately NOT ingested — only the human-
reviewable conclusions, so the finance-domain wiki stays a curated knowledge
layer, not a data dump.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime


def _q_utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _q_claim_id(*parts: str) -> str:
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"claim_{h[:16]}"


def _fmt_backtest(bt: object) -> str:
    if not isinstance(bt, dict):
        return ""
    bits: list[str] = []
    for k, label in (
        ("sharpe", "Sharpe"), ("max_drawdown", "max DD"), ("win_rate", "win-rate"),
        ("cagr", "CAGR"), ("trades", "trades"),
    ):
        v = bt.get(k)
        if v not in (None, ""):
            bits.append(f"{label} {v}")
    period = str(bt.get("period", "")).strip()
    tail = f" over {period}" if period else ""
    return ("; ".join(bits) + tail).strip()


def quant_finding_to_claims(
    finding: object,
    *,
    source_id: str = "quant",
    domain: str = "finance",
) -> list[dict[str, object]]:
    """Decompose a quant finding dict into candidate claims.

    Three families: ``strategy_conclusion`` (hypothesis/conclusion),
    ``backtest_result`` (the metrics line), ``risk_disclosure`` (the risk
    note).  Conservative + lossless-on-skip; dedups by statement; deterministic
    ids (idempotent re-ingest).  NEVER emits raw OHLCV/tick data.
    """
    if not isinstance(finding, dict):
        return []
    symbol = str(finding.get("symbol", "")).strip()
    timeframe = str(finding.get("timeframe", "") or finding.get("tf", "")).strip()
    strategy = str(finding.get("strategy", "")).strip()
    venue = str(finding.get("venue", "")).strip()
    regime = str(finding.get("regime", "")).strip()
    bt = finding.get("backtest") if isinstance(finding.get("backtest"), dict) else {}
    pfx = " ".join(p for p in (symbol, timeframe) if p)
    pfx = f"[{pfx}] " if pfx else ""

    claims: list[dict[str, object]] = []
    seen: set[str] = set()

    def _add(content: str, kind: str, confidence: object = 0.5) -> None:
        c = (content or "").strip()
        if not c:                       # no real content -> no claim (skip prefix-only)
            return
        s = (pfx + c).strip()
        if s.lower() in seen:
            return
        seen.add(s.lower())
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            conf = 0.5
        claims.append({
            "claim_id": _q_claim_id("quant", domain, kind, source_id, s),
            "domain": domain,
            "statement": s[:280],
            "support": [{
                "source_id": source_id,
                "source": "quant",
                "served_via": "quant_backtest",
                "claim_kind": kind,
                "symbol": symbol,
                "timeframe": timeframe,
                "venue": venue,
                "regime": regime,
                "backtest": bt,
            }],
            "against": [],
            "confidence": conf,
            "uncertainty": (
                "quant backtest finding; in-sample edge — awaits out-of-sample "
                "/ walk-forward confirmation + human review"
            ),
            "review_state": "proposed",
            "t_valid_from": _q_utcnow(),
            "t_valid_to": None,
            "supersedes": [],
            "superseded_by": None,
        })

    _add(
        str(finding.get("conclusion", "")).strip()
        or str(finding.get("hypothesis", "")).strip(),
        "strategy_conclusion", finding.get("confidence", 0.5),
    )
    bt_line = _fmt_backtest(bt)
    if bt_line:
        _add(f"{strategy or 'strategy'} backtest: {bt_line}", "backtest_result", 0.55)
    _add(str(finding.get("risk", "")).strip(), "risk_disclosure", 0.6)
    return claims


def propose_quant_finding(
    workspace: str = ".",
    *,
    finding: dict | None = None,
    finding_json: str = "",
    domain: str = "finance",
    title: str = "",
    trace_id: str = "",
) -> dict[str, object]:
    """A quant finding (dict or JSON file) -> candidate claims -> ``Proposal[T]``.

    The sanctioned quant->knowledge path: emits ``Proposal(kind="wiki_update")``
    for human review; on approve the finance-domain synthesis page projects
    from the claims.  Never writes wiki directly; never ingests raw OHLCV.
    """
    import json as _json
    from pathlib import Path as _Path

    from .knowledge_plane import (
        _slugify,
        _synthesis_target_path,
        append_log,
        init_layout,
        safe_workspace_path,
    )
    from .proposals import PENDING, Proposal, ProposalStore

    workspace_root = _Path(workspace).resolve()
    init_layout(workspace_root)

    rel_src = ""
    if finding is None:
        if not finding_json:
            raise ValueError("provide finding (dict) or finding_json (path)")
        f = safe_workspace_path(workspace_root, finding_json)
        if not f.exists():
            raise FileNotFoundError(f"quant finding not found: {finding_json}")
        try:
            finding = _json.loads(f.read_text(encoding="utf-8"))
        except _json.JSONDecodeError as exc:
            raise ValueError(f"invalid finding json {finding_json}: {exc}") from exc
        rel_src = str(f.relative_to(workspace_root))
    if not isinstance(finding, dict):
        raise ValueError("finding must be a JSON object")

    symbol = str(finding.get("symbol", "")).strip()
    strategy = str(finding.get("strategy", "")).strip()
    resolved_title = (
        title.strip()
        or " ".join(p for p in (strategy, symbol) if p).strip()
        or "Quant finding"
    )
    source_id = f"quant:{_slugify(resolved_title)}"
    claims = quant_finding_to_claims(finding, source_id=source_id, domain=domain)
    if not claims:
        raise ValueError(
            "no claims extracted (need conclusion / hypothesis / backtest / risk)"
        )

    target_path = _synthesis_target_path(resolved_title, source_id)
    body = (
        f"---\npage_type: synthesis\ndomain: {domain}\n"
        f"review_state: proposed\n---\n\n# {resolved_title}\n\n"
        f"_Pending projection from {len(claims)} quant claim(s)._\n"
    )
    proposal = Proposal(
        kind="wiki_update",
        state=PENDING,
        title=f"[quant] {resolved_title}",
        summary=f"{len(claims)} candidate claim(s) from a quant backtest finding.",
        source_path=rel_src,
        payload={
            "target_path": target_path,
            "domain": domain,
            "page_type": "synthesis",
            "title": resolved_title,
            "query": resolved_title,
            "body": body,
            "claims": claims,
            "quant": {
                "symbol": symbol,
                "timeframe": str(finding.get("timeframe", "")),
                "venue": str(finding.get("venue", "")),
                "regime": str(finding.get("regime", "")),
            },
        },
    )
    stored = ProposalStore(workspace_root).store(proposal)
    proposal_id = stored.get("proposal_id", proposal.proposal_id)
    append_log(
        workspace_root, op="ingest",
        summary=f"quant {resolved_title} ({len(claims)} claims)",
        source=rel_src or "inline",
    )
    return {
        "proposal_id": proposal_id,
        "target_path": target_path,
        "claim_count": len(claims),
        "domain": domain,
    }
