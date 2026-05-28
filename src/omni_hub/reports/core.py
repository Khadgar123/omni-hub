"""Report rendering.  Stdlib only."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from ..harness import graphiti_bridge
from ..harness.preference import PreferenceStore
from ..harness.redundancy import load_proposals


@dataclass(slots=True)
class ReportContext:
    period: str                     # "daily" | "weekly" | "monthly"
    anchor_date: date
    start: datetime
    end: datetime
    workspace: Path = field(default_factory=lambda: Path("."))
    db_path: Path = field(default_factory=lambda: Path(".omni/memory.sqlite3"))
    preference_root: Path = field(default_factory=lambda: Path(".omni/preference"))
    proposals_path: Path = field(default_factory=lambda: Path(".omni/proposals/redundancy.jsonl"))


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


def _section_proposals(ctx: ReportContext) -> str:
    proposals = list(load_proposals(path=ctx.proposals_path))
    if not proposals:
        return "_(no redundancy proposals pending — run `harness-redundancy-scan`)_"
    by_kind: dict[str, int] = {}
    for p in proposals:
        by_kind[p.kind] = by_kind.get(p.kind, 0) + 1
    lines = [f"- **{k}**: {n}" for k, n in sorted(by_kind.items(), key=lambda x: x[0])]
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
    ctx.proposals_path = ctx.workspace / ".omni" / "proposals" / "redundancy.jsonl"
    return _render(ctx), ctx


def build_weekly(anchor: date | None = None, workspace: Path | str = ".") -> tuple[str, ReportContext]:
    ctx = _context_for("weekly", anchor)
    ctx.workspace = Path(workspace)
    ctx.db_path = ctx.workspace / ".omni" / "memory.sqlite3"
    ctx.preference_root = ctx.workspace / ".omni" / "preference"
    ctx.proposals_path = ctx.workspace / ".omni" / "proposals" / "redundancy.jsonl"
    return _render(ctx), ctx


def build_monthly(anchor: date | None = None, workspace: Path | str = ".") -> tuple[str, ReportContext]:
    ctx = _context_for("monthly", anchor)
    ctx.workspace = Path(workspace)
    ctx.db_path = ctx.workspace / ".omni" / "memory.sqlite3"
    ctx.preference_root = ctx.workspace / ".omni" / "preference"
    ctx.proposals_path = ctx.workspace / ".omni" / "proposals" / "redundancy.jsonl"
    return _render(ctx), ctx
