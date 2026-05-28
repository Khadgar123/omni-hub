"""Local-first Wiki Dream — Anthropic Dreaming (2026-05-06) parity for omni-hub.

The pattern Anthropic shipped (Managed Agents `dreams`) runs an offline pass
between sessions that reads the raw transcript + memory store, extracts
patterns, merges duplicates, and writes new memory entries.  Harvey reports
~6× task-completion gains.

This module is the on-prem dual: a deterministic, stdlib-only "dream" pass
over `vault/raw/`, `.omni/retrieval/<run_id>/evidence.jsonl`, and
`.omni/claims.jsonl` — produces ``Proposal(kind="wiki_dream")`` candidates a
human reviews via the standard control plane.

Heuristics
----------

1. **Cluster by canonical_id** — when ≥ 2 evidence records across runs cite
   the same `canonical_id` AND no wiki page already references it, propose
   a new concept/entity page.
2. **Statement-key duplicate cluster** — when ≥ 3 open claims share the
   first-60-chars statement key but live on different pages, propose a
   synthesis page that pulls them together.
3. **Raw without evidence** — when a file lives in `vault/raw/` but no
   `.omni/retrieval/<run_id>/evidence.jsonl` references it, propose
   running `wiki-ingest` over it (or marking it manual-only).
4. **Stale-but-active topic** — when ≥ 3 retrieval runs in the last 14
   days cite the same topic (canonical_id family) but the corresponding
   wiki page's `t_valid_from` is > 60 days old, propose a refresh.

Each finding becomes one Proposal(kind="wiki_dream") with payload:

    {
      "rule": "cluster_canonical | statement_cluster | raw_orphan | stale_active",
      "severity": "low" | "medium" | "high",
      "evidence_refs": [".omni/retrieval/<run_id>/evidence.jsonl#0", ...],
      "affected_pages": ["vault/wiki/..." ],
      "suggested_action": "create_page" | "merge_claims" | "ingest_raw" | "refresh_page",
      "detail": {...}
    }

Scheduling
----------

`schedule-tick --period weekly` enqueues `wiki_dream` as a python-lane task.
launchd plist (`scripts/launchd/com.omni-hub.weekly.plist`) drives it.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ._storage import safe_workspace_path
from .knowledge_plane import (
    CLAIM_LEDGER_PATH,
    EVIDENCE_ROOT,
    RAW_ROOT,
    RETRIEVAL_RUN_ROOT,
    WIKI_ROOT,
    _slugify,
    _stable_id,
)
from .proposals import PENDING, Proposal, ProposalStore


DREAM_STATE_PATH = ".omni/wiki_dream_state.json"

RULE_CLUSTER_CANONICAL = "cluster_canonical"
RULE_STATEMENT_CLUSTER = "statement_cluster"
RULE_RAW_ORPHAN = "raw_orphan"
RULE_STALE_ACTIVE = "stale_active"

ALL_RULES = (
    RULE_CLUSTER_CANONICAL,
    RULE_STATEMENT_CLUSTER,
    RULE_RAW_ORPHAN,
    RULE_STALE_ACTIVE,
)


@dataclass(slots=True)
class DreamFinding:
    rule: str
    severity: str
    summary: str
    suggested_action: str
    evidence_refs: list[str] = field(default_factory=list)
    affected_pages: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DreamReport:
    total: int
    by_rule: dict[str, int]
    findings: list[DreamFinding]
    proposal_ids: list[str] = field(default_factory=list)
    since: str = ""
    until: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "by_rule": dict(self.by_rule),
            "findings": [f.to_dict() for f in self.findings],
            "proposal_ids": list(self.proposal_ids),
            "since": self.since,
            "until": self.until,
        }


def run_dream(
    workspace: Path | str = ".",
    *,
    since_days: int = 7,
    persist_proposals: bool = False,
    rules: list[str] | None = None,
    now: datetime | None = None,
    update_state: bool = True,
) -> DreamReport:
    """Run one offline consolidation pass.

    `since_days=7` mirrors weekly schedule.  Pass `since_days=0` to scan
    everything (e.g. cold-start dream).  Each run advances the
    `last_dream_at` watermark in `.omni/wiki_dream_state.json` when
    `update_state=True`.
    """

    workspace_root = Path(workspace).resolve()
    now = now or datetime.now(UTC)
    state = _load_state(workspace_root)
    if since_days > 0:
        since = max(
            now - timedelta(days=since_days),
            _parse_iso(state.get("last_dream_at", "")) or (now - timedelta(days=since_days)),
        )
    else:
        since = datetime.fromtimestamp(0, tz=UTC)

    requested = set(rules) if rules else set(ALL_RULES)
    unknown = requested - set(ALL_RULES)
    if unknown:
        raise ValueError(f"unknown dream rule(s): {sorted(unknown)}")

    evidence = _load_recent_evidence(workspace_root, since=since)
    wiki_pages = _load_wiki_pages(workspace_root)
    claims = _load_claims(workspace_root)
    raw_files = _load_raw_files(workspace_root)

    findings: list[DreamFinding] = []
    if RULE_CLUSTER_CANONICAL in requested:
        findings.extend(_rule_cluster_canonical(evidence, wiki_pages))
    if RULE_STATEMENT_CLUSTER in requested:
        findings.extend(_rule_statement_cluster(claims))
    if RULE_RAW_ORPHAN in requested:
        findings.extend(_rule_raw_orphan(raw_files, evidence))
    if RULE_STALE_ACTIVE in requested:
        findings.extend(_rule_stale_active(evidence, wiki_pages, now=now))

    proposal_ids: list[str] = []
    if persist_proposals and findings:
        store = ProposalStore(workspace_root)
        for finding in findings:
            proposal = Proposal(
                kind="wiki_dream",
                state=PENDING,
                title=f"[{finding.rule}] {finding.summary[:80]}",
                summary=finding.summary[:500],
                source_path=DREAM_STATE_PATH,
                confidence=_severity_to_confidence(finding.severity),
                suggested_action=finding.suggested_action,
                payload={
                    "rule": finding.rule,
                    "severity": finding.severity,
                    "evidence_refs": list(finding.evidence_refs),
                    "affected_pages": list(finding.affected_pages),
                    "detail": dict(finding.detail),
                },
            )
            store.store(proposal, write_card=False)
            proposal_ids.append(proposal.proposal_id)

    by_rule: dict[str, int] = {}
    for f in findings:
        by_rule[f.rule] = by_rule.get(f.rule, 0) + 1

    if update_state:
        state["last_dream_at"] = now.isoformat()
        state.setdefault("history", []).append(
            {"at": now.isoformat(), "since": since.isoformat(), "findings": len(findings)}
        )
        state["history"] = state["history"][-30:]
        _save_state(workspace_root, state)

    return DreamReport(
        total=len(findings),
        by_rule=by_rule,
        findings=findings,
        proposal_ids=proposal_ids,
        since=since.isoformat(),
        until=now.isoformat(),
    )


# ---------------------------------------------------------------------------
# Heuristic rules
# ---------------------------------------------------------------------------


def _rule_cluster_canonical(
    evidence: list[dict[str, Any]],
    wiki_pages: dict[str, dict[str, Any]],
) -> list[DreamFinding]:
    """≥2 evidence records share canonical_id AND no wiki page cites it."""

    by_canonical: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in evidence:
        cid = str(record.get("canonical_id") or "").strip()
        if cid:
            by_canonical[cid].append(record)

    referenced_canonicals: set[str] = set()
    for page in wiki_pages.values():
        for src in page.get("frontmatter", {}).get("source_ids", []) or []:
            referenced_canonicals.add(str(src))

    findings: list[DreamFinding] = []
    for canonical, records in by_canonical.items():
        if len(records) < 2 or canonical in referenced_canonicals:
            continue
        titles = sorted({str(r.get("title", "")).strip() for r in records if r.get("title")})
        domains = sorted({str(r.get("_domain_hint", "")) for r in records if r.get("_domain_hint")})
        sample_title = titles[0] if titles else canonical
        findings.append(
            DreamFinding(
                rule=RULE_CLUSTER_CANONICAL,
                severity="medium",
                summary=(
                    f"{len(records)} retrieval hits on `{canonical[:40]}` "
                    f"({sample_title[:60]}) but no wiki page references it yet"
                ),
                suggested_action="create_page",
                evidence_refs=[r.get("_evidence_ref", "") for r in records],
                detail={
                    "canonical_id": canonical,
                    "hit_count": len(records),
                    "sample_titles": titles[:3],
                    "domains": domains,
                },
            )
        )
    return findings


def _rule_statement_cluster(claims: list[dict[str, Any]]) -> list[DreamFinding]:
    """≥3 open claims share statement key but live on different pages."""

    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        if claim.get("t_valid_to") is not None:
            continue
        if str(claim.get("review_state", "")).lower() in {"rejected", "superseded"}:
            continue
        key = _statement_key(str(claim.get("statement", "")))
        if key:
            by_key[key].append(claim)

    findings: list[DreamFinding] = []
    for key, group in by_key.items():
        if len(group) < 3:
            continue
        target_paths = sorted({str(c.get("target_path", "")) for c in group if c.get("target_path")})
        if len(target_paths) < 2:
            # All on the same page already — no consolidation needed.
            continue
        findings.append(
            DreamFinding(
                rule=RULE_STATEMENT_CLUSTER,
                severity="low",
                summary=(
                    f"{len(group)} open claims share key \"{key[:40]}…\" "
                    f"across {len(target_paths)} pages — candidate for synthesis"
                ),
                suggested_action="merge_claims",
                affected_pages=target_paths,
                detail={
                    "statement_key": key,
                    "claim_ids": [c.get("claim_id") for c in group],
                    "target_paths": target_paths,
                },
            )
        )
    return findings


def _rule_raw_orphan(
    raw_files: list[str],
    evidence: list[dict[str, Any]],
) -> list[DreamFinding]:
    """vault/raw/ file with no evidence.jsonl reference."""

    referenced_paths: set[str] = set()
    for record in evidence:
        ref = str(record.get("raw_path") or "").strip()
        if ref:
            referenced_paths.add(ref)

    findings: list[DreamFinding] = []
    for raw_path in raw_files:
        if raw_path in referenced_paths:
            continue
        findings.append(
            DreamFinding(
                rule=RULE_RAW_ORPHAN,
                severity="low",
                summary=f"raw file `{raw_path}` has no evidence reference — needs ingest",
                suggested_action="ingest_raw",
                affected_pages=[raw_path],
                detail={"raw_path": raw_path},
            )
        )
    return findings


def _rule_stale_active(
    evidence: list[dict[str, Any]],
    wiki_pages: dict[str, dict[str, Any]],
    *,
    now: datetime,
    refresh_after_days: int = 60,
) -> list[DreamFinding]:
    """Topic is hot in retrieval but the wiki page is > 60 days old."""

    hits_by_canonical: dict[str, int] = defaultdict(int)
    for record in evidence:
        cid = str(record.get("canonical_id") or "").strip()
        if cid:
            hits_by_canonical[cid] += 1

    pages_by_canonical: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for path, page in wiki_pages.items():
        for src in page.get("frontmatter", {}).get("source_ids", []) or []:
            pages_by_canonical[str(src)].append((path, page))

    findings: list[DreamFinding] = []
    threshold = now - timedelta(days=refresh_after_days)
    for canonical, hits in hits_by_canonical.items():
        if hits < 3:
            continue
        pages = pages_by_canonical.get(canonical, [])
        if not pages:
            continue
        for path, page in pages:
            t_from = page.get("frontmatter", {}).get("t_valid_from")
            parsed = _parse_iso(str(t_from)) if t_from else None
            if parsed is None or parsed > threshold:
                continue
            findings.append(
                DreamFinding(
                    rule=RULE_STALE_ACTIVE,
                    severity="medium",
                    summary=(
                        f"`{path}` cites `{canonical[:32]}` (t_valid_from={t_from}); "
                        f"{hits} fresh retrieval hits — candidate refresh"
                    ),
                    suggested_action="refresh_page",
                    affected_pages=[path],
                    detail={
                        "canonical_id": canonical,
                        "fresh_hit_count": hits,
                        "t_valid_from": str(t_from),
                    },
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_recent_evidence(
    workspace: Path,
    *,
    since: datetime,
) -> list[dict[str, Any]]:
    """Walk `.omni/retrieval/<run_id>/evidence.jsonl` for runs whose
    manifest `written_at` >= since."""

    root = safe_workspace_path(workspace, RETRIEVAL_RUN_ROOT)
    if not root.exists():
        return []
    records: list[dict[str, Any]] = []
    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue
        manifest_path = run_dir / "run_manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        written_at = _parse_iso(str(manifest.get("written_at", "")))
        if written_at is None or written_at < since:
            continue
        evidence_path = run_dir / "evidence.jsonl"
        if not evidence_path.exists():
            continue
        domain_hint = str(manifest.get("domain", ""))
        ref_root = f"{RETRIEVAL_RUN_ROOT}/{run_dir.name}/evidence.jsonl"
        for idx, line in enumerate(evidence_path.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            record["_evidence_ref"] = f"{ref_root}#{idx}"
            record["_domain_hint"] = domain_hint
            records.append(record)
    return records


def _load_wiki_pages(workspace: Path) -> dict[str, dict[str, Any]]:
    """Return {relative_path: {frontmatter, body}} for all non-meta wiki .md."""

    wiki_root = safe_workspace_path(workspace, WIKI_ROOT)
    if not wiki_root.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    from .wiki_lint import _parse_frontmatter
    for path in sorted(wiki_root.rglob("*.md")):
        if path.name in {"AGENTS.md", "index.md", "log.md", "_schema.md"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        relative = str(path.relative_to(workspace))
        out[relative] = {
            "frontmatter": _parse_frontmatter(text),
            "body": text,
        }
    return out


def _load_claims(workspace: Path) -> list[dict[str, Any]]:
    ledger = safe_workspace_path(workspace, CLAIM_LEDGER_PATH)
    if not ledger.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _load_raw_files(workspace: Path) -> list[str]:
    raw_root = safe_workspace_path(workspace, RAW_ROOT)
    if not raw_root.exists():
        return []
    out: list[str] = []
    for path in raw_root.rglob("*"):
        if not path.is_file() or path.name == ".gitkeep":
            continue
        out.append(str(path.relative_to(workspace)))
    return out


# ---------------------------------------------------------------------------
# State + helpers
# ---------------------------------------------------------------------------


def _load_state(workspace: Path) -> dict[str, Any]:
    path = safe_workspace_path(workspace, DREAM_STATE_PATH)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_state(workspace: Path, state: dict[str, Any]) -> None:
    path = safe_workspace_path(workspace, DREAM_STATE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _statement_key(statement: str) -> str:
    return re.sub(r"\s+", " ", statement.strip().casefold())[:60]


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _severity_to_confidence(severity: str) -> float:
    return {"high": 0.85, "medium": 0.6, "low": 0.4}.get(severity, 0.5)
