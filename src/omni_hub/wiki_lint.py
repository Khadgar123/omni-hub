"""Karpathy LLM-Wiki Lint engine.

Implements the six wiki-lint rules declared in `vault/wiki/AGENTS.md` (the
schema document).  Lint is a *read-only* pass; every finding is materialised
as ``Proposal(kind="lint_finding")`` so the human review path matches the
rest of the control plane.

Rules
-----

1. ``contradiction`` — two claims share a statement key but live in
   opposite stances (one in ``support`` and one in ``against``, or two
   approved claims with the same statement key and divergent stance).
2. ``stale_fact`` — page frontmatter ``t_valid_to`` lies in the past and
   ``superseded_by`` is null — the wiki has not closed the time window.
3. ``orphan_page`` — page has no inbound ``[[slug]]`` link from
   ``index.md`` or any other page.
4. ``missing_concept`` — body text references ``[[slug]]`` that has no
   matching ``.md`` under ``vault/wiki/``.
5. ``broken_cross_ref`` — page frontmatter ``claim_ids`` lists a claim_id
   that is absent from ``.omni/claims.jsonl``.
6. ``data_gap`` — page tagged ``confidence: low`` for more than
   ``stale_after_days`` days (default 30) with no recent ingest entry in
   ``log.md``.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ._storage import safe_workspace_path
from .knowledge_plane import (
    CLAIM_LEDGER_PATH,
    WIKI_ROOT,
    _slugify,
    _stable_id,
)
from .proposals import PENDING, Proposal, ProposalStore


RULE_CONTRADICTION = "contradiction"
RULE_STALE_FACT = "stale_fact"
RULE_ORPHAN_PAGE = "orphan_page"
RULE_MISSING_CONCEPT = "missing_concept"
RULE_BROKEN_CROSS_REF = "broken_cross_ref"
RULE_DATA_GAP = "data_gap"
# v0.17-L: super-SOTA additions answering published community critiques
# (Proudfrog / foundanand / Avi Chawla / Karpathy gist discussions).
RULE_CROSS_REF_ASYMMETRY = "cross_ref_asymmetry"   # high-confidence one-way link → likely hallucinated
RULE_ABANDONED_PAGE = "abandoned_page"             # second-brain graveyard suppression

ALL_RULES = (
    RULE_CONTRADICTION,
    RULE_STALE_FACT,
    RULE_ORPHAN_PAGE,
    RULE_MISSING_CONCEPT,
    RULE_BROKEN_CROSS_REF,
    RULE_DATA_GAP,
    RULE_CROSS_REF_ASYMMETRY,
    RULE_ABANDONED_PAGE,
)


WIKI_LINK_RE = re.compile(r"\[\[([^\]\|\#]+)(?:\|[^\]]+)?\]\]")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
META_FILES = {"AGENTS.md", "index.md", "log.md", "_schema.md"}


@dataclass(slots=True)
class LintFinding:
    rule: str
    severity: str
    summary: str
    affected_paths: list[str] = field(default_factory=list)
    affected_claim_ids: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LintReport:
    total: int
    by_rule: dict[str, int]
    findings: list[LintFinding]
    proposal_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "by_rule": dict(self.by_rule),
            "findings": [f.to_dict() for f in self.findings],
            "proposal_ids": list(self.proposal_ids),
        }


@dataclass(slots=True)
class _Page:
    relative_path: str
    frontmatter: dict[str, Any]
    body: str
    outbound_links: list[str]
    mtime: datetime


def lint_wiki(
    workspace: Path | str = ".",
    *,
    domain: str | None = None,
    rules: list[str] | None = None,
    stale_after_days: int = 30,
    now: datetime | None = None,
    persist_proposals: bool = False,
) -> LintReport:
    """Run the wiki-lint pass.  ``persist_proposals=True`` stores each
    finding as ``Proposal(kind="lint_finding", state="pending")`` so the
    human can review via ``propose-list --kind lint_finding``.
    """

    workspace_root = Path(workspace).resolve()
    now = now or datetime.now(UTC)
    requested = set(rules) if rules else set(ALL_RULES)
    unknown = requested - set(ALL_RULES)
    if unknown:
        raise ValueError(f"unknown lint rule(s): {sorted(unknown)}")

    pages = _load_pages(workspace_root, domain=domain)
    claims = _load_claims(workspace_root)

    findings: list[LintFinding] = []
    if RULE_CONTRADICTION in requested:
        findings.extend(_rule_contradiction(claims))
    if RULE_STALE_FACT in requested:
        findings.extend(_rule_stale_fact(pages, now=now))
    if RULE_ORPHAN_PAGE in requested:
        findings.extend(_rule_orphan_page(pages))
    if RULE_MISSING_CONCEPT in requested:
        findings.extend(_rule_missing_concept(pages))
    if RULE_BROKEN_CROSS_REF in requested:
        findings.extend(_rule_broken_cross_ref(pages, claims))
    if RULE_DATA_GAP in requested:
        findings.extend(
            _rule_data_gap(pages, workspace=workspace_root, now=now, stale_after_days=stale_after_days)
        )
    if RULE_CROSS_REF_ASYMMETRY in requested:
        findings.extend(_rule_cross_ref_asymmetry(pages))
    if RULE_ABANDONED_PAGE in requested:
        findings.extend(_rule_abandoned_page(pages, now=now))

    # Apply per-domain severity overrides + skip filters declared in
    # `DomainSchema.rule_overrides`.  This runs AFTER rule emission so the
    # detection logic stays domain-agnostic.
    findings = _apply_domain_rule_overrides(findings, pages=pages, claims=claims)

    proposal_ids: list[str] = []
    if persist_proposals and findings:
        store = ProposalStore(workspace_root)
        for finding in findings:
            proposal = Proposal(
                kind="lint_finding",
                state=PENDING,
                title=f"[{finding.rule}] {finding.summary[:80]}",
                summary=finding.summary[:500],
                source_path=finding.affected_paths[0] if finding.affected_paths else WIKI_ROOT,
                confidence=_severity_to_confidence(finding.severity),
                suggested_action=_suggested_action(finding.rule),
                payload={
                    "rule": finding.rule,
                    "severity": finding.severity,
                    "affected_paths": list(finding.affected_paths),
                    "affected_claim_ids": list(finding.affected_claim_ids),
                    "detail": dict(finding.detail),
                },
            )
            store.store(proposal, write_card=False)
            proposal_ids.append(proposal.proposal_id)

    by_rule: dict[str, int] = {}
    for finding in findings:
        by_rule[finding.rule] = by_rule.get(finding.rule, 0) + 1

    return LintReport(
        total=len(findings),
        by_rule=by_rule,
        findings=findings,
        proposal_ids=proposal_ids,
    )


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------


def _rule_contradiction(claims: list[dict[str, Any]]) -> list[LintFinding]:
    """Pair claims that share a statement key but disagree.

    A statement key is the first 60 chars of statement, lowercased and
    whitespace-collapsed.  Two claims with the same key are flagged as a
    contradiction when:

    * one has the other listed in ``against`` (explicit), OR
    * one is approved while the other is rejected (state divergence), OR
    * both approved and their support source_id sets are disjoint (likely
      independent claims about the same statement).

    The check is conservative — it surfaces candidate pairs for human
    review, it does not auto-resolve.
    """

    by_key: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        if claim.get("t_valid_to") is not None:
            continue
        # v0.17-C: skip claims already resolved into conflict/rejected
        # state — otherwise `keep_both` decisions loop forever as the
        # next lint pass re-emits the same contradiction finding.
        if str(claim.get("review_state", "")).strip().lower() in {"conflict", "rejected"}:
            continue
        key = _statement_key(str(claim.get("statement", "")))
        if not key:
            continue
        by_key.setdefault(key, []).append(claim)

    findings: list[LintFinding] = []
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        # Pair every (i, j) but only emit when stances actually diverge.
        seen_pairs: set[tuple[str, str]] = set()
        for i, claim_a in enumerate(group):
            for claim_b in group[i + 1 :]:
                ids = tuple(sorted([str(claim_a["claim_id"]), str(claim_b["claim_id"])]))
                if ids in seen_pairs:
                    continue
                seen_pairs.add(ids)
                if not _stance_diverges(claim_a, claim_b):
                    continue
                findings.append(
                    LintFinding(
                        rule=RULE_CONTRADICTION,
                        severity="high",
                        summary=(
                            f"claims {ids[0]} ↔ {ids[1]} share key "
                            f"\"{key[:40]}...\" with divergent stance"
                        ),
                        affected_claim_ids=list(ids),
                        affected_paths=sorted(
                            {
                                str(claim_a.get("target_path", "")),
                                str(claim_b.get("target_path", "")),
                            }
                            - {""}
                        ),
                        detail={
                            "statement_key": key,
                            "claim_a": _claim_thumbnail(claim_a),
                            "claim_b": _claim_thumbnail(claim_b),
                        },
                    )
                )
    return findings


def _rule_stale_fact(pages: list[_Page], *, now: datetime) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for page in pages:
        t_valid_to = page.frontmatter.get("t_valid_to")
        superseded_by = page.frontmatter.get("superseded_by")
        if not t_valid_to or _is_null(t_valid_to):
            continue
        parsed = _parse_iso(str(t_valid_to))
        if parsed is None or parsed >= now:
            continue
        if not _is_null(superseded_by):
            continue
        findings.append(
            LintFinding(
                rule=RULE_STALE_FACT,
                severity="medium",
                summary=(
                    f"{page.relative_path}: t_valid_to={t_valid_to} is in the past "
                    "but superseded_by is null"
                ),
                affected_paths=[page.relative_path],
                detail={
                    "t_valid_to": str(t_valid_to),
                    "now": now.isoformat(),
                },
            )
        )
    return findings


def _rule_orphan_page(pages: list[_Page]) -> list[LintFinding]:
    by_slug: dict[str, _Page] = {}
    for page in pages:
        slug = _path_to_slug(page.relative_path)
        by_slug[slug] = page

    inbound: dict[str, set[str]] = {slug: set() for slug in by_slug}
    for page in pages:
        for target in page.outbound_links:
            target_slug = _slugify(target)
            if target_slug in inbound and target_slug != _path_to_slug(page.relative_path):
                inbound[target_slug].add(page.relative_path)

    findings: list[LintFinding] = []
    for slug, page in by_slug.items():
        if inbound[slug]:
            continue
        # index.md inbound-link check: if index.md mentions the page path
        # in markdown link form, consider it linked.
        findings.append(
            LintFinding(
                rule=RULE_ORPHAN_PAGE,
                severity="low",
                summary=f"orphan page: {page.relative_path} has no inbound [[link]]",
                affected_paths=[page.relative_path],
                detail={"slug": slug},
            )
        )
    return findings


def _rule_missing_concept(pages: list[_Page]) -> list[LintFinding]:
    page_slugs = {_path_to_slug(p.relative_path) for p in pages}
    findings: list[LintFinding] = []
    for page in pages:
        for target in page.outbound_links:
            target_slug = _slugify(target)
            if not target_slug or target_slug in page_slugs:
                continue
            findings.append(
                LintFinding(
                    rule=RULE_MISSING_CONCEPT,
                    severity="low",
                    summary=(
                        f"{page.relative_path}: [[{target}]] has no matching wiki page"
                    ),
                    affected_paths=[page.relative_path],
                    detail={"missing_slug": target_slug, "raw_link": target},
                )
            )
    return findings


def _rule_broken_cross_ref(
    pages: list[_Page],
    claims: list[dict[str, Any]],
) -> list[LintFinding]:
    known_claim_ids = {str(c.get("claim_id", "")) for c in claims if c.get("claim_id")}
    findings: list[LintFinding] = []
    for page in pages:
        declared = page.frontmatter.get("claim_ids") or []
        if not isinstance(declared, list):
            continue
        missing = [str(cid) for cid in declared if str(cid) not in known_claim_ids]
        if not missing:
            continue
        findings.append(
            LintFinding(
                rule=RULE_BROKEN_CROSS_REF,
                severity="high",
                summary=(
                    f"{page.relative_path}: frontmatter claim_ids reference "
                    f"{len(missing)} missing claim(s)"
                ),
                affected_paths=[page.relative_path],
                affected_claim_ids=missing,
                detail={"missing_claim_ids": missing},
            )
        )
    return findings


def _rule_data_gap(
    pages: list[_Page],
    *,
    workspace: Path,
    now: datetime,
    stale_after_days: int,
) -> list[LintFinding]:
    """Per-domain stale threshold: research → 730d, finance → 30d,
    international_relations → 7d, etc.  The ``stale_after_days`` CLI flag
    overrides the per-domain default when explicitly set (caller signals
    by passing it explicitly — the default 30 matches the floor in
    domain_schemas).
    """

    from .domain_schemas import get_stale_after_days

    findings: list[LintFinding] = []
    for page in pages:
        confidence = str(page.frontmatter.get("confidence", "")).lower()
        if confidence != "low":
            continue
        domain = str(page.frontmatter.get("domain", "")).strip()
        # Resolve threshold: CLI override (passed in) takes precedence when
        # it differs from the floor (30); otherwise use domain default.
        if stale_after_days != 30:
            threshold_days = stale_after_days
        else:
            threshold_days = get_stale_after_days(domain or "default", default=30)
        threshold = now - timedelta(days=int(threshold_days))
        if page.mtime >= threshold:
            continue
        findings.append(
            LintFinding(
                rule=RULE_DATA_GAP,
                severity="low",
                summary=(
                    f"{page.relative_path}: confidence=low and unchanged for "
                    f">{threshold_days}d (mtime={page.mtime.isoformat()}, "
                    f"domain={domain or 'default'})"
                ),
                affected_paths=[page.relative_path],
                detail={
                    "mtime": page.mtime.isoformat(),
                    "threshold": threshold.isoformat(),
                    "stale_after_days": threshold_days,
                    "domain": domain or "default",
                },
            )
        )
    return findings


# ---------------------------------------------------------------------------
# v0.17-L: super-SOTA rules answering community critiques
# ---------------------------------------------------------------------------


def _rule_cross_ref_asymmetry(pages: list[_Page]) -> list[LintFinding]:
    """High-confidence one-way wiki link — likely hallucinated cross-ref.

    Answers Proudfrog / foundanand "hallucination contamination" critique:
    when an LLM-authored page links `[[other-page]]` but `other-page` has
    no inbound link back AND confidence on the source page is `high`,
    that link is a candidate fabrication.  We flag pairs where:

      * page A frontmatter ``confidence: high``,
      * page A body contains ``[[B-slug]]``,
      * page B exists, but has NO outbound link or claim referencing A.

    The check is intentionally narrow — only `confidence: high` pages,
    only true asymmetry (B doesn't mention A at all).  Otherwise we'd
    flood the lint pipeline.
    """

    by_slug: dict[str, _Page] = {
        _path_to_slug(p.relative_path): p for p in pages
    }
    findings: list[LintFinding] = []
    seen_pairs: set[tuple[str, str]] = set()
    for page_a in pages:
        confidence = str(page_a.frontmatter.get("confidence", "")).strip().lower()
        if confidence != "high":
            continue
        slug_a = _path_to_slug(page_a.relative_path)
        for raw_link in page_a.outbound_links:
            slug_b = _slugify(raw_link)
            if not slug_b or slug_b == slug_a:
                continue
            page_b = by_slug.get(slug_b)
            if page_b is None:
                continue                        # caught by missing_concept
            pair = (slug_a, slug_b)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            # B asymmetry: does B's body or frontmatter reference A in any form?
            b_links = {_slugify(link) for link in page_b.outbound_links}
            b_claim_ids = page_b.frontmatter.get("claim_ids") or []
            a_claim_ids = set(page_a.frontmatter.get("claim_ids") or [])
            if slug_a in b_links:
                continue
            if isinstance(b_claim_ids, list) and a_claim_ids.intersection(b_claim_ids):
                continue
            findings.append(
                LintFinding(
                    rule=RULE_CROSS_REF_ASYMMETRY,
                    severity="medium",
                    summary=(
                        f"{page_a.relative_path} (confidence=high) links "
                        f"[[{raw_link}]] but {page_b.relative_path} never "
                        "references it back — candidate hallucinated cross-ref"
                    ),
                    affected_paths=[page_a.relative_path, page_b.relative_path],
                    detail={
                        "from_slug": slug_a,
                        "to_slug": slug_b,
                        "raw_link": raw_link,
                    },
                )
            )
    return findings


def _rule_abandoned_page(
    pages: list[_Page],
    *,
    now: datetime,
    abandoned_after_days: int = 180,
) -> list[LintFinding]:
    """Second-brain graveyard suppression.

    Answers the "Evernote / Roam / Mem.ai collapsed on maintenance burden"
    critique.  Triggers when a page has:

      * `confidence: low`,
      * mtime > 180 days ago,
      * no inbound `[[link]]` from any other page,
      * no claim in ``.omni/claims.jsonl`` still open against it.

    Suggested action: archive (set `t_valid_to` + `review_state=rejected`,
    move under `vault/wiki/90_Archive/`) so the wiki stops costing
    attention without losing the audit trail.
    """

    by_slug: dict[str, _Page] = {
        _path_to_slug(p.relative_path): p for p in pages
    }
    inbound: dict[str, set[str]] = {slug: set() for slug in by_slug}
    for page in pages:
        for target in page.outbound_links:
            slug = _slugify(target)
            if slug in inbound and slug != _path_to_slug(page.relative_path):
                inbound[slug].add(page.relative_path)

    threshold = now - timedelta(days=int(abandoned_after_days))
    findings: list[LintFinding] = []
    for page in pages:
        confidence = str(page.frontmatter.get("confidence", "")).strip().lower()
        if confidence != "low":
            continue
        if page.mtime >= threshold:
            continue
        slug = _path_to_slug(page.relative_path)
        if inbound.get(slug):
            continue
        findings.append(
            LintFinding(
                rule=RULE_ABANDONED_PAGE,
                severity="low",
                summary=(
                    f"{page.relative_path}: confidence=low, untouched "
                    f">{abandoned_after_days}d, no inbound links — candidate archive"
                ),
                affected_paths=[page.relative_path],
                detail={
                    "mtime": page.mtime.isoformat(),
                    "threshold": threshold.isoformat(),
                    "abandoned_after_days": abandoned_after_days,
                },
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Domain override layer
# ---------------------------------------------------------------------------


def _apply_domain_rule_overrides(
    findings: list[LintFinding],
    *,
    pages: list[_Page],
    claims: list[dict[str, Any]],
) -> list[LintFinding]:
    """Re-stamp severity (or drop entirely) per `DomainSchema.rule_overrides`.

    Resolution order for the domain attached to a finding:

    1. If the finding has `affected_paths`, take the domain from the first
       page's frontmatter.
    2. Otherwise, if it has `affected_claim_ids`, take the claim's domain.
    3. Otherwise treat as "default" (no override applies).
    """

    from .domain_schemas import get_rule_override

    page_by_path = {p.relative_path: p for p in pages}
    claim_by_id = {str(c.get("claim_id", "")): c for c in claims if c.get("claim_id")}

    kept: list[LintFinding] = []
    for finding in findings:
        domain = _domain_for_finding(finding, page_by_path, claim_by_id)
        override = get_rule_override(domain, finding.rule) if domain else None
        if not override:
            kept.append(finding)
            continue
        if override.lower() == "skip":
            # Domain says don't emit this rule.
            continue
        if override in {"low", "medium", "high"}:
            finding.severity = override
            finding.detail = dict(finding.detail)
            # Record the override even when it equals the rule's default —
            # the domain explicitly opted in, so the audit trail benefits.
            finding.detail["domain_override"] = domain
        kept.append(finding)
    return kept


def _domain_for_finding(
    finding: LintFinding,
    page_by_path: dict[str, _Page],
    claim_by_id: dict[str, dict[str, Any]],
) -> str:
    for path in finding.affected_paths:
        page = page_by_path.get(path)
        if page is not None:
            domain = str(page.frontmatter.get("domain", "")).strip()
            if domain:
                return domain
    for cid in finding.affected_claim_ids:
        claim = claim_by_id.get(str(cid))
        if claim is not None:
            domain = str(claim.get("domain", "")).strip()
            if domain:
                return domain
    return ""


# ---------------------------------------------------------------------------
# Loaders + helpers
# ---------------------------------------------------------------------------


def _load_pages(workspace: Path, *, domain: str | None = None) -> list[_Page]:
    wiki_root = safe_workspace_path(workspace, WIKI_ROOT)
    if not wiki_root.exists():
        return []
    pages: list[_Page] = []
    for path in sorted(wiki_root.rglob("*.md")):
        if path.name in META_FILES:
            continue
        relative = str(path.relative_to(workspace))
        if domain and f"/domains/{_slugify(domain)}/" not in relative.replace("\\", "/"):
            # Filter only when the user pinned a domain; non-domain pages
            # (concepts/, methods/, syntheses/, ...) are always included
            # in domain-pinned runs because they may cross-cut domains.
            if "/domains/" in relative:
                continue
        text = path.read_text(encoding="utf-8")
        frontmatter = _parse_frontmatter(text)
        body = FRONTMATTER_RE.sub("", text, count=1) if FRONTMATTER_RE.match(text) else text
        links = WIKI_LINK_RE.findall(body)
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        pages.append(
            _Page(
                relative_path=relative,
                frontmatter=frontmatter,
                body=body,
                outbound_links=links,
                mtime=mtime,
            )
        )
    return pages


def _load_claims(workspace: Path) -> list[dict[str, Any]]:
    path = safe_workspace_path(workspace, CLAIM_LEDGER_PATH)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Minimal YAML-frontmatter parser — supports the shapes the wiki
    schema uses (scalar, null, list of scalars).  Reject anything more
    complex to keep us stdlib-only.
    """

    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    out: dict[str, Any] = {}
    for raw_line in match.group(1).splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if ":" not in raw_line:
            continue
        key, _, value = raw_line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        out[key] = _parse_yaml_value(value)
    return out


def _parse_yaml_value(value: str) -> Any:
    if not value:
        return ""
    if value.lower() == "null":
        return None
    if value.startswith("[") and value.endswith("]"):
        # JSON-style list (the synthesis page writes claim_ids as JSON).
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [piece.strip().strip('"').strip("'") for piece in inner.split(",")]
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def _statement_key(statement: str) -> str:
    return re.sub(r"\s+", " ", statement.strip().casefold())[:60]


def _stance_diverges(claim_a: dict[str, Any], claim_b: dict[str, Any]) -> bool:
    """Return True if the two claims declare conflicting stance.

    Three signals (any one fires):
      * either claim lists the other in its ``against`` array,
      * review states diverge (one approved, the other rejected),
      * both approved but support source_id sets are disjoint AND
        confidence gap >= 0.2 (very different sources giving different weight
        to the same statement is the classic Wikipedia-style flag).
    """

    against_a = {str(item.get("claim_id", "")) for item in claim_a.get("against", []) if isinstance(item, dict)}
    against_b = {str(item.get("claim_id", "")) for item in claim_b.get("against", []) if isinstance(item, dict)}
    if str(claim_b.get("claim_id", "")) in against_a:
        return True
    if str(claim_a.get("claim_id", "")) in against_b:
        return True

    states = {str(claim_a.get("review_state", "")), str(claim_b.get("review_state", ""))}
    if "approved" in states and "rejected" in states:
        return True

    if states == {"approved"}:
        support_a = {str(item.get("source_id", "")) for item in claim_a.get("support", []) if isinstance(item, dict)}
        support_b = {str(item.get("source_id", "")) for item in claim_b.get("support", []) if isinstance(item, dict)}
        support_a.discard("")
        support_b.discard("")
        disjoint = bool(support_a) and bool(support_b) and not (support_a & support_b)
        gap = abs(float(claim_a.get("confidence", 0.0)) - float(claim_b.get("confidence", 0.0)))
        if disjoint and gap >= 0.2:
            return True
    return False


def _path_to_slug(relative_path: str) -> str:
    name = Path(relative_path).stem
    return _slugify(name)


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in {"", "null", "none"}:
        return True
    return False


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _claim_thumbnail(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim_id": claim.get("claim_id"),
        "statement": str(claim.get("statement", ""))[:120],
        "confidence": claim.get("confidence"),
        "review_state": claim.get("review_state"),
        "support_count": len(claim.get("support", []) or []),
        "target_path": claim.get("target_path"),
    }


def _severity_to_confidence(severity: str) -> float:
    return {
        "high": 0.85,
        "medium": 0.6,
        "low": 0.4,
    }.get(severity, 0.5)


def _suggested_action(rule: str) -> str:
    return {
        RULE_CONTRADICTION: "wiki_conflict_resolve",
        RULE_STALE_FACT: "wiki_supersede_or_extend_validity",
        RULE_ORPHAN_PAGE: "wiki_add_inbound_link",
        RULE_MISSING_CONCEPT: "wiki_create_referenced_page",
        RULE_BROKEN_CROSS_REF: "wiki_repair_claim_ids",
        RULE_DATA_GAP: "wiki_reingest_recent_evidence",
    }.get(rule, "wiki_manual_review")
