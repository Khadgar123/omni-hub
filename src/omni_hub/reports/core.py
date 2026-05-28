"""Report rendering.  Stdlib only."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from ..harness import graphiti_bridge
from ..harness.preference import PreferenceStore
from ..proposals import ProposalStore


@dataclass(slots=True)
class ReportContext:
    period: str                     # "daily" | "weekly" | "monthly"
    anchor_date: date
    start: datetime
    end: datetime
    workspace: Path = field(default_factory=lambda: Path("."))
    db_path: Path = field(default_factory=lambda: Path(".omni/memory.sqlite3"))
    preference_root: Path = field(default_factory=lambda: Path(".omni/preference"))
    proposal_db_path: Path = field(default_factory=lambda: Path(".omni/proposals.sqlite3"))


def default_output_path(workspace: Path | str, ctx: ReportContext) -> Path:
    base = Path(workspace) / "vault" / "40_Reports" / ctx.period
    base.mkdir(parents=True, exist_ok=True)
    if ctx.period == "monthly":
        name = ctx.anchor_date.strftime("%Y-%m") + ".md"
    elif ctx.period == "weekly":
        iso = ctx.anchor_date.isocalendar()
        name = f"{iso.year}-W{iso.week:02d}.md"
    else:
        name = ctx.anchor_date.strftime("%Y-%m-%d") + ".md"
    return base / name


def _context_for(period: str, anchor: date | None) -> ReportContext:
    anchor = anchor or date.today()
    if period == "daily":
        start = datetime.combine(anchor, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=1)
    elif period == "weekly":
        weekday = anchor.weekday()        # Monday=0
        monday = anchor - timedelta(days=weekday)
        start = datetime.combine(monday, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=7)
    elif period == "monthly":
        first = anchor.replace(day=1)
        if first.month == 12:
            next_month = first.replace(year=first.year + 1, month=1)
        else:
            next_month = first.replace(month=first.month + 1)
        start = datetime.combine(first, datetime.min.time(), tzinfo=timezone.utc)
        end = datetime.combine(next_month, datetime.min.time(), tzinfo=timezone.utc)
    else:
        raise ValueError(f"unknown period: {period}")
    return ReportContext(period=period, anchor_date=anchor, start=start, end=end)


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------


def _filter_documents_in_range(
    records: Iterable[graphiti_bridge.KnowledgeRecord],
    start: datetime, end: datetime,
) -> list[graphiti_bridge.KnowledgeRecord]:
    out: list[graphiti_bridge.KnowledgeRecord] = []
    for r in records:
        if not r.updated_at:
            continue
        try:
            ts = datetime.fromisoformat(r.updated_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if start <= ts < end:
            out.append(r)
    return out


def _section_new_documents(ctx: ReportContext) -> str:
    backend = graphiti_bridge.get_backend(prefer="auto", db_path=ctx.db_path)
    try:
        all_docs = backend.list_documents(limit=2000)
    except Exception:
        all_docs = []
    in_range = _filter_documents_in_range(all_docs, ctx.start, ctx.end)
    if not in_range:
        return "_(no new documents captured in this window)_"
    lines = ["| updated_at | title | source |", "| --- | --- | --- |"]
    for r in in_range[:50]:
        title = (r.title or "(untitled)").replace("|", "/")
        lines.append(f"| {r.updated_at} | {title} | `{r.source_path}` |")
    if len(in_range) > 50:
        lines.append(f"| ... | _{len(in_range) - 50} more_ | |")
    return "\n".join(lines)


def _section_preference_activity(ctx: ReportContext) -> str:
    store = PreferenceStore(ctx.preference_root)
    domains = store.list_domains()
    if not domains:
        return "_(no preference activity yet — use `harness-preference-add`)_"
    rows = ["| domain | accepted | rejected | edited | total |", "| --- | ---: | ---: | ---: | ---: |"]
    grand = {"accepted": 0, "rejected": 0, "edited": 0, "total": 0}
    for d in domains:
        s = store.stats(d)
        rows.append(
            f"| {d} | {s['accepted']} | {s['rejected']} | {s['edited']} | {s['total']} |"
        )
        for k in grand:
            grand[k] += s[k]
    rows.append(
        f"| **all** | **{grand['accepted']}** | **{grand['rejected']}** | "
        f"**{grand['edited']}** | **{grand['total']}** |"
    )
    return "\n".join(rows)


def _section_wiki_health(ctx: ReportContext) -> str:
    """Compiled wiki + claims ledger snapshot."""

    try:
        from ..knowledge_plane import claims_stats, status as wiki_status
    except Exception:
        return "_(knowledge_plane unavailable)_"

    try:
        ws = wiki_status(ctx.workspace)
    except Exception:
        return "_(wiki not initialised yet — run `wiki-init`)_"

    try:
        cs = claims_stats(ctx.workspace)
    except Exception:
        cs = {"total": 0, "open": 0, "closed": 0, "by_state": {}, "by_domain": {}}

    wiki_section = ws.get("wiki", {})
    pages = wiki_section.get("page_count", 0)
    ready = wiki_section.get("ready", False)

    lines = [
        f"- wiki ready: `{ready}`  ·  pages: **{pages}**",
        f"- claims: total **{cs['total']}**  ·  open **{cs['open']}**  ·  closed **{cs['closed']}**",
    ]
    by_state = cs.get("by_state") or {}
    if by_state:
        bits = "  ·  ".join(f"`{k}`={v}" for k, v in sorted(by_state.items()))
        lines.append(f"- by state: {bits}")
    by_domain = cs.get("by_domain") or {}
    if by_domain:
        top = sorted(by_domain.items(), key=lambda kv: -kv[1])[:6]
        bits = "  ·  ".join(f"`{k}`={v}" for k, v in top)
        lines.append(f"- by domain (top 6): {bits}")
    return "\n".join(lines)


def _section_lint_pipeline(ctx: ReportContext) -> str:
    """Pending lint_finding proposals broken down by rule."""

    try:
        store = ProposalStore(
            workspace=ctx.workspace,
            db_path=str(ctx.proposal_db_path.relative_to(ctx.workspace))
            if ctx.proposal_db_path.is_absolute() and ctx.proposal_db_path.is_relative_to(ctx.workspace)
            else str(ctx.proposal_db_path),
            create=False,
        )
    except Exception:
        return "_(proposals store unavailable)_"

    try:
        findings = store.list(kind="lint_finding", state="pending", limit=500)
    except Exception:
        return "_(no lint pipeline data)_"

    if not findings:
        return "_(no pending lint_finding proposals — wiki-lint daily is healthy)_"

    by_rule: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for p in findings:
        rule = str(p.payload.get("rule", "?"))
        severity = str(p.payload.get("severity", "?"))
        by_rule[rule] = by_rule.get(rule, 0) + 1
        by_severity[severity] = by_severity.get(severity, 0) + 1

    rule_bits = "  ·  ".join(f"`{k}`={v}" for k, v in sorted(by_rule.items()))
    sev_bits = "  ·  ".join(f"`{k}`={v}" for k, v in sorted(by_severity.items()))
    return (
        f"- total pending: **{len(findings)}**\n"
        f"- by rule: {rule_bits}\n"
        f"- by severity: {sev_bits}"
    )


def _section_proposals(ctx: ReportContext) -> str:
    """Read pending redundancy proposals from the unified store.

    Reads ``state='pending'`` only — proposals the human has already
    approved or rejected shouldn't appear in the "things to triage"
    section.  The store also lives next to memory + queue, so the report
    naturally reflects the current state machine.
    """

    relevant_kinds = ("duplicate", "stale", "conflict", "low_signal")
    store = ProposalStore(
        workspace=ctx.workspace, db_path=str(ctx.proposal_db_path.name)
        if ctx.proposal_db_path.is_absolute() else str(ctx.proposal_db_path),
        create=False,
    ) if str(ctx.workspace) else ProposalStore(create=False)

    # Workspace-relative or absolute — pass through to ProposalStore.
    if ctx.proposal_db_path.is_absolute():
        store = ProposalStore(
            workspace=ctx.proposal_db_path.parent,
            db_path=ctx.proposal_db_path.name,
            create=False,
        )

    by_kind: dict[str, int] = {}
    total = 0
    for kind in relevant_kinds:
        proposals = store.list(kind=kind, state="pending", limit=10_000)
        if proposals:
            by_kind[kind] = len(proposals)
            total += len(proposals)
    if total == 0:
        return "_(no redundancy proposals pending — run `harness-redundancy-scan`)_"
    lines = [f"- **{k}**: {n}" for k, n in sorted(by_kind.items())]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _render(ctx: ReportContext) -> str:
    title_map = {"daily": "Daily Brief", "weekly": "Weekly Review", "monthly": "Monthly Roll-up"}
    title = title_map.get(ctx.period, ctx.period.capitalize())
    if ctx.period == "weekly":
        iso = ctx.anchor_date.isocalendar()
        period_label = f"{iso.year}-W{iso.week:02d}"
    elif ctx.period == "monthly":
        period_label = ctx.anchor_date.strftime("%Y-%m")
    else:
        period_label = ctx.anchor_date.strftime("%Y-%m-%d")
    header = (
        f"# {title} — {period_label}\n\n"
        f"window: `{ctx.start.isoformat()}` → `{ctx.end.isoformat()}`\n"
    )
    body = (
        f"\n## New captures\n\n{_section_new_documents(ctx)}\n"
        f"\n## Wiki health\n\n{_section_wiki_health(ctx)}\n"
        f"\n## Lint pipeline\n\n{_section_lint_pipeline(ctx)}\n"
        f"\n## Preference flywheel\n\n{_section_preference_activity(ctx)}\n"
        f"\n## Pending redundancy proposals\n\n{_section_proposals(ctx)}\n"
    )
    footer = (
        "\n---\n"
        "Generated by `omni-hub harness-report-*`.  "
        "Run `harness-compile --domain <d>` to ingest the accepted/rejected counts above "
        "into the next prompt version.\n"
    )
    return header + body + footer


def build_daily(anchor: date | None = None, workspace: Path | str = ".") -> tuple[str, ReportContext]:
    ctx = _context_for("daily", anchor)
    ctx.workspace = Path(workspace)
    ctx.db_path = ctx.workspace / ".omni" / "memory.sqlite3"
    ctx.preference_root = ctx.workspace / ".omni" / "preference"
    ctx.proposal_db_path = ctx.workspace / ".omni" / "proposals.sqlite3"
    return _render(ctx), ctx


def build_weekly(anchor: date | None = None, workspace: Path | str = ".") -> tuple[str, ReportContext]:
    ctx = _context_for("weekly", anchor)
    ctx.workspace = Path(workspace)
    ctx.db_path = ctx.workspace / ".omni" / "memory.sqlite3"
    ctx.preference_root = ctx.workspace / ".omni" / "preference"
    ctx.proposal_db_path = ctx.workspace / ".omni" / "proposals.sqlite3"
    return _render(ctx), ctx


def build_monthly(anchor: date | None = None, workspace: Path | str = ".") -> tuple[str, ReportContext]:
    ctx = _context_for("monthly", anchor)
    ctx.workspace = Path(workspace)
    ctx.db_path = ctx.workspace / ".omni" / "memory.sqlite3"
    ctx.preference_root = ctx.workspace / ".omni" / "preference"
    ctx.proposal_db_path = ctx.workspace / ".omni" / "proposals.sqlite3"
    return _render(ctx), ctx
