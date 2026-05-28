"""Cross-skill report orchestrator (v0.19 + v0.26 narrative).

Aggregates the four primary signal sources omni-hub already maintains and
renders a Markdown summary suitable for daily / weekly / monthly digest:

* ClaimLedger stats (added / superseded / by-domain)
* lint findings since the last report
* PreferenceStore deltas (accepted / rejected)
* WorkflowKernel runs (completed / failed / suspended)

No LLM call in the **aggregation** path — pure Markdown rollup.  v0.26
adds an opt-in **narrative** mode that enqueues a ``report_narrate``
TaskPacket on the claude lane; the agent reads the markdown summary +
context and writes a ``Proposal(kind=generation)`` carrying the trend
analysis.  The narrative step never bypasses Proposal[T] — humans approve
before the narrative reaches ``vault/40_Reports/``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any


class ReportPeriod(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"

    @property
    def days(self) -> int:
        return {"daily": 1, "weekly": 7, "monthly": 30}[self.value]


@dataclass(slots=True)
class ReportSection:
    """One section in a report — title + Markdown body + raw numbers."""

    title: str
    body_md: str
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReportSummary:
    period: str
    window_start: str
    window_end: str
    sections: list[ReportSection] = field(default_factory=list)
    markdown: str = ""
    narrative_task_id: str = ""           # v0.26: set when --narrate enqueues a task

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "sections": [s.to_dict() for s in self.sections],
            "markdown": self.markdown,
            "narrative_task_id": self.narrative_task_id,
        }


@dataclass(slots=True)
class NarrativeRequest:
    """v0.26 — packet for the claude-lane narrative task."""

    period: str
    markdown_summary: str
    target_audience: str = "self"
    additional_notes: str = ""
    trace_id: str = ""

    def to_packet(self) -> dict[str, Any]:
        """Map to a TaskPacket-compatible dict for task_enqueue."""

        return {
            "task_type": "report_narrate",
            "domain_profile": "meta",
            "goal": (
                f"Write a {self.period} narrative summary for the user. "
                "Highlight 3 trends, 2 decisions to make, and 1 follow-up "
                "for next period.  Cite specific stats from the markdown "
                "below.  Output a markdown body (no frontmatter)."
            ),
            "audience": self.target_audience,
            "notes": self.additional_notes,
            "context": {
                "report_markdown": self.markdown_summary,
                "period": self.period,
            },
        }


class ReportOrchestrator:
    """Cross-skill report builder."""

    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()

    def build(self, period: ReportPeriod) -> ReportSummary:
        now = datetime.now(UTC)
        window_start = now - timedelta(days=period.days)

        sections: list[ReportSection] = [
            self._claims_section(window_start, now),
            self._lint_section(window_start, now),
            self._preference_section(window_start, now),
            self._workflow_section(window_start, now),
        ]

        markdown = self._render_markdown(period, window_start, now, sections)
        return ReportSummary(
            period=period.value,
            window_start=window_start.isoformat(),
            window_end=now.isoformat(),
            sections=sections,
            markdown=markdown,
        )

    def build_with_narrative(
        self,
        period: ReportPeriod,
        *,
        target_audience: str = "self",
        additional_notes: str = "",
        trace_id: str = "",
    ) -> tuple[ReportSummary, NarrativeRequest]:
        """v0.26 — build the data summary AND a NarrativeRequest ready to
        enqueue on the claude lane.  The caller decides whether to
        actually enqueue (e.g. via TaskQueue) — this keeps the
        orchestrator stdlib-only.
        """

        summary = self.build(period)
        narrative = NarrativeRequest(
            period=period.value,
            markdown_summary=summary.markdown,
            target_audience=target_audience,
            additional_notes=additional_notes,
            trace_id=trace_id,
        )
        return summary, narrative

    # ---- sections ------------------------------------------------

    def _claims_section(self, start: datetime, end: datetime) -> ReportSection:
        ledger = self.workspace / ".omni" / "claims.jsonl"
        added = 0
        superseded = 0
        by_domain: dict[str, int] = {}
        if ledger.exists():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_iso(record.get("t_valid_from", ""))
                if ts is None or not (start <= ts <= end):
                    continue
                added += 1
                domain = str(record.get("domain", "(unknown)"))
                by_domain[domain] = by_domain.get(domain, 0) + 1
                if record.get("review_state") == "superseded":
                    superseded += 1
        stats = {
            "added": added,
            "superseded": superseded,
            "by_domain": by_domain,
        }
        body = [f"- 新增 claim: **{added}**", f"- 被替换 claim: **{superseded}**"]
        if by_domain:
            body.append("- 域分布:")
            for d in sorted(by_domain, key=lambda d: -by_domain[d]):
                body.append(f"  - `{d}`: {by_domain[d]}")
        return ReportSection(
            title="ClaimLedger",
            body_md="\n".join(body),
            stats=stats,
        )

    def _lint_section(self, start: datetime, end: datetime) -> ReportSection:
        # Lint findings land in proposals.sqlite3 as ``kind=lint_finding``.
        # We tally by rule + severity for the window.
        proposals_db = self.workspace / ".omni" / "proposals.sqlite3"
        rule_counts: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        total = 0
        if proposals_db.exists():
            import sqlite3
            try:
                with sqlite3.connect(proposals_db) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        "SELECT created_at, payload FROM proposals "
                        "WHERE kind = ? AND created_at >= ? AND created_at <= ?",
                        ("lint_finding", start.isoformat(), end.isoformat()),
                    ).fetchall()
                for row in rows:
                    total += 1
                    try:
                        payload = json.loads(row["payload"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    rule = str(payload.get("rule", "(unknown)"))
                    severity = str(payload.get("severity", "(unknown)"))
                    rule_counts[rule] = rule_counts.get(rule, 0) + 1
                    severity_counts[severity] = severity_counts.get(severity, 0) + 1
            except sqlite3.Error:
                pass
        body = [f"- 总发现数: **{total}**"]
        if rule_counts:
            body.append("- 按规则:")
            for r in sorted(rule_counts, key=lambda r: -rule_counts[r]):
                body.append(f"  - `{r}`: {rule_counts[r]}")
        if severity_counts:
            body.append("- 按严重度:")
            for s in sorted(severity_counts, key=lambda s: -severity_counts[s]):
                body.append(f"  - `{s}`: {severity_counts[s]}")
        return ReportSection(
            title="wiki-lint findings",
            body_md="\n".join(body),
            stats={
                "total": total,
                "by_rule": rule_counts,
                "by_severity": severity_counts,
            },
        )

    def _preference_section(self, start: datetime, end: datetime) -> ReportSection:
        pref_root = self.workspace / ".omni" / "preference"
        accepted = 0
        rejected = 0
        by_domain: dict[str, dict[str, int]] = {}
        if pref_root.exists():
            for jsonl in pref_root.glob("*.jsonl"):
                domain = jsonl.stem
                d_acc = 0
                d_rej = 0
                for line in jsonl.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = _parse_iso(record.get("created_at", ""))
                    if ts is None or not (start <= ts <= end):
                        continue
                    decision = str(record.get("decision", ""))
                    if decision == "accepted":
                        accepted += 1
                        d_acc += 1
                    elif decision == "rejected":
                        rejected += 1
                        d_rej += 1
                if d_acc or d_rej:
                    by_domain[domain] = {"accepted": d_acc, "rejected": d_rej}
        body = [f"- 接受: **{accepted}**", f"- 拒绝: **{rejected}**"]
        if by_domain:
            body.append("- 按域:")
            for d in sorted(by_domain):
                stats = by_domain[d]
                body.append(
                    f"  - `{d}`: accepted={stats['accepted']} rejected={stats['rejected']}"
                )
        return ReportSection(
            title="PreferenceStore",
            body_md="\n".join(body),
            stats={
                "accepted": accepted,
                "rejected": rejected,
                "by_domain": by_domain,
            },
        )

    def _workflow_section(self, start: datetime, end: datetime) -> ReportSection:
        wf_db = self.workspace / ".omni" / "workflows.sqlite3"
        completed = 0
        failed = 0
        suspended = 0
        if wf_db.exists():
            import sqlite3
            try:
                with sqlite3.connect(wf_db) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        "SELECT state, updated_at FROM workflows "
                        "WHERE updated_at >= ? AND updated_at <= ?",
                        (start.isoformat(), end.isoformat()),
                    ).fetchall()
                for row in rows:
                    st = (row["state"] or "").lower()
                    if st == "completed":
                        completed += 1
                    elif st == "failed":
                        failed += 1
                    elif st == "suspended":
                        suspended += 1
            except sqlite3.Error:
                pass
        body = [
            f"- 完成: **{completed}**",
            f"- 失败: **{failed}**",
            f"- 暂停 (等批准): **{suspended}**",
        ]
        return ReportSection(
            title="WorkflowKernel",
            body_md="\n".join(body),
            stats={
                "completed": completed,
                "failed": failed,
                "suspended": suspended,
            },
        )

    # ---- render --------------------------------------------------

    def _render_markdown(
        self,
        period: ReportPeriod,
        start: datetime,
        end: datetime,
        sections: list[ReportSection],
    ) -> str:
        title = {
            ReportPeriod.DAILY: "日报",
            ReportPeriod.WEEKLY: "周报",
            ReportPeriod.MONTHLY: "月报",
        }[period]
        out = [
            f"# omni-hub {title}",
            "",
            f"- 周期: `{period.value}`",
            f"- 窗口: `{start.isoformat()}` → `{end.isoformat()}`",
            "",
        ]
        for section in sections:
            out.extend([f"## {section.title}", "", section.body_md, ""])
        out.extend([
            "---",
            "",
            "_由 ReportOrchestrator (v0.19) 生成。无 LLM 调用,纯数据汇总。",
            "对叙述/趋势分析,enqueue `report-narrate` task 让 claude/codex 走 Proposal[T]。_",
            "",
        ])
        return "\n".join(out)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


__all__ = ["ReportOrchestrator", "ReportPeriod", "ReportSection", "ReportSummary"]
