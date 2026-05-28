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

WIKI_SCHEMA_VERSION = "v0.11"

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
domain: research | engineering | finance | policy | international_relations
         | ai_progress | photography | fashion | chat_relationships
         | agent_systems | social_en | social_zh
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
    f"{WIKI_ROOT}/domains/policy",
    f"{WIKI_ROOT}/domains/international-relations",
    f"{WIKI_ROOT}/domains/ai-progress",
    f"{WIKI_ROOT}/domains/agent-systems",
)


@dataclass(slots=True)
class WikiSearchResult:
    path: str
    title: str
    snippet: str
    score: float

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["score"] = round(self.score, 4)
        return data


@dataclass(slots=True)
class ContextPack:
    pack_id: str
    query: str
    domain: str
    wiki_results: list[WikiSearchResult] = field(default_factory=list)
    research_results: list[dict[str, object]] = field(default_factory=list)
    path: str = ""
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, object]:
        return {
            "pack_id": self.pack_id,
            "query": self.query,
            "domain": self.domain,
            "wiki_results": [result.to_dict() for result in self.wiki_results],
            "research_results": self.research_results,
            "path": self.path,
            "created_at": self.created_at,
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
    return status(workspace_root)


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
) -> list[WikiSearchResult]:
    workspace_root = Path(workspace).resolve()
    wiki_root = workspace_root / WIKI_ROOT
    normalized = query.strip()
    if not normalized or not wiki_root.exists():
        return []
    terms = _query_terms(normalized)
    results: list[WikiSearchResult] = []
    for path in sorted(wiki_root.rglob("*.md")):
        if path.name in {"AGENTS.md", "index.md", "log.md"}:
            continue
        text = path.read_text(encoding="utf-8")
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
            )
        )
    results.sort(key=lambda item: (-item.score, item.path))
    return results[: max(limit, 0)]


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


def apply_wiki_proposal(
    workspace: Path | str = ".",
    proposal_id: str = "",
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
    target.write_text(body.rstrip() + "\n", encoding="utf-8")
    append_log(workspace_root, op="apply", summary=proposal.title, source=proposal.source_path)
    _upsert_index_entry(workspace_root, target.relative_to(workspace_root), proposal.title, proposal.summary)
    claims_written = _append_claims(
        workspace_root,
        list(proposal.payload.get("claims", [])),
        proposal_id=proposal.proposal_id,
        target_path=str(target.relative_to(workspace_root)),
    )

    return {
        "proposal_id": proposal.proposal_id,
        "target_path": str(target.relative_to(workspace_root)),
        "claims_written": claims_written,
        "log_path": f"{WIKI_ROOT}/log.md",
    }


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


def build_context_pack(
    workspace: Path | str = ".",
    *,
    query: str,
    domain: str = "research",
    wiki_limit: int = 6,
    research_limit: int = 6,
    persist: bool = False,
) -> ContextPack:
    workspace_root = Path(workspace).resolve()
    wiki_results = search_wiki(query, workspace=workspace_root, limit=wiki_limit)
    research_results = [
        result.to_dict()
        for result in search_research_assets(
            query,
            workspace=workspace_root,
            source_id="all",
            limit=research_limit,
        )
    ]
    pack = ContextPack(
        pack_id=_stable_id("context-pack", domain, query, _utcnow(), str(uuid4())),
        query=query,
        domain=domain,
        wiki_results=wiki_results,
        research_results=research_results,
    )
    if persist:
        out_dir = safe_workspace_path(workspace_root, CONTEXT_PACK_ROOT)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{pack.pack_id}.json"
        pack.path = str(path)
        path.write_text(json.dumps(pack.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return pack


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
        "review_state: approved_after_proposal",
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


def _write_evidence_files(
    workspace: Path,
    domain: str,
    run_id: str,
    records: list[dict[str, object]],
) -> list[str]:
    """Persist one normalised JSON per retrieval record under vault/evidence/<domain>/.

    Each file is the durable evidence anchor that a wiki page cites.  Filename
    pattern keeps run provenance: ``<run_id>__<idx>__<canonical_hash>.json``.
    """

    domain_dir = safe_workspace_path(workspace, f"{EVIDENCE_ROOT}/{_slugify(domain)}")
    domain_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for idx, record in enumerate(records, start=1):
        canonical = str(record.get("canonical_id") or record.get("url") or f"r{idx}")
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]
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
        }
        file_name = f"{run_id}__{idx:03d}__{digest}.json"
        target = domain_dir / file_name
        target.write_text(
            json.dumps(evidence_record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(str(target.relative_to(workspace)))
    return written


def _claims_from_retrieval_records(
    records: list[dict[str, object]],
    *,
    domain: str,
    query: str,
) -> list[dict[str, object]]:
    """Generate one candidate claim per record.

    Conservative: confidence 0.5 (web evidence, single-source),
    review_state=proposed, bitemporal fields populated.  Lint + human review
    sharpen these later.
    """

    claims: list[dict[str, object]] = []
    seen_statements: set[str] = set()
    for record in records:
        snippet = str(record.get("snippet", "")).strip()
        if not snippet:
            continue
        statement = _first_sentence(snippet, max_chars=280)
        if not statement or statement.lower() in seen_statements:
            continue
        seen_statements.add(statement.lower())
        canonical = str(record.get("canonical_id") or record.get("url") or "")
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
                    }
                ],
                "against": [],
                "confidence": 0.5,
                "uncertainty": "single-source retrieval evidence; awaits cross-source confirmation",
                "review_state": "proposed",
                "t_valid_from": _utcnow(),
                "t_valid_to": None,
                "supersedes": [],
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
