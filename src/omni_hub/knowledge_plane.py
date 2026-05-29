from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ._storage import safe_workspace_path
from .proposals import APPROVED, PENDING, Proposal, ProposalStore
from .research_assets import (
    default_sources,
    iter_index,
    read_analysis,
    search as search_research_assets,
)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


WIKI_ROOT = "vault/wiki"
RAW_ROOT = "vault/raw"
EVIDENCE_ROOT = "vault/evidence"
CLAIM_LEDGER_PATH = ".omni/claims.jsonl"
CONTEXT_PACK_ROOT = ".omni/context_packs"
RETRIEVAL_RUN_ROOT = ".omni/retrieval"

WIKI_SCHEMA_VERSION = "v0.19"

WIKI_SCHEMA_BODY = """---
omni_type: wiki_schema
schema_version: {version}
---

# Omni Wiki Schema

This is the contract every agent reads before writing into `vault/wiki/`.
It is the Karpathy *schema layer* — the file that tells an LLM how the wiki
is structured. Editing this file is a wiki-wide change: bump `schema_version`
and append a migration note to `log.md`.

## Three-Layer Lineage

```
vault/raw/          append-only source material (retrieval cascade output)
vault/evidence/     parsed, normalised evidence (one record per source hit)
vault/wiki/         compiled, human-readable knowledge (THIS directory)
.omni/claims.jsonl  reviewed atomic claims, indexed by claim_id
```

The wiki is the **compiled layer**. It is rebuilt from raw+evidence on
`wiki-ingest`; it is NOT re-derived per query. The retrieval cascade
(`omni-hub retrieve`) is the upstream Ingest data source.

## Page Types

Every `.md` under `vault/wiki/` SHOULD declare a `page_type` in YAML
frontmatter. Exemptions: `AGENTS.md`, `index.md`, `log.md`.

| page_type     | Where                              | Purpose                                |
|---------------|------------------------------------|----------------------------------------|
| `concept`     | `concepts/<slug>.md`               | Named idea (e.g. context-engineering)  |
| `entity`      | `entities/<slug>.md`               | Person / org / product / model         |
| `event`       | `events/<slug>.md`                 | Conference / release / incident        |
| `method`      | `methods/<slug>.md`                | Technique / algorithm / pattern        |
| `synthesis`   | `syntheses/<slug>.md`              | Cross-source compiled findings         |
| `domain_page` | `domains/<domain>/<slug>.md`      | Deep page (paper, product, policy)     |

## Required Frontmatter

```yaml
---
page_type: concept | entity | event | method | synthesis | domain_page
domain: research | engineering | finance | us_policy | cn_policy
         | international_relations | ai_progress | photography | fashion
         | chat_relationships | agent_systems | social_en | social_zh
         | meta | fitness_wellness | cooking | travel | marketing
         | enterprise
claim_ids: [c_a1b2c3d4, c_e5f6g7h8]      # cross-ref into .omni/claims.jsonl
source_ids: [arxiv:2510.04618, doi:10.xx]  # canonical_id list backing this page
t_valid_from: 2026-05-28                  # when content becomes correct (bitemporal)
t_valid_to: null                          # null = still valid; set on supersede
superseded_by: null                       # path to replacement page, or null
confidence: high | medium | low
review_state: approved | proposed | conflict
---
```

`t_valid_from / t_valid_to` follow the Graphiti / Zep bitemporal model: old
facts are NEVER deleted, only closed by setting `t_valid_to`. This keeps the
audit trail intact and lets queries pin a viewing date.

## Linking Rules

- **Internal links use wiki style**: `[[other-page-slug]]` or
  `[[other-page-slug|Display Text]]`.
- **Never use absolute filesystem paths** to other wiki pages.
- **Evidence references use citation markers**: `[1]`, `[2]` corresponding to
  a trailing `## References` section listing `source_id` + `vault/evidence/...`
  path or external URL.
- **Cross-domain references** are allowed but both pages SHOULD declare each
  other in `claim_ids` to keep the graph consistent.

## Write Boundary

- **Agents propose, humans approve.** Agents write `Proposal(kind="wiki_update")`
  via `wiki-ingest` or `wiki-propose-research`. The proposal carries the
  target page body + a list of candidate claims. Only after human
  `propose-approve` and `wiki-apply-proposal` does content land in `vault/wiki/`.
- **Direct agent writes are forbidden.** The single exception: `log.md` is
  append-only and may be written by `wiki-log` operations as an audit trail.
- **Manual edits are first-class.** A human may edit any wiki page directly
  in Obsidian / a text editor. After manual edits, run `wiki-lint` to surface
  inconsistencies (broken refs, stale claims, conflicts).

## Lint Rules (`wiki-lint`)

`wiki-lint` produces `Proposal(kind="lint_finding")` for each issue. Rules:

1. **Contradiction** — two claims sharing a statement key but opposite stance
   (one in `support`, one in `against`).
2. **Stale fact** — page with `t_valid_to < now()` and no `superseded_by`.
3. **Orphan page** — page with no inbound `[[...]]` link from `index.md` or
   from any other page.
4. **Missing concept page** — a claim references an entity/concept slug that
   has no dedicated page under `concepts/` or `entities/`.
5. **Broken cross-ref** — frontmatter `claim_ids` entry absent from
   `.omni/claims.jsonl`.
6. **Data gap** — page tagged `confidence: low` for > 30 days with no
   subsequent `wiki-ingest` enrichment.

## Log Format

`log.md` is append-only and chronological. Each entry header MUST be:

```
## [YYYY-MM-DDTHH:MM:SSZ] op | one-line summary
- source: <path | proposal_id | run_id>
```

Where `op` is one of: `ingest`, `apply`, `lint`, `supersede`, `conflict-resolve`,
`manual`. Tail with `grep "^## \\[" vault/wiki/log.md | tail -10`.

## Index Format

`index.md` is content-oriented navigation, not chronological. New entries
auto-append on `wiki-apply-proposal`. Edit by hand to add topical groupings,
"see also" sections, or to demote noisy pages.

## Domain Sub-Schemas

A domain MAY override or extend this schema by writing
`domains/<domain>/_schema.md`. Domain sub-schemas can:

- Add required frontmatter fields for that domain (e.g. research domain may
  require `paper_link`).
- Declare authoritative source priorities (e.g. policy domain prefers
  `federal_register` over `gdelt`).
- Define domain-specific lint rules.

A page's domain sub-schema takes precedence over this global schema where they
conflict; the global schema sets the floor.
""".format(version=WIKI_SCHEMA_VERSION)

WIKI_DIRS = (
    RAW_ROOT,
    EVIDENCE_ROOT,
    WIKI_ROOT,
    f"{WIKI_ROOT}/sources",
    f"{WIKI_ROOT}/summaries",
    f"{WIKI_ROOT}/concepts",
    f"{WIKI_ROOT}/entities",
    f"{WIKI_ROOT}/events",
    f"{WIKI_ROOT}/methods",
    f"{WIKI_ROOT}/claims",
    f"{WIKI_ROOT}/syntheses",
    f"{WIKI_ROOT}/domains",
    f"{WIKI_ROOT}/domains/research",
    f"{WIKI_ROOT}/domains/engineering",
    f"{WIKI_ROOT}/domains/photography",
    f"{WIKI_ROOT}/domains/fashion",
    f"{WIKI_ROOT}/domains/chat-relationships",
    f"{WIKI_ROOT}/domains/finance",
    f"{WIKI_ROOT}/domains/us-policy",
    f"{WIKI_ROOT}/domains/cn-policy",
    f"{WIKI_ROOT}/domains/international-relations",
    f"{WIKI_ROOT}/domains/ai-progress",
    f"{WIKI_ROOT}/domains/agent-systems",
    f"{WIKI_ROOT}/domains/social-en",
    f"{WIKI_ROOT}/domains/social-zh",
    # v0.19 additions: 6 new vertical-skill domains.
    f"{WIKI_ROOT}/domains/meta",
    f"{WIKI_ROOT}/domains/fitness-wellness",
    f"{WIKI_ROOT}/domains/cooking",
    f"{WIKI_ROOT}/domains/travel",
    f"{WIKI_ROOT}/domains/marketing",
    f"{WIKI_ROOT}/domains/enterprise",
)


@dataclass(slots=True)
class WikiSearchResult:
    path: str
    title: str
    snippet: str
    score: float
    frontmatter: dict[str, object] = field(default_factory=dict)
    body_excerpt: str = ""  # first ~600 chars of body, set by progressive disclosure

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["score"] = round(self.score, 4)
        return data


CONTEXT_PACK_TIERS = ("minimal", "standard", "expanded")
# Per-tier excerpt budgets (chars) — calibrated so a 6-result minimal pack
# fits in ~1k tokens, standard in ~5k tokens, expanded uncapped.
_TIER_BUDGETS = {
    "minimal": 0,
    "standard": 600,
    "expanded": 8000,
}


@dataclass(slots=True)
class ContextPack:
    pack_id: str
    query: str
    domain: str
    tier: str = "standard"
    wiki_results: list[WikiSearchResult] = field(default_factory=list)
    research_results: list[dict[str, object]] = field(default_factory=list)
    path: str = ""
    created_at: str = field(default_factory=_utcnow)
    char_budget: int = 0
    total_chars: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "pack_id": self.pack_id,
            "query": self.query,
            "domain": self.domain,
            "tier": self.tier,
            "wiki_results": [result.to_dict() for result in self.wiki_results],
            "research_results": self.research_results,
            "path": self.path,
            "created_at": self.created_at,
            "char_budget": self.char_budget,
            "total_chars": self.total_chars,
        }


def init_layout(workspace: Path | str = ".") -> dict[str, object]:
    workspace_root = Path(workspace).resolve()
    for relative in WIKI_DIRS:
        safe_workspace_path(workspace_root, relative).mkdir(parents=True, exist_ok=True)

    _write_if_missing(
        workspace_root / WIKI_ROOT / "index.md",
        "# Omni Wiki Index\n\n"
        "Karpathy-style compiled wiki. Read this first, then drill into domain pages.\n",
    )
    _write_if_missing(
        workspace_root / WIKI_ROOT / "log.md",
        "# Omni Wiki Log\n\n"
        "Append-only operation log for ingest, query, lint, and apply events.\n",
    )
    # AGENTS.md is canonical schema — overwrite if stale (older than current version).
    schema_path = workspace_root / WIKI_ROOT / "AGENTS.md"
    if not schema_path.exists() or _schema_is_stale(schema_path):
        schema_path.write_text(WIKI_SCHEMA_BODY, encoding="utf-8")

    # Per-domain sub-schemas — 12 folders under vault/wiki/domains/.
    from .domain_schemas import materialise_all
    domain_actions = materialise_all(workspace_root / WIKI_ROOT / "domains")

    state = status(workspace_root)
    state["domain_schemas"] = domain_actions
    return state


def _schema_is_stale(path: Path) -> bool:
    """True if AGENTS.md is older than the current WIKI_SCHEMA_VERSION marker."""
    try:
        head = path.read_text(encoding="utf-8")[:400]
    except OSError:
        return True
    return f"schema_version: {WIKI_SCHEMA_VERSION}" not in head


def status(workspace: Path | str = ".") -> dict[str, object]:
    workspace_root = Path(workspace).resolve()
    wiki = workspace_root / WIKI_ROOT
    raw = workspace_root / RAW_ROOT
    evidence = workspace_root / EVIDENCE_ROOT
    index = wiki / "index.md"
    log = wiki / "log.md"
    schema = wiki / "AGENTS.md"
    return {
        "raw": {"path": str(raw), "exists": raw.is_dir()},
        "evidence": {"path": str(evidence), "exists": evidence.is_dir()},
        "wiki": {
            "path": str(wiki),
            "exists": wiki.is_dir(),
            "ready": wiki.is_dir() and index.exists() and log.exists() and schema.exists(),
            "index_path": str(index),
            "log_path": str(log),
            "schema_path": str(schema),
            "page_count": _count_wiki_pages(wiki),
        },
        "claims": {
            "path": str(workspace_root / CLAIM_LEDGER_PATH),
            "count": _count_jsonl(workspace_root / CLAIM_LEDGER_PATH),
        },
    }


def search_wiki(
    query: str,
    *,
    workspace: Path | str = ".",
    limit: int = 10,
    include_closed: bool = False,
    now: datetime | None = None,
    backend: str = "auto",
) -> list[WikiSearchResult]:
    """Search the compiled wiki.

    Default filters (bitemporal + state hygiene):

    * Skip pages whose ``review_state`` is ``rejected`` or ``superseded``.
    * Skip pages whose ``t_valid_to`` is set and lies in the past
      (Graphiti closed window).
    * Skip page-type ``_schema`` (domain sub-schemas — they're not content).

    Pass ``include_closed=True`` to disable the bitemporal/state filter.

    ``backend`` selects the index implementation:

    * ``"auto"`` (default) — use FTS5 when the local sqlite3 build supports
      it AND the FTS index has any rows; fall back to substring otherwise.
    * ``"fts5"`` — force the FTS5 path; raises if unsupported.
    * ``"substring"`` — force the legacy substring scoring path
      (kept for parity / tests / very small wikis).
    """

    workspace_root = Path(workspace).resolve()
    wiki_root = workspace_root / WIKI_ROOT
    normalized = query.strip()
    if not normalized or not wiki_root.exists():
        return []
    now = now or datetime.now(UTC)

    backend = backend.strip().lower()
    if backend not in {"auto", "fts5", "substring"}:
        raise ValueError(f"unknown search backend {backend!r}")

    # Decide which path to take.
    if backend in {"auto", "fts5"}:
        from .wiki_fts import WikiFTSIndex, fts5_available
        if not fts5_available():
            if backend == "fts5":
                raise RuntimeError(
                    "backend='fts5' requested but the local sqlite3 build "
                    "lacks FTS5 support."
                )
        else:
            index = WikiFTSIndex(workspace_root)
            if backend == "fts5" or index.stats().get("indexed", 0) > 0:
                hits = index.search(
                    normalized, limit=limit, include_closed=include_closed, now=now,
                )
                return [
                    WikiSearchResult(
                        path=h.path,
                        title=h.title,
                        snippet=h.snippet,
                        score=h.score,
                        frontmatter=dict(h.frontmatter),
                    )
                    for h in hits
                ]

    # Substring fallback (legacy path).
    return _substring_search_wiki(
        normalized,
        workspace_root=workspace_root,
        wiki_root=wiki_root,
        limit=limit,
        include_closed=include_closed,
        now=now,
    )


def _substring_search_wiki(
    query: str,
    *,
    workspace_root: Path,
    wiki_root: Path,
    limit: int,
    include_closed: bool,
    now: datetime,
) -> list[WikiSearchResult]:
    """Linear scan over vault/wiki/*.md.  Used when FTS5 is unavailable or
    the FTS index is still empty (fresh workspace, first ingest)."""

    terms = _query_terms(query)
    # Local import to dodge circular (wiki_lint imports knowledge_plane).
    from .wiki_lint import _parse_frontmatter

    results: list[WikiSearchResult] = []
    for path in sorted(wiki_root.rglob("*.md")):
        if path.name in {"AGENTS.md", "index.md", "log.md", "_schema.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        frontmatter = _parse_frontmatter(text)

        if not include_closed and _is_closed_page(frontmatter, now=now):
            continue

        relative = str(path.relative_to(workspace_root))
        score = _score_text(f"{relative}\n{text}", terms)
        if score <= 0:
            continue
        results.append(
            WikiSearchResult(
                path=relative,
                title=_markdown_title(text) or path.stem.replace("-", " ").title(),
                snippet=_snippet(text, terms),
                score=score,
                frontmatter=dict(frontmatter),
            )
        )
    results.sort(key=lambda item: (-item.score, item.path))
    return results[: max(limit, 0)]


def reindex_wiki(workspace: Path | str = ".") -> dict[str, int]:
    """Drop + rebuild the FTS5 index from every page under vault/wiki/.

    Returns ``{"indexed": N, "skipped": M, "fts5": True|False}``.  When the
    local sqlite3 build lacks FTS5, returns ``{"fts5": False}`` and does
    no work; callers stay on the substring path automatically.
    """

    from .wiki_fts import WikiFTSIndex, fts5_available

    if not fts5_available():
        return {"fts5": False}
    index = WikiFTSIndex(Path(workspace).resolve())
    stats = index.rebuild_all()
    stats["fts5"] = True
    return stats


def _is_closed_page(frontmatter: dict[str, object], *, now: datetime) -> bool:
    """Page is "closed" (skipped by default search / context-pack) when
    review_state ∈ rejected/superseded/proposed OR t_valid_to is in the past.

    P0.2: ``proposed`` is closed-by-default so an un-applied draft never leaks
    into downstream consumption.  ``apply_wiki_proposal`` rewrites an applied
    page to ``approved``, so correctly-landed pages stay visible."""

    state = str(frontmatter.get("review_state", "")).strip().lower()
    if state in {"rejected", "superseded", "proposed"}:
        return True
    t_valid_to = frontmatter.get("t_valid_to")
    if t_valid_to is None:
        return False
    if isinstance(t_valid_to, str) and t_valid_to.strip().lower() in {"", "null", "none"}:
        return False
    try:
        parsed = datetime.fromisoformat(str(t_valid_to))
    except ValueError:
        return False
    return parsed < now


def propose_research_wiki_update(
    workspace: Path | str = ".",
    *,
    source_id: str,
    analysis_path: str,
    target_domain: str = "research",
) -> dict[str, object]:
    workspace_root = Path(workspace).resolve()
    init_layout(workspace_root)

    entry = _research_entry_by_path(workspace_root, source_id, analysis_path)
    analysis = read_analysis(
        analysis_path,
        workspace=workspace_root,
        source_id=source_id,
        max_chars=8000,
    )

    title = str(entry.get("title") or _markdown_title(str(analysis["body"])) or "Research Note")
    target_path = _wiki_target_path(title, target_domain)
    claims = _claims_from_research_entry(
        entry,
        source_id=source_id,
        analysis_path=analysis_path,
        domain=target_domain,
    )
    body = _render_research_wiki_page(
        title=title,
        source_id=source_id,
        analysis_path=analysis_path,
        domain=target_domain,
        entry=entry,
        analysis_body=str(analysis["body"]),
        claims=claims,
    )
    summary = str(entry.get("core_operator") or entry.get("primary_logic") or _snippet(str(analysis["body"]), []))

    proposal = Proposal(
        kind="wiki_update",
        state=PENDING,
        title=title,
        summary=summary[:500],
        source_path=analysis_path,
        confidence=0.72,
        suggested_action="review_and_apply_wiki_patch",
        payload={
            "target_path": target_path,
            "domain": target_domain,
            "body": body,
            "claims": claims,
            "source": {
                "source_id": source_id,
                "analysis_path": analysis_path,
                "paper_link": entry.get("paper_link", ""),
            },
        },
    )
    paths = ProposalStore(workspace_root).store(proposal, write_card=False)
    return {"proposal": proposal, **paths}


def claim_ledger_version(workspace: Path | str = ".") -> int:
    """Return the current ClaimLedger version (line count).

    Used as the optimistic-concurrency token: read at the start of a
    mutation, pass back to the mutator as ``expected_version``.  A
    mismatch on append means another writer landed first (single-user
    still hits this with concurrent ``omni-hub worker --lane`` invocations).
    """

    workspace_root = Path(workspace).resolve()
    ledger = workspace_root / CLAIM_LEDGER_PATH
    if not ledger.exists():
        return 0
    return sum(1 for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip())


def preview_apply_wiki_proposal(
    workspace: Path | str = ".",
    proposal_id: str = "",
    *,
    trace_id: str = "",
) -> "ProjectionDiff":
    """v0.18-A: return what apply_wiki_proposal WOULD write, without writing.

    Materialises a ProjectionDiff so policy / Proposal review / MCP
    clients can see concrete impact on wiki / claims / preference / fts5.
    Reads only — never mutates.
    """

    from .models import ProjectionChange, ProjectionDiff

    workspace_root = Path(workspace).resolve()
    proposal = ProposalStore(workspace_root).load(proposal_id)

    diff = ProjectionDiff(
        command_name="wiki_apply_proposal",
        trace_id=trace_id,
    )

    if proposal.kind != "wiki_update":
        diff.add(ProjectionChange(
            projection_name="(no-op)", op="modify",
            target=proposal_id,
            detail={"reason": f"kind={proposal.kind!r} is not wiki_update — preview would refuse"},
        ))
        return diff

    target_path = str(proposal.payload.get("target_path", "")).strip()
    body = str(proposal.payload.get("body", ""))
    claims = list(proposal.payload.get("claims", []))
    domain = str(proposal.payload.get("domain", "") or "general")

    # Wiki page write
    target_exists = bool(target_path) and (workspace_root / target_path).exists()
    diff.add(ProjectionChange(
        projection_name="wiki",
        op="modify" if target_exists else "add",
        target=target_path,
        detail={"bytes": len(body.encode("utf-8")), "domain": domain},
    ))
    diff.affected_size_bytes += len(body.encode("utf-8"))

    # Claim ledger appends
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        cid = str(claim.get("claim_id", ""))
        if not cid:
            continue
        diff.add(ProjectionChange(
            projection_name="claims",
            op="add",
            target=cid,
            after={"statement": str(claim.get("statement", ""))[:200], "domain": domain},
        ))

    # Preference jsonl append (v0.15-B auto-feed)
    diff.add(ProjectionChange(
        projection_name="preference",
        op="add",
        target=f".omni/preference/{domain}.jsonl",
        detail={"decision": "accepted", "domain": domain},
    ))

    # FTS5 incremental reindex
    diff.add(ProjectionChange(
        projection_name="fts5",
        op="add" if not target_exists else "modify",
        target=target_path,
    ))

    # log.md append (always)
    diff.add(ProjectionChange(
        projection_name="log_md",
        op="add",
        target="vault/wiki/log.md",
        detail={"event_kind": "apply", "title": proposal.title[:80]},
    ))

    # index.md upsert
    diff.add(ProjectionChange(
        projection_name="index_md",
        op="add",
        target=target_path,
    ))

    return diff


def preview_supersede_claim(
    workspace: Path | str = ".",
    *,
    new_claim_id: str,
    old_claim_id: str,
    trace_id: str = "",
) -> "ProjectionDiff":
    """v0.18-A: preview wiki-supersede (Graphiti bitemporal close)."""

    from .models import ProjectionChange, ProjectionDiff

    workspace_root = Path(workspace).resolve()
    diff = ProjectionDiff(command_name="wiki_supersede", trace_id=trace_id)

    if new_claim_id == old_claim_id:
        diff.add(ProjectionChange(
            projection_name="(no-op)", op="modify", target=new_claim_id,
            detail={"reason": "new and old are identical — preview would refuse"},
        ))
        return diff

    ledger_claims = _load_claims_jsonl(workspace_root)
    by_id = {str(c.get("claim_id", "")): c for c in ledger_claims if c.get("claim_id")}
    if new_claim_id not in by_id:
        diff.add(ProjectionChange(
            projection_name="(error)", op="modify", target=new_claim_id,
            detail={"reason": f"new_claim_id {new_claim_id!r} not in ledger"},
        ))
        return diff
    if old_claim_id not in by_id:
        diff.add(ProjectionChange(
            projection_name="(error)", op="modify", target=old_claim_id,
            detail={"reason": f"old_claim_id {old_claim_id!r} not in ledger"},
        ))
        return diff

    old_claim = by_id[old_claim_id]
    old_target = str(old_claim.get("target_path", "")).strip()

    diff.add(ProjectionChange(
        projection_name="claims", op="modify", target=old_claim_id,
        before={"t_valid_to": old_claim.get("t_valid_to"),
                "review_state": old_claim.get("review_state")},
        after={"t_valid_to": "<now>", "review_state": "superseded",
               "superseded_by": new_claim_id},
    ))
    diff.add(ProjectionChange(
        projection_name="claims", op="modify", target=new_claim_id,
        detail={"supersedes_append": old_claim_id},
    ))
    diff.add(ProjectionChange(
        projection_name="log_md", op="add", target="vault/wiki/log.md",
        detail={"event_kind": "supersede"},
    ))
    if old_target:
        diff.add(ProjectionChange(
            projection_name="index_md", op="remove", target=old_target,
        ))
    return diff


def _set_frontmatter_review_state(body: str, state: str) -> str:
    """Set the YAML frontmatter ``review_state`` to ``state`` (first match)."""

    return re.sub(r"(?m)^review_state:.*$", f"review_state: {state}", body, count=1)


def apply_wiki_proposal(
    workspace: Path | str = ".",
    proposal_id: str = "",
    *,
    trace_id: str = "",
) -> dict[str, object]:
    workspace_root = Path(workspace).resolve()
    init_layout(workspace_root)
    proposal = ProposalStore(workspace_root).load(proposal_id)
    if proposal.kind != "wiki_update":
        raise ValueError(f"apply_wiki_proposal only handles kind='wiki_update'; got {proposal.kind!r}")
    if proposal.state != APPROVED:
        raise ValueError("wiki update proposal must be approved before apply")

    target_path = str(proposal.payload["target_path"])
    target = safe_workspace_path(workspace_root, target_path)
    wiki_root = (workspace_root / WIKI_ROOT).resolve()
    try:
        target.relative_to(wiki_root)
    except ValueError as exc:
        raise PermissionError("wiki update target must stay inside vault/wiki") from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    body = str(proposal.payload["body"])
    # P0.2: an applied page is APPROVED by definition.  The synthesis body was
    # emitted with `review_state: proposed` at ingest time; rewrite it so the
    # approved-only search / context-pack gate consumes it — and any
    # un-applied `proposed` page stays hidden by default.
    body = _set_frontmatter_review_state(body, "approved")
    target.write_text(body.rstrip() + "\n", encoding="utf-8")
    append_log(workspace_root, op="apply", summary=proposal.title, source=proposal.source_path)
    _upsert_index_entry(workspace_root, target.relative_to(workspace_root), proposal.title, proposal.summary)
    claims_written = _append_claims(
        workspace_root,
        list(proposal.payload.get("claims", [])),
        proposal_id=proposal.proposal_id,
        target_path=str(target.relative_to(workspace_root)),
    )

    # Feed the DSPy/GEPA flywheel without depending on Argilla: an approved
    # wiki_update is by definition a positive demonstration of the
    # synthesis-page schema, so record it as an accepted PreferenceRecord.
    # Failures here are non-fatal — the apply step already succeeded.
    preference_path = ""
    try:
        preference_path = _record_wiki_preference(
            workspace_root, proposal=proposal, body=body, claims_written=claims_written,
        )
    except Exception:                                           # noqa: BLE001
        # Preference flywheel is opportunistic; never block apply.
        preference_path = ""

    # Incremental FTS5 reindex of the new page (non-fatal: substring
    # fallback still works when FTS5 isn't available).
    fts5_indexed = False
    try:
        fts5_indexed = _reindex_fts_for(workspace_root, target)
    except Exception:                                           # noqa: BLE001
        fts5_indexed = False

    return {
        "proposal_id": proposal.proposal_id,
        "target_path": str(target.relative_to(workspace_root)),
        "claims_written": claims_written,
        "log_path": f"{WIKI_ROOT}/log.md",
        "preference_path": preference_path,
        "fts5_indexed": fts5_indexed,
        "trace_id": trace_id,
        "new_ledger_version": claim_ledger_version(workspace_root),
    }


def _reindex_fts_for(workspace: Path, page_path: Path) -> bool:
    """Re-index a single page in the FTS5 sidecar.  Returns True when the
    page lands in the index; False when FTS5 is unavailable or the path
    isn't under vault/wiki/."""

    from .wiki_fts import WikiFTSIndex, fts5_available
    if not fts5_available():
        return False
    return WikiFTSIndex(workspace).rebuild_one(page_path)


def _record_wiki_preference(
    workspace: Path,
    *,
    proposal: Proposal,
    body: str,
    claims_written: int,
) -> str:
    """Append a PreferenceRecord(decision='accepted') for an approved wiki_update.

    Closes the DSPy/GEPA loop locally — Argilla is the upstream UI, but a
    local-user approve already counts as a positive demonstration.  Stored
    under ``.omni/preference/<domain>.jsonl`` (one file per domain).
    """

    from .harness.preference import PreferenceRecord, PreferenceStore

    payload = proposal.payload
    domain = str(payload.get("domain", "") or "general")
    store = PreferenceStore(workspace / ".omni" / "preference")
    record = PreferenceRecord(
        task_id=proposal.source_task_id or proposal.proposal_id,
        domain=domain,
        prompt_version=str(payload.get("prompt_version", "v0")),
        candidate_text=body,
        decision="accepted",
        accepted_spans=[body] if body else [],
        rejected_spans=[],
        reason=f"wiki_update applied; {claims_written} claim(s) recorded",
        reviewer=proposal.decided_by or "local-user",
        judge_summary={
            "claims_written": float(claims_written),
            "confidence": float(proposal.confidence),
        },
    )
    return str(store.append(record).relative_to(workspace))


def ingest_retrieval_evidence(
    workspace: Path | str = ".",
    *,
    run_id: str,
    domain: str | None = None,
    title: str = "",
    max_records: int = 20,
) -> dict[str, object]:
    """Bridge a retrieval cascade run into the wiki Ingest pipeline.

    Reads ``.omni/retrieval/<run_id>/{run_manifest.json,evidence.jsonl}``,
    writes one normalised file per record under ``vault/evidence/<domain>/``,
    and emits a single ``Proposal(kind="wiki_update")`` whose ``payload``
    carries a synthesis page body + N candidate claims. Approve via
    ``propose-approve`` then materialise via ``wiki-apply-proposal``.

    This is the Karpathy ``Ingest`` operation. It is the only path by which
    raw retrieval output becomes wiki content; direct writes to ``vault/wiki``
    are forbidden by the schema.
    """

    workspace_root = Path(workspace).resolve()
    init_layout(workspace_root)

    run_dir = safe_workspace_path(workspace_root, f"{RETRIEVAL_RUN_ROOT}/{run_id}")
    manifest_path = run_dir / "run_manifest.json"
    evidence_path = run_dir / "evidence.jsonl"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no retrieval run manifest at {manifest_path}")
    if not evidence_path.exists():
        raise FileNotFoundError(f"no evidence file at {evidence_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    query = str(manifest.get("query", "")).strip()
    resolved_domain = (domain or str(manifest.get("domain", "default"))).strip() or "default"
    resolved_title = (title or query or f"ingest-{run_id}").strip()

    records: list[dict[str, object]] = []
    for line in evidence_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(records) >= max_records:
            break

    evidence_files = _write_evidence_files(workspace_root, resolved_domain, run_id, records)

    target_path = _synthesis_target_path(resolved_title, run_id)
    claims = _claims_from_retrieval_records(
        records,
        domain=resolved_domain,
        query=query,
    )
    body = _render_synthesis_page(
        title=resolved_title,
        run_id=run_id,
        query=query,
        domain=resolved_domain,
        manifest=manifest,
        records=records,
        evidence_files=evidence_files,
        claims=claims,
    )
    summary = (
        f"retrieval ingest: {len(records)} records / {len(claims)} claim candidates "
        f"from run {run_id} [{resolved_domain}]"
    )

    proposal = Proposal(
        kind="wiki_update",
        state=PENDING,
        title=resolved_title,
        summary=summary[:500],
        source_path=f"{RETRIEVAL_RUN_ROOT}/{run_id}",
        confidence=0.55,
        suggested_action="review_and_apply_wiki_patch",
        payload={
            "target_path": target_path,
            "domain": resolved_domain,
            "body": body,
            "claims": claims,
            "ingest": {
                "run_id": run_id,
                "query": query,
                "record_count": len(records),
                "evidence_files": evidence_files,
                "fusion": manifest.get("fusion", ""),
                "sources_succeeded": list(manifest.get("sources_succeeded", [])),
            },
        },
    )
    paths = ProposalStore(workspace_root).store(proposal, write_card=False)

    append_log(
        workspace_root,
        op="ingest",
        summary=summary,
        source=f"{RETRIEVAL_RUN_ROOT}/{run_id}",
    )

    return {
        "proposal_id": proposal.proposal_id,
        "proposal": proposal,
        "run_id": run_id,
        "domain": resolved_domain,
        "target_path": target_path,
        "record_count": len(records),
        "claim_count": len(claims),
        "evidence_files": evidence_files,
        **paths,
    }


def list_claims(
    workspace: Path | str = ".",
    *,
    state: str | None = None,
    domain: str | None = None,
    include_closed: bool = False,
    limit: int = 50,
) -> list[dict[str, object]]:
    """List claims from ``.omni/claims.jsonl`` with optional filters.

    Default behaviour matches ``wiki-search``: filter out closed claims
    (those with ``t_valid_to`` set, plus rejected/superseded).  Pass
    ``include_closed=True`` for the full audit view.
    """

    workspace_root = Path(workspace).resolve()
    claims = _load_claims_jsonl(workspace_root)
    state_filter = state.strip().lower() if state else None
    domain_filter = domain.strip() if domain else None
    out: list[dict[str, object]] = []
    for claim in claims:
        if not include_closed and _is_closed_claim(claim):
            continue
        if state_filter and str(claim.get("review_state", "")).lower() != state_filter:
            continue
        if domain_filter and str(claim.get("domain", "")) != domain_filter:
            continue
        out.append(claim)
        if len(out) >= max(limit, 0):
            break
    return out


def show_claim(
    workspace: Path | str = ".",
    *,
    claim_id: str,
) -> dict[str, object]:
    """Show a single claim plus its supersession chain.

    Returns ``{claim: ..., supersedes_chain: [...], superseded_chain: [...]}``
    where the chains are ordered oldest-to-newest.
    """

    workspace_root = Path(workspace).resolve()
    claims = _load_claims_jsonl(workspace_root)
    index = {str(c.get("claim_id", "")): c for c in claims if c.get("claim_id")}
    if claim_id not in index:
        raise KeyError(f"claim_id not found: {claim_id}")
    target = index[claim_id]

    # Walk backwards: claims this one supersedes, recursively.
    supersedes_chain: list[dict[str, object]] = []
    visited: set[str] = {claim_id}
    queue = [str(cid) for cid in target.get("supersedes", []) or []]
    while queue:
        cid = queue.pop()
        if cid in visited or cid not in index:
            continue
        visited.add(cid)
        record = index[cid]
        supersedes_chain.append(record)
        queue.extend(str(x) for x in record.get("supersedes", []) or [])

    # Walk forward: claims that supersede this one (follow superseded_by hops).
    superseded_chain: list[dict[str, object]] = []
    cursor = str(target.get("superseded_by") or "")
    seen: set[str] = set()
    while cursor and cursor in index and cursor not in seen:
        seen.add(cursor)
        record = index[cursor]
        superseded_chain.append(record)
        cursor = str(record.get("superseded_by") or "")

    return {
        "claim": target,
        "supersedes_chain": supersedes_chain,
        "superseded_chain": superseded_chain,
    }


def claims_stats(workspace: Path | str = ".") -> dict[str, object]:
    """Aggregate claim counts: total, by state, by domain, closed count."""

    workspace_root = Path(workspace).resolve()
    claims = _load_claims_jsonl(workspace_root)
    by_state: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    closed = 0
    for claim in claims:
        state = str(claim.get("review_state", "")) or "unknown"
        by_state[state] = by_state.get(state, 0) + 1
        domain = str(claim.get("domain", "")) or "unknown"
        by_domain[domain] = by_domain.get(domain, 0) + 1
        if _is_closed_claim(claim):
            closed += 1
    return {
        "total": len(claims),
        "open": len(claims) - closed,
        "closed": closed,
        "by_state": dict(sorted(by_state.items())),
        "by_domain": dict(sorted(by_domain.items())),
    }


def _load_claims_jsonl(workspace: Path) -> list[dict[str, object]]:
    ledger = workspace / CLAIM_LEDGER_PATH
    if not ledger.exists():
        return []
    out: list[dict[str, object]] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _is_closed_claim(claim: dict[str, object]) -> bool:
    state = str(claim.get("review_state", "")).strip().lower()
    if state in {"rejected", "superseded"}:
        return True
    t_valid_to = claim.get("t_valid_to")
    if t_valid_to is None:
        return False
    if isinstance(t_valid_to, str) and t_valid_to.strip().lower() in {"", "null", "none"}:
        return False
    return True


def supersede_claim(
    workspace: Path | str = ".",
    *,
    new_claim_id: str,
    old_claim_id: str,
    reason: str = "",
    expected_version: int | None = None,
    trace_id: str = "",
) -> dict[str, object]:
    """Close the old claim's validity window and link the new claim's
    ``supersedes`` chain.  Bitemporal — old claim is NOT deleted, only
    closed by setting ``t_valid_to`` (Graphiti / Zep pattern).

    Atomically rewrites ``.omni/claims.jsonl`` via temp file + rename.
    Appends a ``supersede`` entry to ``vault/wiki/log.md``.

    v0.18-E: when ``expected_version`` is provided, the rewrite is gated
    on the ledger row count matching the caller's read snapshot.  A
    mismatch raises ``ConcurrentModificationError`` (Event Sourcing
    optimistic-concurrency pattern).  Pass ``None`` to skip the check
    (legacy callers).
    """

    from .models import ConcurrentModificationError

    if new_claim_id == old_claim_id:
        raise ValueError("new_claim_id and old_claim_id must differ")

    workspace_root = Path(workspace).resolve()
    ledger = workspace_root / CLAIM_LEDGER_PATH
    if not ledger.exists():
        raise FileNotFoundError(f"claim ledger not found at {ledger}")

    raw_lines = ledger.read_text(encoding="utf-8").splitlines()
    actual_version = sum(1 for line in raw_lines if line.strip())
    if expected_version is not None and actual_version != expected_version:
        raise ConcurrentModificationError(
            f"ClaimLedger expected_version={expected_version} but actual={actual_version}; "
            "re-read and retry"
        )

    claims: list[dict[str, object]] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            claims.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    new_claim: dict[str, object] | None = None
    old_claim: dict[str, object] | None = None
    for claim in claims:
        cid = str(claim.get("claim_id", ""))
        if cid == new_claim_id:
            new_claim = claim
        elif cid == old_claim_id:
            old_claim = claim
    if new_claim is None:
        raise KeyError(f"new claim_id not found: {new_claim_id}")
    if old_claim is None:
        raise KeyError(f"old claim_id not found: {old_claim_id}")

    timestamp = _utcnow()
    old_claim["t_valid_to"] = timestamp
    old_claim["superseded_by"] = new_claim_id
    old_existing_state = str(old_claim.get("review_state", ""))
    if old_existing_state != "rejected":
        old_claim["review_state"] = "superseded"
    old_claim["updated_at"] = timestamp

    existing_supersedes = list(new_claim.get("supersedes", []) or [])
    if old_claim_id not in existing_supersedes:
        existing_supersedes.append(old_claim_id)
    new_claim["supersedes"] = existing_supersedes
    new_claim["updated_at"] = timestamp

    # Atomic rewrite — temp file + os.replace.
    tmp = ledger.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for claim in claims:
            handle.write(json.dumps(claim, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(ledger)

    append_log(
        workspace_root,
        op="supersede",
        summary=f"{new_claim_id} supersedes {old_claim_id}"
                + (f" — {reason}" if reason else ""),
        source=f"claim:{old_claim_id}",
    )

    # v0.17-G: strip the old claim's page from index.md (if it had one).
    # No-op when the claim is a free-floating fact with no target_path.
    index_pruned = False
    old_target = str(old_claim.get("target_path", "")).strip()
    if old_target:
        try:
            index_pruned = _remove_index_entry(workspace_root, old_target)
        except Exception:                                       # noqa: BLE001
            index_pruned = False

    return {
        "new_claim_id": new_claim_id,
        "old_claim_id": old_claim_id,
        "t_valid_to": timestamp,
        "reason": reason,
        "ledger_path": str(ledger.relative_to(workspace_root)),
        "index_pruned": index_pruned,
        "trace_id": trace_id,
        "new_ledger_version": claim_ledger_version(workspace_root),
    }


def resolve_conflict(
    workspace: Path | str = ".",
    *,
    proposal_id: str,
    decision: str,
    new_claim_id: str = "",
    old_claim_id: str = "",
    reason: str = "",
    expected_version: int | None = None,
    trace_id: str = "",
) -> dict[str, object]:
    """Apply a resolution to a ``lint_finding`` proposal of rule
    ``contradiction``.  Four decisions:

    * ``keep_both``    — both claims kept, both marked ``review_state=conflict``.
    * ``reject_old``   — older claim (lower t_valid_from) set ``review_state=rejected``.
    * ``reject_new``   — newer claim set ``review_state=rejected``.
    * ``supersede``    — newer claim supersedes older claim (bitemporal close).

    The proposal itself is then approved with the decision recorded as the
    approve reason; the lint pass on the next run will see the cleared
    state.
    """

    workspace_root = Path(workspace).resolve()
    store = ProposalStore(workspace_root)
    proposal = store.load(proposal_id)
    if proposal.kind != "lint_finding":
        raise ValueError(
            f"resolve_conflict only handles kind='lint_finding'; got {proposal.kind!r}"
        )
    payload = dict(proposal.payload or {})
    if payload.get("rule") != "contradiction":
        raise ValueError(
            f"resolve_conflict only handles contradiction findings; got rule={payload.get('rule')!r}"
        )

    affected = list(payload.get("affected_claim_ids", []))
    if len(affected) != 2:
        raise ValueError(
            f"contradiction finding must reference exactly 2 claim_ids; got {affected}"
        )

    resolved_new, resolved_old = _order_pair_by_time(workspace_root, affected, new_claim_id, old_claim_id)

    decision = decision.strip().lower()
    output: dict[str, object] = {
        "proposal_id": proposal_id,
        "decision": decision,
        "new_claim_id": resolved_new,
        "old_claim_id": resolved_old,
    }

    if decision == "keep_both":
        _mark_claim_state(workspace_root, resolved_new, state="conflict")
        _mark_claim_state(workspace_root, resolved_old, state="conflict")
    elif decision == "reject_old":
        _mark_claim_state(workspace_root, resolved_old, state="rejected")
        # Prune the rejected claim's page from index.md (if any).
        _prune_index_for_claim(workspace_root, resolved_old)
    elif decision == "reject_new":
        _mark_claim_state(workspace_root, resolved_new, state="rejected")
        _prune_index_for_claim(workspace_root, resolved_new)
    elif decision == "supersede":
        supersede_claim(
            workspace_root,
            new_claim_id=resolved_new,
            old_claim_id=resolved_old,
            reason=reason or f"conflict-resolve via {proposal_id}",
            expected_version=expected_version,
            trace_id=trace_id,
        )
    else:
        raise ValueError(
            f"unknown decision {decision!r}; expected keep_both|reject_old|reject_new|supersede"
        )

    store.approve(proposal_id, reason=f"resolved={decision}: {reason}", decided_by="local-user")
    append_log(
        workspace_root,
        op="conflict-resolve",
        summary=f"{decision}: {resolved_new} vs {resolved_old}"
                + (f" — {reason}" if reason else ""),
        source=f"proposal:{proposal_id}",
    )
    return output


def _order_pair_by_time(
    workspace: Path,
    affected: list[str],
    explicit_new: str,
    explicit_old: str,
) -> tuple[str, str]:
    """Decide which of the two affected claim_ids is "new" and which "old".

    Explicit overrides win when both are supplied; otherwise we sort by
    ``t_valid_from`` (newer first).  Falls back to the order found in the
    proposal's ``affected_claim_ids`` list (which is sorted by claim_id
    text in the lint module).
    """

    if explicit_new and explicit_old:
        if {explicit_new, explicit_old} != set(affected):
            raise ValueError(
                "explicit new/old claim_ids must match the affected pair "
                f"{affected}"
            )
        return explicit_new, explicit_old

    ledger = workspace / CLAIM_LEDGER_PATH
    times: dict[str, str] = {}
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = str(record.get("claim_id", ""))
            if cid in affected:
                times[cid] = str(record.get("t_valid_from") or record.get("updated_at") or "")

    ordered = sorted(affected, key=lambda cid: times.get(cid, ""), reverse=True)
    return ordered[0], ordered[1]


def _prune_index_for_claim(workspace: Path, claim_id: str) -> bool:
    """Look up a claim's target_path and strip it from index.md."""

    ledger = workspace / CLAIM_LEDGER_PATH
    if not ledger.exists():
        return False
    target = ""
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(record.get("claim_id", "")) == claim_id:
            target = str(record.get("target_path", "")).strip()
            break
    if not target:
        return False
    try:
        return _remove_index_entry(workspace, target)
    except Exception:                                           # noqa: BLE001
        return False


def _mark_claim_state(workspace: Path, claim_id: str, *, state: str) -> None:
    ledger = workspace / CLAIM_LEDGER_PATH
    if not ledger.exists():
        raise FileNotFoundError(f"claim ledger not found at {ledger}")
    timestamp = _utcnow()
    found = False
    claims: list[dict[str, object]] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(record.get("claim_id", "")) == claim_id:
            record["review_state"] = state
            record["updated_at"] = timestamp
            found = True
        claims.append(record)
    if not found:
        raise KeyError(f"claim_id not found: {claim_id}")
    tmp = ledger.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for record in claims:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(ledger)


def build_context_pack(
    workspace: Path | str = ".",
    *,
    query: str,
    domain: str = "research",
    wiki_limit: int = 6,
    research_limit: int = 6,
    persist: bool = False,
    tier: str = "standard",
    include_closed: bool = False,
) -> ContextPack:
    """Build a context pack with Karpathy / Anthropic Skills progressive
    disclosure: ``minimal`` (frontmatter only, ~1k tok), ``standard``
    (frontmatter + snippet/abstract per result, ~5k tok), ``expanded``
    (full bodies up to ~30k tok).
    """

    tier = tier.strip().lower()
    if tier not in CONTEXT_PACK_TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {CONTEXT_PACK_TIERS}")
    workspace_root = Path(workspace).resolve()

    wiki_results = search_wiki(
        query,
        workspace=workspace_root,
        limit=wiki_limit,
        include_closed=include_closed,
    )
    research_results = [
        result.to_dict()
        for result in search_research_assets(
            query,
            workspace=workspace_root,
            source_id="all",
            limit=research_limit,
        )
    ]

    budget = _TIER_BUDGETS[tier]
    wiki_results = _apply_disclosure_tier(workspace_root, wiki_results, tier=tier, budget=budget)
    research_results = _apply_research_disclosure_tier(research_results, tier=tier, budget=budget)

    total_chars = sum(len(r.snippet) + len(r.body_excerpt) for r in wiki_results)
    total_chars += sum(len(str(r.get("snippet", ""))) for r in research_results)

    pack = ContextPack(
        pack_id=_stable_id("context-pack", domain, query, tier, _utcnow(), str(uuid4())),
        query=query,
        domain=domain,
        tier=tier,
        wiki_results=wiki_results,
        research_results=research_results,
        char_budget=budget,
        total_chars=total_chars,
    )
    if persist:
        out_dir = safe_workspace_path(workspace_root, CONTEXT_PACK_ROOT)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{pack.pack_id}.json"
        pack.path = str(path)
        path.write_text(json.dumps(pack.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return pack


def _apply_disclosure_tier(
    workspace: Path,
    results: list[WikiSearchResult],
    *,
    tier: str,
    budget: int,
) -> list[WikiSearchResult]:
    """Trim or expand each wiki result according to the tier budget.

    * ``minimal``  — snippet cleared (frontmatter is the surface).
    * ``standard`` — snippet kept (~320 chars from search).
    * ``expanded`` — load body excerpt up to ``budget`` chars per result.
    """

    if tier == "minimal":
        for r in results:
            r.snippet = ""
            r.body_excerpt = ""
        return results

    if tier == "standard":
        # Search-provided snippet is already truncated; nothing to expand.
        for r in results:
            r.body_excerpt = ""
        return results

    # expanded — read body off disk up to budget.
    for r in results:
        page_path = workspace / r.path
        try:
            text = page_path.read_text(encoding="utf-8")
        except OSError:
            r.body_excerpt = ""
            continue
        # Skip frontmatter section in the body excerpt.
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end > 0:
                text = text[end + 5:]
        r.body_excerpt = text[: max(budget, 0)]
    return results


def _apply_research_disclosure_tier(
    research_results: list[dict[str, object]],
    *,
    tier: str,
    budget: int,
) -> list[dict[str, object]]:
    if tier == "minimal":
        return [
            {k: v for k, v in r.items() if k in {"source_id", "title", "analysis_path", "score"}}
            for r in research_results
        ]
    if tier == "standard":
        return research_results
    # expanded
    for r in research_results:
        snippet = str(r.get("snippet", ""))
        r["snippet"] = snippet[: max(budget, 0)] if snippet else snippet
    return research_results


def _write_if_missing(path: Path, body: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _count_wiki_pages(wiki_root: Path) -> int:
    if not wiki_root.exists():
        return 0
    return sum(1 for path in wiki_root.rglob("*.md") if path.is_file())


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _research_entry_by_path(
    workspace: Path,
    source_id: str,
    analysis_path: str,
) -> dict[str, object]:
    source = default_sources(workspace).get(source_id)
    if source is not None:
        for entry in iter_index(source):
            if str(entry.get("analysis_path", "")) == analysis_path:
                return dict(entry)
    return {"analysis_path": analysis_path}


def _wiki_target_path(title: str, domain: str) -> str:
    return f"{WIKI_ROOT}/domains/{_slugify(domain)}/{_slugify(title)}.md"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if slug:
        return slug
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _claims_from_research_entry(
    entry: dict[str, object],
    *,
    source_id: str,
    analysis_path: str,
    domain: str,
) -> list[dict[str, object]]:
    statements = [
        str(entry.get("core_operator", "")).strip(),
        str(entry.get("primary_logic", "")).strip(),
    ]
    now = _utcnow()
    claims: list[dict[str, object]] = []
    for statement in statements:
        if not statement:
            continue
        claims.append(
            {
                "claim_id": _stable_id("claim", domain, source_id, analysis_path, statement),
                "domain": domain,
                "statement": statement,
                "support": [{"source_id": source_id, "path": analysis_path}],
                "against": [],
                "confidence": 0.68,
                "uncertainty": "single-source research evidence; keep open to later conflict checks",
                "review_state": "proposed",
                "t_valid_from": now,
                "t_valid_to": None,
                "supersedes": [],
                "superseded_by": None,
            }
        )
    return claims


def _render_research_wiki_page(
    *,
    title: str,
    source_id: str,
    analysis_path: str,
    domain: str,
    entry: dict[str, object],
    analysis_body: str,
    claims: list[dict[str, object]],
) -> str:
    lines = [
        "---",
        "omni_type: compiled_wiki",
        f"domain: {domain}",
        f"source_id: {source_id}",
        f"source_path: {analysis_path}",
        f"paper_link: {entry.get('paper_link', '')}",
        "review_state: proposed",
        "---",
        "",
        f"# {title}",
        "",
        "## Source",
        "",
        f"- source_id: {source_id}",
        f"- analysis_path: {analysis_path}",
    ]
    if entry.get("paper_link"):
        lines.append(f"- paper_link: {entry['paper_link']}")
    if entry.get("venue_year"):
        lines.append(f"- venue_year: {entry['venue_year']}")

    lines.extend(["", "## Compiled Synthesis", ""])
    synthesis = str(entry.get("primary_logic") or entry.get("core_operator") or "").strip()
    lines.append(synthesis or _snippet(analysis_body, []))

    lines.extend(["", "## Claims", ""])
    if claims:
        for claim in claims:
            lines.append(f"- `{claim['claim_id']}` {claim['statement']}")
    else:
        lines.append("- No atomic claims extracted yet.")

    lines.extend(["", "## Evidence Excerpt", "", _snippet(analysis_body, [], max_chars=1200)])
    return "\n".join(lines) + "\n"


def append_log(
    workspace: Path | str,
    *,
    op: str,
    summary: str,
    source: str = "",
) -> dict[str, object]:
    """Append an audit entry to vault/wiki/log.md (Karpathy log format).

    Header pattern: ``## [YYYY-MM-DDTHH:MM:SSZ] op | summary``.
    ``op`` is one of ingest | apply | lint | supersede | conflict-resolve | manual.
    """
    workspace_root = Path(workspace).resolve()
    log_path = workspace_root / WIKI_ROOT / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = _utcnow()
    line_summary = summary.replace("\n", " ").strip()[:200]
    source_line = f"- source: {source}\n" if source else "- source: (none)\n"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## [{timestamp}] {op} | {line_summary}\n{source_line}")
    return {
        "log_path": f"{WIKI_ROOT}/log.md",
        "op": op,
        "summary": line_summary,
        "source": source,
        "timestamp": timestamp,
    }


def _upsert_index_entry(workspace: Path, relative_path: Path, title: str, summary: str) -> None:
    index_path = workspace / WIKI_ROOT / "index.md"
    marker = f"- [[{relative_path.as_posix()}|{title}]]"
    existing = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    if marker in existing:
        return
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{marker} — {summary[:180]}\n")


def _remove_index_entry(workspace: Path, relative_path: str) -> bool:
    """Strip the index.md line that points at ``relative_path``.

    Called by ``wiki-supersede`` (when the old claim's page is being
    closed) and by ``wiki-conflict-resolve`` with ``reject_old/new``.
    Returns True when at least one entry was removed.
    """

    index_path = workspace / WIKI_ROOT / "index.md"
    if not index_path.exists():
        return False
    needle = f"[[{relative_path}|"
    lines = index_path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if needle not in line]
    if len(kept) == len(lines):
        return False
    index_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return True


def _write_evidence_files(
    workspace: Path,
    domain: str,
    run_id: str,
    records: list[dict[str, object]],
) -> list[str]:
    """Persist one normalised JSON per retrieval record under vault/evidence/<domain>/.

    Each file is the durable evidence anchor that a wiki page cites.  Filename
    pattern keeps run provenance: ``<run_id>__<idx>__<canonical_hash>.json``.

    Also: when the record carries a non-empty ``snippet`` we mirror it under
    ``vault/raw/<domain>/<run_id>/<canonical_hash>.md`` so the three-layer
    lineage (raw → evidence → wiki) has actual data in raw (v0.17-B).
    """

    domain_dir = safe_workspace_path(workspace, f"{EVIDENCE_ROOT}/{_slugify(domain)}")
    domain_dir.mkdir(parents=True, exist_ok=True)
    raw_run_dir = safe_workspace_path(workspace, f"{RAW_ROOT}/{_slugify(domain)}/{run_id}")
    raw_run_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for idx, record in enumerate(records, start=1):
        canonical = str(record.get("canonical_id") or record.get("url") or f"r{idx}")
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]
        # Persist raw snippet (the durable, append-only copy of what we
        # actually saw at fetch time).
        raw_name = f"{idx:03d}__{digest}.md"
        raw_target = raw_run_dir / raw_name
        raw_hash = _record_raw_hash(record)
        license_str = _record_license(record)
        raw_body = _render_raw_capture(
            record, run_id=run_id, idx=idx, raw_hash=raw_hash, license_=license_str,
        )
        raw_target.write_text(raw_body, encoding="utf-8")
        raw_path = str(raw_target.relative_to(workspace))

        evidence_record = {
            "run_id": run_id,
            "record_idx": idx,
            "cite_id": record.get("cite_id", ""),
            "source": record.get("source", ""),
            "title": record.get("title", ""),
            "url": record.get("url", ""),
            "snippet": record.get("snippet", ""),
            "canonical_id": canonical,
            "fetched_at": record.get("fetched_at", _utcnow()),
            "score": record.get("score"),
            "raw_path": raw_path,
            # v0.46 bronze provenance: content fingerprint (tamper-evident +
            # run-independent dedup key) + best-effort source license.
            "raw_hash": raw_hash,
            "license": license_str,
            # v0.46: persist the connector's API-native metadata.  Previously
            # dropped here, which is why "the API structure is lost" — the
            # RetrievalRecord.metadata escape hatch never reached disk.  The
            # seed-script writer already kept it; this aligns the production
            # path to the same (single) evidence schema.
            "metadata": record.get("metadata", {}) or {},
        }
        file_name = f"{run_id}__{idx:03d}__{digest}.json"
        target = domain_dir / file_name
        target.write_text(
            json.dumps(evidence_record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(str(target.relative_to(workspace)))
    return written


def _record_raw_hash(record: dict[str, object]) -> str:
    """Content fingerprint of what we captured — tamper-evident + a dedup key.

    Hashes the stable fetch-time payload (title/url/snippet/canonical_id +
    the connector's API-native metadata), independent of run_id/idx so the
    same artifact fetched twice hashes identically.
    """

    payload = {
        "title": record.get("title", ""),
        "url": record.get("url", ""),
        "snippet": record.get("snippet", ""),
        "canonical_id": record.get("canonical_id", ""),
        "metadata": record.get("metadata", {}) or {},
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _record_license(record: dict[str, object]) -> str:
    """Best-effort source license / usage terms for the bronze artifact.

    Several connectors already surface it (Crossref ``license``, GitHub SPDX
    ``license``, OpenAlex OA status); capture whatever is present and leave it
    blank otherwise — honest, never invented.
    """

    meta = record.get("metadata") or {}
    lic = (
        record.get("license")
        or meta.get("license")
        or meta.get("spdx_license")
        or meta.get("oa_status")
        or ""
    )
    if isinstance(lic, dict):
        lic = lic.get("name") or lic.get("spdx_id") or lic.get("url") or ""
    if isinstance(lic, (list, tuple)):
        lic = ", ".join(str(x) for x in lic if x)
    return str(lic)


def _render_raw_capture(
    record: dict[str, object],
    *,
    run_id: str,
    idx: int,
    raw_hash: str = "",
    license_: str = "",
) -> str:
    """Render a retrieval record as raw-layer markdown.

    `vault/raw/` is append-only and intentionally lossless — we keep
    every field we know about the original source so that a later
    re-parse (different evidence pipeline) can still recover the
    provenance.
    """

    lines = [
        "---",
        f"omni_layer: raw",
        f"run_id: {run_id}",
        f"record_idx: {idx}",
        f"source: {record.get('source', '')}",
        f"url: {record.get('url', '')}",
        f"canonical_id: {record.get('canonical_id', '')}",
        f"cite_id: {record.get('cite_id', '')}",
        f"title: {json.dumps(str(record.get('title', '')), ensure_ascii=False)}",
        f"fetched_at: {record.get('fetched_at', _utcnow())}",
        f"raw_hash: {raw_hash}",
        f"license: {json.dumps(license_, ensure_ascii=False)}",
        "---",
        "",
        f"# {record.get('title') or 'Untitled retrieval record'}",
        "",
        str(record.get("snippet", "")),
        "",
    ]
    metadata = record.get("metadata") or {}
    if metadata:
        # Keep raw genuinely lossless (as the docstring promises): preserve
        # the connector's full API-native metadata so a later re-parse with
        # a different evidence pipeline can recover fields the summary layer
        # dropped.
        lines.append("<!-- omni:metadata -->")
        lines.append("```json")
        lines.append(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


# P0.3: a quality gate so the naive "first sentence of a snippet" extraction
# can't write bare titles / journal names / company names ("Constellations",
# "Annals of Oncology", "LyondellBasell Industries N.V.") into the claim
# ledger.  A real claim is a full predication — long enough AND carrying a
# verb/predicate.  Below the bar the record still lands as *evidence*; it just
# does not become a *claim* (single-source low-signal → evidence-only).
_CLAIM_MIN_CHARS = 40
# Explicit finite verb forms (NOT ``\w*`` stems) so nominalisations like
# "Optimization" / "improvement" / "reduction" — common in titles — do not
# masquerade as predicates.  Precision over recall: a real claim whose verb
# isn't listed simply stays evidence (the conservative, correct failure mode).
_CLAIM_VERBISH = re.compile(
    r"\b(is|are|was|were|be|been|being|am|"
    r"has|have|had|can|could|should|shall|will|would|may|might|must|do|does|did|"
    r"shows?|showed|improves?|improved|reduces?|reduced|increases?|increased|"
    r"achieves?|achieved|enables?|enabled|provides?|provided|presents?|presented|"
    r"introduces?|introduced|outperforms?|outperformed|requires?|required|"
    r"leads?|led|causes?|caused|finds?|found|suggests?|suggested|allows?|allowed|"
    r"trains?|trained|proposes?|proposed|demonstrates?|demonstrated|"
    r"generates?|generated|estimates?|estimated|predicts?|predicted|"
    r"solves?|solved|optimizes?|optimized|enhances?|enhanced|yields?|yielded|"
    r"evolves?|evolved|edits?|edited|reads?|adapts?|adapted|extends?|extended|"
    r"combines?|combined|evaluates?|evaluated|leverages?|leveraged|exploits?|"
    r"exploited|mitigates?|mitigated|captures?|captured|treats?|treated)\b",
    re.IGNORECASE,
)
# CJK predicate / copula markers (Chinese snippets carry no whitespace verbs).
_CLAIM_VERBISH_CJK = re.compile(
    r"(是|为|可以|能够|提出|实现|表明|显示|证明|提升|提高|降低|增加|减少|"
    r"需要|使用|导致|发现|改善|优化|生成|预测|解决|应用|包含|具有|属于)"
)


def _looks_like_claim(statement: str) -> bool:
    """True when ``statement`` reads like an assertable claim, not a bare
    title / venue / entity name."""

    s = statement.strip()
    # CJK carries far more meaning per character, so a dense Chinese sentence
    # clears the bar at a lower char count than an English one.
    cjk = sum(1 for ch in s if "一" <= ch <= "鿿")
    min_chars = 16 if cjk >= 8 else _CLAIM_MIN_CHARS
    if len(s) < min_chars:
        return False
    return bool(_CLAIM_VERBISH.search(s) or _CLAIM_VERBISH_CJK.search(s))


def _claims_from_retrieval_records(
    records: list[dict[str, object]],
    *,
    domain: str,
    query: str,
) -> list[dict[str, object]]:
    """Generate one candidate claim per record that clears the quality gate.

    Conservative: confidence 0.5 (web evidence, single-source),
    review_state=proposed, bitemporal fields populated.  Low-signal fragments
    are dropped (they remain as evidence).  Lint + human review sharpen the
    survivors later.
    """

    from .retrieval.source_policy import source_tier as _source_tier

    claims: list[dict[str, object]] = []
    seen_statements: set[str] = set()
    for record in records:
        snippet = str(record.get("snippet", "")).strip()
        if not snippet:
            continue
        statement = _first_sentence(snippet, max_chars=280)
        if not statement or statement.lower() in seen_statements:
            continue
        # P0.3 quality gate: bare titles / venue / entity fragments stay as
        # evidence only — they do not become claims.
        if not _looks_like_claim(statement):
            continue
        seen_statements.add(statement.lower())
        canonical = str(record.get("canonical_id") or record.get("url") or "")
        _meta = record.get("metadata") or {}
        claims.append(
            {
                "claim_id": _stable_id("claim", domain, query, canonical, statement),
                "domain": domain,
                "statement": statement,
                "support": [
                    {
                        "source_id": canonical,
                        "cite_id": record.get("cite_id", ""),
                        "url": record.get("url", ""),
                        "source": record.get("source", ""),
                        # v0.46 provenance carried onto the claim: cost/access
                        # tier + how the evidence was served.  Lets downstream
                        # rank/audit a claim by where it came from WITHOUT
                        # assuming a fallback/degraded source is worse.
                        "source_tier": _source_tier(str(record.get("source", ""))),
                        "served_via": _meta.get("served_via", ""),
                    }
                ],
                "against": [],
                "confidence": 0.5,
                "uncertainty": "single-source retrieval evidence; awaits cross-source confirmation",
                "review_state": "proposed",
                "t_valid_from": _utcnow(),
                "t_valid_to": None,
                "supersedes": [],
                "superseded_by": None,
            }
        )
    return claims


def _synthesis_target_path(title: str, run_id: str) -> str:
    slug = _slugify(title) or _slugify(run_id)
    return f"{WIKI_ROOT}/syntheses/{slug}.md"


def _first_sentence(text: str, *, max_chars: int = 280) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ""
    match = re.search(r"[。.!?！？]\s", compact[:max_chars + 60])
    if match:
        return compact[: match.end()].strip()
    return compact[:max_chars].strip()


def _render_synthesis_page(
    *,
    title: str,
    run_id: str,
    query: str,
    domain: str,
    manifest: dict[str, object],
    records: list[dict[str, object]],
    evidence_files: list[str],
    claims: list[dict[str, object]],
) -> str:
    source_ids = [
        str(r.get("canonical_id") or r.get("url") or f"r{idx}")
        for idx, r in enumerate(records, start=1)
    ]
    claim_id_list = [c["claim_id"] for c in claims]

    lines = [
        "---",
        "page_type: synthesis",
        f"domain: {domain}",
        f"claim_ids: {json.dumps(claim_id_list, ensure_ascii=False)}",
        f"source_ids: {json.dumps(source_ids, ensure_ascii=False)}",
        f"t_valid_from: {_utcnow()}",
        "t_valid_to: null",
        "superseded_by: null",
        "confidence: medium",
        "review_state: proposed",
        f"ingest_run_id: {run_id}",
        "---",
        "",
        f"# {title}",
        "",
        "## Question",
        "",
        query or "(no query recorded)",
        "",
        "## Sources",
        "",
    ]
    for idx, record in enumerate(records, start=1):
        cite = record.get("cite_id") or f"R{idx}"
        src = record.get("source", "")
        rec_title = str(record.get("title", "")).strip() or "(untitled)"
        url = record.get("url", "")
        lines.append(f"- [{cite}] **{rec_title}** — {src} — {url}")

    lines.extend(["", "## Compiled Findings", ""])
    if records:
        for idx, record in enumerate(records, start=1):
            cite = record.get("cite_id") or f"R{idx}"
            snippet = str(record.get("snippet", "")).strip()
            if not snippet:
                continue
            lines.append(f"- [{cite}] {snippet[:600]}")
    else:
        lines.append("- (no retrieval records — manifest may be stale)")

    lines.extend(["", "## Candidate Claims", ""])
    if claims:
        for claim in claims:
            lines.append(f"- `{claim['claim_id']}` ({claim['confidence']:.2f}) {claim['statement']}")
    else:
        lines.append("- (no candidate claims extracted)")

    lines.extend(["", "## Evidence Files", ""])
    for path in evidence_files:
        lines.append(f"- `{path}`")

    lines.extend([
        "",
        "## References",
        "",
    ])
    for idx, record in enumerate(records, start=1):
        cite = record.get("cite_id") or f"R{idx}"
        url = record.get("url", "")
        src = record.get("source", "")
        canonical = record.get("canonical_id", "")
        lines.append(f"- [{cite}] {src} · {canonical} · {url}")

    fusion = manifest.get("fusion", "")
    sources_succeeded = ", ".join(map(str, manifest.get("sources_succeeded", []) or []))
    lines.extend([
        "",
        "## Ingest Metadata",
        "",
        f"- run_id: `{run_id}`",
        f"- fusion: {fusion or '(none)'}",
        f"- sources_succeeded: {sources_succeeded or '(none)'}",
        f"- record_count: {len(records)}",
    ])
    return "\n".join(lines) + "\n"


def _append_claims(
    workspace: Path,
    claims: list[object],
    *,
    proposal_id: str,
    target_path: str,
) -> int:
    ledger = workspace / CLAIM_LEDGER_PATH
    ledger.parent.mkdir(parents=True, exist_ok=True)
    existing_ids: set[str] = set()
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                existing_ids.add(str(json.loads(line).get("claim_id", "")))
            except json.JSONDecodeError:
                continue

    written = 0
    with ledger.open("a", encoding="utf-8") as handle:
        for raw in claims:
            if not isinstance(raw, dict):
                continue
            record = dict(raw)
            claim_id = str(record.get("claim_id", ""))
            if not claim_id or claim_id in existing_ids:
                continue
            record["review_state"] = "approved"
            record["proposal_id"] = proposal_id
            record["target_path"] = target_path
            record["updated_at"] = _utcnow()
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            existing_ids.add(claim_id)
            written += 1
    return written


def _query_terms(query: str) -> list[str]:
    lowered = query.casefold()
    terms = [part for part in re.split(r"\s+", lowered) if part]
    if lowered and lowered not in terms:
        terms.insert(0, lowered)
    return terms


def _score_text(text: str, terms: list[str]) -> float:
    lowered = text.casefold()
    return sum(1.0 for term in terms if term and term in lowered)


def _markdown_title(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _snippet(text: str, terms: list[str], *, max_chars: int = 320) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ""
    lowered = compact.casefold()
    positions = [lowered.find(term) for term in terms if term and lowered.find(term) >= 0]
    start = max(min(positions) - 80, 0) if positions else 0
    return compact[start:start + max_chars]


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
