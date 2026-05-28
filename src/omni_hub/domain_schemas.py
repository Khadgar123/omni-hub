"""Per-domain wiki sub-schemas (v0.13).

Each entry maps to ``vault/wiki/domains/<slug>/_schema.md``.  A domain
sub-schema may:

* declare authoritative source priorities (which sources the cascade
  hits — these are the ones a domain page MUST cite when possible),
* add or strengthen frontmatter fields beyond the global schema,
* override the default ``stale_after_days`` for ``wiki-lint`` data-gap
  detection (research domain accepts 2-year-old facts; international
  relations does not),
* pin to upstream forks under ``agent-harness/`` that own the workflow
  for that domain (research → ResearchFlow/PaperBite).

The global ``vault/wiki/AGENTS.md`` schema sets the floor; this file
sets per-domain overrides.  When both apply, the domain sub-schema
wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


DOMAIN_SCHEMA_VERSION = "v0.13"


@dataclass(slots=True)
class DomainSchema:
    slug: str
    display_name: str
    folder: str  # slug variant used under vault/wiki/domains/
    position: str
    authoritative_sources: list[str]
    frontmatter_required: list[tuple[str, str]] = field(default_factory=list)
    frontmatter_optional: list[tuple[str, str]] = field(default_factory=list)
    stale_after_days: int = 30
    pinned_refs: list[str] = field(default_factory=list)
    lint_hints: list[str] = field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# 12 domain schemas — keys match DEFAULT_DOMAIN_CASCADES keys
# ---------------------------------------------------------------------------


DOMAIN_SCHEMAS: dict[str, DomainSchema] = {
    "research": DomainSchema(
        slug="research",
        display_name="Research",
        folder="research",
        position=(
            "First (and reference) implementation of the global truth wiki母模板. "
            "Owns scholarly evidence (papers, citations, conferences).  Workflow "
            "engine lives upstream in `RipeMangoBox/ResearchFlow`; the read-only "
            "evidence vault is `RipeMangoBox/PaperBite`.  omni-hub compiles their "
            "output via wiki-ingest; it does NOT copy their notes into main repo."
        ),
        authoritative_sources=["openalex", "semantic_scholar", "arxiv", "wikipedia"],
        frontmatter_required=[
            ("paper_link", "URL to the canonical paper (OpenReview / arXiv abs / DOI)"),
            ("venue_year", "Conference + year, e.g. ICLR_2026"),
        ],
        frontmatter_optional=[
            ("doi", "DOI when available"),
            ("methods", "list of methods/algorithms the paper introduces"),
            ("topics", "list of topical tags from the analysis"),
            ("core_operator", "PaperBite-style one-line description of the central operator"),
            ("primary_logic", "PaperBite-style one-line description of the mechanism"),
        ],
        stale_after_days=730,
        pinned_refs=[
            "agent-harness/researchflow (upstream: RipeMangoBox/ResearchFlow)",
            "agent-harness/paperbite (upstream: RipeMangoBox/PaperBite)",
        ],
        lint_hints=[
            "research domain accepts 2-year-old facts — only flag data-gap after 730 days.",
            "broken_cross_ref severity=high: missing paper citations break academic trust.",
            "missing_concept findings on method/algorithm slugs SHOULD become new method pages.",
        ],
    ),
    "engineering": DomainSchema(
        slug="engineering",
        display_name="Engineering",
        folder="engineering",
        position=(
            "Software engineering, programming languages, framework evolution, "
            "system design.  Faster-moving than research — 6-month-old framework "
            "docs are likely stale; library APIs drift quarterly."
        ),
        authoritative_sources=["openalex", "arxiv", "wikipedia"],
        frontmatter_optional=[
            ("github_repo", "owner/name when the page concerns a specific repo"),
            ("language", "primary programming language"),
            ("framework_version", "framework version at time of writing"),
        ],
        stale_after_days=180,
        lint_hints=[
            "engineering pages tagged confidence: low for > 180d SHOULD trigger a re-ingest.",
            "github_repo links should be checked against current default branch.",
        ],
    ),
    "photography": DomainSchema(
        slug="photography",
        display_name="Photography",
        folder="photography",
        position=(
            "Reactive domain — content comes from user-forwarded links, not "
            "active ingest.  Wiki pages here are mostly portfolio notes, "
            "technique references, and gear comparisons."
        ),
        authoritative_sources=["unsplash", "pexels", "wikipedia"],
        frontmatter_required=[
            ("attribution", "photographer credit + license (CC0, CC-BY, etc.)"),
        ],
        frontmatter_optional=[
            ("camera_body", "e.g. Sony α7 IV"),
            ("lens", "lens used"),
            ("style_tags", "list of style descriptors"),
        ],
        stale_after_days=365,
        lint_hints=[
            "missing attribution = automatic broken_cross_ref severity high.",
            "low data-gap pressure — photography knowledge ages slowly.",
        ],
    ),
    "fashion": DomainSchema(
        slug="fashion",
        display_name="Fashion",
        folder="fashion",
        position=(
            "Reactive, taste-driven domain.  Pages capture season trends, brand "
            "histories, and outfit references.  No active cascade — built from "
            "vault snapshots."
        ),
        authoritative_sources=["wikipedia"],
        frontmatter_optional=[
            ("season", "e.g. SS26, FW25"),
            ("brand", "brand name"),
            ("price_tier", "luxury | premium | mid | budget"),
        ],
        stale_after_days=90,
        lint_hints=[
            "season pages SHOULD be superseded each cycle; flag stale_fact aggressively.",
        ],
    ),
    "chat_relationships": DomainSchema(
        slug="chat_relationships",
        display_name="Chat & Relationships",
        folder="chat-relationships",
        position=(
            "Purely reactive — no cascade hits.  Pages capture conversational "
            "patterns, social mappings, and shared context.  All ingest is via "
            "manual `wiki-propose-research` or `wiki-log --op manual`."
        ),
        authoritative_sources=[],
        frontmatter_optional=[
            ("participants", "list of named participants or roles"),
            ("context_window", "time range the page covers"),
        ],
        stale_after_days=180,
        lint_hints=[
            "data_gap is informational only — chat context decays naturally.",
            "missing_concept findings here often map to entity pages (people / roles).",
        ],
    ),
    "finance": DomainSchema(
        slug="finance",
        display_name="Finance",
        folder="finance",
        position=(
            "SEC filings, central-bank time-series, scholarly finance.  Data "
            "moves quarterly (10-K) or monthly (FRED); short stale threshold."
        ),
        authoritative_sources=["edgar", "fred", "openalex", "wikipedia"],
        frontmatter_required=[
            ("period", "data period (e.g. 2026-Q1)"),
        ],
        frontmatter_optional=[
            ("ticker", "stock ticker, e.g. NVDA"),
            ("cik", "SEC central index key"),
            ("fred_series_id", "FRED series identifier"),
            ("currency", "ISO 4217 code"),
        ],
        stale_after_days=30,
        lint_hints=[
            "stale_fact severity=high: outdated financial data is dangerous.",
            "broken_cross_ref on cik/ticker MUST be repaired before next ingest.",
        ],
    ),
    "policy": DomainSchema(
        slug="policy",
        display_name="Policy",
        folder="policy",
        position=(
            "US federal rules, dockets, bills, votes.  Per-domain cascade hits "
            "the canonical .gov sources directly; secondary news (GDELT) backs "
            "context.  Quarterly update cycle."
        ),
        authoritative_sources=[
            "federal_register", "regulations_gov", "congress_gov", "gdelt", "wikipedia",
        ],
        frontmatter_optional=[
            ("bill_id", "Congress.gov bill number"),
            ("regulation_id", "Federal Register doc number"),
            ("docket_id", "regulations.gov docket id"),
            ("jurisdiction", "US-federal | US-state-XX | etc."),
        ],
        stale_after_days=90,
        lint_hints=[
            "missing_concept on bill_id / regulation_id SHOULD become an event page.",
            "contradiction severity=high — policy positions across sources require resolution.",
        ],
    ),
    "international_relations": DomainSchema(
        slug="international_relations",
        display_name="International Relations",
        folder="international-relations",
        position=(
            "Cross-border events, conflicts, multilateral data.  Highest "
            "velocity domain — daily news cycle, weekly stale threshold."
        ),
        authoritative_sources=[
            "acled", "gdelt", "world_bank", "imf", "wikipedia",
        ],
        frontmatter_optional=[
            ("country_iso", "ISO 3166-1 alpha-3 country code(s)"),
            ("event_date", "ISO date of the underlying event"),
            ("conflict_type", "ACLED event_type if relevant"),
        ],
        stale_after_days=7,
        lint_hints=[
            "stale_fact severity=high — IR pages decay in days.",
            "contradiction frequent and EXPECTED — multiple narrative sources are the norm.",
        ],
    ),
    "ai_progress": DomainSchema(
        slug="ai_progress",
        display_name="AI Progress",
        folder="ai-progress",
        position=(
            "Frontier AI model / paper / release tracking.  Velocity higher "
            "than research overall — weekly-ish refresh."
        ),
        authoritative_sources=["hf_daily_papers", "arxiv", "openalex", "wikipedia"],
        frontmatter_optional=[
            ("arxiv_id", "e.g. 2510.04618"),
            ("hf_paper_url", "HuggingFace Daily Papers URL"),
            ("model_family", "e.g. Claude / GPT / Gemini / Llama"),
            ("model_version", "specific release version"),
        ],
        stale_after_days=14,
        lint_hints=[
            "stale threshold = 14d (AI progress moves faster than classic research).",
            "missing_concept on model_family slugs SHOULD become entity pages.",
        ],
    ),
    "agent_systems": DomainSchema(
        slug="agent_systems",
        display_name="Agent Systems",
        folder="agent-systems",
        position=(
            "Agent frameworks, SDKs, harness modules.  Pages here document the "
            "BUILD-vs-USE decisions and the pinned forks under `agent-harness/`."
        ),
        authoritative_sources=["wikipedia", "openalex", "gdelt"],  # falls through to default cascade
        frontmatter_optional=[
            ("framework", "framework name (Letta / DSPy / Graphiti / etc.)"),
            ("version", "version pinned in agent-harness"),
            ("decision", "BUILD | USE | PIN-AS-FORK | DEFER | REJECT"),
        ],
        stale_after_days=30,
        pinned_refs=[
            "agent-harness/dspy (pending fork — see agent-harness/manifest.json)",
            "agent-harness/openhands (pending fork)",
            "agent-harness/opik (pending fork)",
            "agent-harness/graphiti, agent-harness/argilla, agent-harness/promptfoo (pinned)",
        ],
        lint_hints=[
            "decision field MUST be one of the BUILD-vs-USE template enum values.",
            "broken_cross_ref severity=high — pinned forks must exist as submodules.",
        ],
    ),
    "social_en": DomainSchema(
        slug="social_en",
        display_name="Social (English)",
        folder="social-en",
        position=(
            "Tier-2 paid/broker social-media domain.  Opt-in only — no default "
            "cascade hit.  twitterapi.io paid lane.  Reactive: pages mostly "
            "from user-shared links + GDELT news context."
        ),
        authoritative_sources=["x_twitter", "gdelt"],
        frontmatter_optional=[
            ("platform", "x | reddit | hn | other"),
            ("post_id", "platform-native ID"),
            ("author", "post author handle"),
        ],
        stale_after_days=14,
        lint_hints=[
            "data_gap is expected — social pages reflect a moment, not a process.",
            "missing attribution / post_id = broken_cross_ref severity=medium.",
        ],
    ),
    "social_zh": DomainSchema(
        slug="social_zh",
        display_name="Social (Chinese)",
        folder="social-zh",
        position=(
            "Tier-2 broker-routed Chinese social-media.  Xiaohongshu via "
            "jackwener/xiaohongshu-cli subprocess bridge; WeChat MP via self-"
            "hosted rachelos/we-mp-rss RSS.  Legal personal-use only, "
            "share-link parsing rather than scraping."
        ),
        authoritative_sources=["xiaohongshu", "wechat_mp"],
        frontmatter_optional=[
            ("platform", "xiaohongshu | wechat_mp | weibo | other"),
            ("post_id", "platform-native ID"),
            ("author", "post author handle / public account name"),
        ],
        stale_after_days=14,
        lint_hints=[
            "DO NOT propose pages from auto-scrape; only manual share-link parse.",
            "broken_cross_ref severity=medium when post is deleted upstream (expected).",
        ],
    ),
}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


SCHEMA_MARKER = f"schema_version: {DOMAIN_SCHEMA_VERSION}"


def render_domain_schema(schema: DomainSchema) -> str:
    """Render a DomainSchema as the ``_schema.md`` markdown body."""

    lines = [
        "---",
        "omni_type: domain_schema",
        f"domain: {schema.slug}",
        SCHEMA_MARKER,
        f"stale_after_days: {schema.stale_after_days}",
        "---",
        "",
        f"# {schema.display_name} Domain Schema",
        "",
        "## Position",
        "",
        schema.position,
        "",
        "## Authoritative Sources",
        "",
    ]
    if schema.authoritative_sources:
        for src in schema.authoritative_sources:
            lines.append(f"- `{src}`")
    else:
        lines.append("- (none — purely reactive domain; manual ingest only)")

    if schema.frontmatter_required:
        lines.extend(["", "## Required Frontmatter (in addition to global schema)", ""])
        for field_name, desc in schema.frontmatter_required:
            lines.append(f"- `{field_name}` — {desc}")

    if schema.frontmatter_optional:
        lines.extend(["", "## Optional Frontmatter", ""])
        for field_name, desc in schema.frontmatter_optional:
            lines.append(f"- `{field_name}` — {desc}")

    lines.extend([
        "",
        "## Stale Threshold",
        "",
        f"`wiki-lint --rule data_gap` uses **{schema.stale_after_days} days** as "
        f"the default for this domain.  Override per-page via frontmatter when "
        f"the underlying fact has known longer/shorter validity.",
    ])

    if schema.pinned_refs:
        lines.extend(["", "## Pinned References", ""])
        for ref in schema.pinned_refs:
            lines.append(f"- {ref}")

    if schema.lint_hints:
        lines.extend(["", "## Domain-Specific Lint Hints", ""])
        for hint in schema.lint_hints:
            lines.append(f"- {hint}")

    if schema.notes:
        lines.extend(["", "## Notes", "", schema.notes])

    lines.extend([
        "",
        "---",
        "",
        f"_Auto-generated from `src/omni_hub/domain_schemas.py`._  "
        f"Edits will be overwritten on the next `wiki-init` when "
        f"`schema_version` advances.  To customise: bump the version in code, "
        f"do not hand-edit this file.",
        "",
    ])
    return "\n".join(lines)


def is_stale(body: str) -> bool:
    """True if the rendered body lacks the current SCHEMA_MARKER."""

    return SCHEMA_MARKER not in body[:600]


def materialise_all(wiki_domains_root: Path) -> dict[str, str]:
    """Write all 12 ``_schema.md`` files, refreshing any out-of-date ones.

    Returns ``{domain_slug: action}`` where action is ``"written"``,
    ``"refreshed"``, or ``"unchanged"``.
    """

    actions: dict[str, str] = {}
    for slug, schema in DOMAIN_SCHEMAS.items():
        target_dir = wiki_domains_root / schema.folder
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "_schema.md"
        new_body = render_domain_schema(schema)
        if not target.exists():
            target.write_text(new_body, encoding="utf-8")
            actions[slug] = "written"
            continue
        existing = target.read_text(encoding="utf-8")
        if existing == new_body:
            actions[slug] = "unchanged"
            continue
        if is_stale(existing):
            target.write_text(new_body, encoding="utf-8")
            actions[slug] = "refreshed"
        else:
            # Marker matches but content differs — operator hand-edited the
            # local copy; leave it alone so we don't clobber human work.
            actions[slug] = "hand-edited"
    return actions


def get_stale_after_days(domain: str, *, default: int = 30) -> int:
    """Look up the domain's data-gap threshold; falls back to default."""

    schema = DOMAIN_SCHEMAS.get(domain) or DOMAIN_SCHEMAS.get(domain.replace("-", "_"))
    return schema.stale_after_days if schema else default
