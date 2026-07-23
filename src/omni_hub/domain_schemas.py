"""Per-domain wiki sub-schemas (v0.19).

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

v0.19 changes:
* Split ``policy`` (US-only by definition) into ``us_policy`` + the
  new ``cn_policy`` so the user's 5-Plane architecture (see
  ``docs/architecture-v0.19.md``) can carry both regimes side-by-side.
* Added 6 new domains: ``meta`` (skill for iterating omni-hub itself),
  ``fitness_wellness``, ``cooking``, ``travel``, ``marketing``,
  ``enterprise`` — each is a vertical Skill-Plane domain with its
  own corpus + connectors + evaluation metric.

The global ``vault/wiki/AGENTS.md`` schema sets the floor; this file
sets per-domain overrides.  When both apply, the domain sub-schema
wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


DOMAIN_SCHEMA_VERSION = "v0.21"


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
    # Per-rule severity overrides — empty means "use the rule's default".
    # Keys are wiki_lint rule names (contradiction / stale_fact / orphan_page /
    # missing_concept / broken_cross_ref / data_gap).  Values are
    # ``high`` / ``medium`` / ``low`` / ``skip`` (skip = don't emit this rule
    # for pages whose ``domain`` matches this schema).
    rule_overrides: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 19 domain schemas — keys match DEFAULT_DOMAIN_CASCADES keys
# (12 from v0.13 with policy → us_policy + 7 new in v0.19)
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
            # v0.46 paper-artifact fields — modelled as structured frontmatter,
            # NOT new entities (KISS).  Populated by the new connectors:
            ("orcids", "author ORCIDs — from OpenAlex authorships (disambiguation)"),
            ("affiliations", "author institutions + ROR ids — from OpenAlex"),
            ("paper_versions", "PaperVersion list: arXiv v1/v2/.. + camera-ready {version,date,url}"),
            ("review_thread", "ReviewThread: OpenReview decision + avg_rating + n_reviews (openreview.forum_thread)"),
            ("acceptance", "venue decision: accepted / rejected / withdrawn / unknown (OpenReview)"),
            ("code_artifact", "Artifact: GitHub stars/license/latest-release/checkpoint (github.repo_audit)"),
            ("model_artifact", "Artifact: HF Hub model/dataset id + downloads (hf_hub.model_info)"),
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
        rule_overrides={
            "broken_cross_ref": "high",
            "missing_concept": "medium",
        },
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
        rule_overrides={
            "data_gap": "medium",          # framework churn matters more
        },
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
        authoritative_sources=["pexels", "unsplash", "wikipedia"],
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
        rule_overrides={
            "broken_cross_ref": "high",
            "data_gap": "low",
        },
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
        rule_overrides={
            "stale_fact": "high",
            "data_gap": "skip",            # reactive, not gap-driven
        },
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
        rule_overrides={
            "data_gap": "skip",            # purely reactive
            "stale_fact": "low",
        },
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
        rule_overrides={
            "stale_fact": "high",
            "broken_cross_ref": "high",
            "data_gap": "high",
        },
    ),
    "us_policy": DomainSchema(
        slug="us_policy",
        display_name="US Policy",
        folder="us-policy",
        position=(
            "US federal rules, dockets, bills, votes, Supreme Court rulings.  "
            "Per-domain cascade hits canonical .gov sources directly; secondary "
            "news (GDELT) backs context.  Quarterly update cycle.  Companion "
            "to ``cn_policy``; cross-references go through ``international_relations``."
        ),
        authoritative_sources=[
            "federal_register", "regulations_gov", "congress_gov", "courtlistener",
            "gdelt", "wikipedia",
        ],
        frontmatter_optional=[
            ("bill_id", "Congress.gov bill number"),
            ("regulation_id", "Federal Register doc number"),
            ("docket_id", "regulations.gov docket id"),
            ("scotus_case", "Supreme Court docket number when applicable"),
            ("jurisdiction", "US-federal | US-state-XX | etc."),
        ],
        stale_after_days=90,
        lint_hints=[
            "missing_concept on bill_id / regulation_id SHOULD become an event page.",
            "contradiction severity=high — policy positions across sources require resolution.",
            "Cross-references to cn_policy / international_relations are encouraged for trade / sanctions / treaty topics.",
        ],
        rule_overrides={
            "contradiction": "high",
            "missing_concept": "high",
        },
    ),
    "cn_policy": DomainSchema(
        slug="cn_policy",
        display_name="China Policy",
        folder="cn-policy",
        position=(
            "中国政策、法规、五年规划、各部委文件、中央财办、最高人民法院解释。"
            "Connectors land in v0.21 (gov.cn + 国务院 RSS + 各部委 + 央行).  Until "
            "then, pages here are populated manually via ``wiki-propose-research`` "
            "from user-curated PDF / link drops.  Companion to ``us_policy``; "
            "cross-references go through ``international_relations``."
        ),
        authoritative_sources=[
            "gov_cn", "stats_gov_cn", "court_gov_cn", "pbc_gov_cn",
            "wikipedia",
        ],
        frontmatter_optional=[
            ("document_id", "国务院 / 各部委文件号 (e.g. 国发〔2026〕12号)"),
            ("five_year_plan", "适用的五年规划 (e.g. 十四五 / 十五五)"),
            ("regulator", "发布机构 (国务院 / 央行 / 网信办 / 证监会 ...)"),
            ("jurisdiction", "national | province-XX | municipality-XX"),
        ],
        stale_after_days=90,
        lint_hints=[
            "中国政策更新节奏季度级;stale_after_days=90.",
            "contradiction severity=high — 与 us_policy 镜像;跨语言来源差异常见但必须 reconcile.",
            "broken_cross_ref severity=high — 文件号 / 五年规划 引用必须可解析.",
        ],
        rule_overrides={
            "contradiction": "high",
            "missing_concept": "high",
            "broken_cross_ref": "high",
        },
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
        rule_overrides={
            "stale_fact": "high",
            "contradiction": "low",        # noise floor; multi-narrative is normal
            "data_gap": "high",
        },
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
        rule_overrides={
            "data_gap": "medium",
            "missing_concept": "high",
        },
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
        rule_overrides={
            "broken_cross_ref": "high",    # missing submodule = build break risk
            "data_gap": "medium",
        },
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
        rule_overrides={
            "data_gap": "skip",            # social posts are snapshots
            "broken_cross_ref": "medium",
        },
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
        rule_overrides={
            "data_gap": "skip",
            "broken_cross_ref": "medium",
        },
    ),
    # ---------------------------------------------------------------
    # v0.19 new domains: 6 vertical skills the 5-Plane architecture
    # promotes to first-class.  meta is the self-iteration skill;
    # the other 5 are consumer/enterprise verticals the user listed
    # in the v0.19 refactor brief.
    # ---------------------------------------------------------------
    "meta": DomainSchema(
        slug="meta",
        display_name="Meta (Self-Iteration)",
        folder="meta",
        position=(
            "The skill that improves omni-hub itself.  Corpus = own commit "
            "history + AGENTS.md / CLAUDE.md / docs/* + accepted PreferenceRecords "
            "across all other skills + open GitHub issues.  Outputs are pages "
            "documenting BUILD-vs-USE decisions, schema migration plans, "
            "cross-skill optimization wins, and proposed control-plane changes.  "
            "**Does not write to vault/wiki/ directly** — emits "
            "Proposal(kind=wiki_update) like every other skill, the irony being "
            "that meta-skill changes go through the same human gate as the "
            "skills it analyses."
        ),
        authoritative_sources=[],
        frontmatter_optional=[
            ("affects_modules", "list of src/omni_hub modules a meta page concerns"),
            ("decision", "BUILD | USE | PIN-AS-FORK | DEFER | REJECT"),
            ("triggered_by", "preference_drift | lint_pattern | user_request | commit_pattern"),
        ],
        stale_after_days=60,
        lint_hints=[
            "meta pages MUST reference a specific module / commit / lint pattern.",
            "broken_cross_ref severity=high — meta links to code must point at real files.",
            "data_gap severity=low — meta knowledge accumulates, not depletes.",
        ],
        rule_overrides={
            "broken_cross_ref": "high",
            "data_gap": "low",
        },
    ),
    "fitness_wellness": DomainSchema(
        slug="fitness_wellness",
        display_name="Fitness & Wellness",
        folder="fitness-wellness",
        position=(
            "健身、营养、康复、睡眠、心理健康。 RCT-backed claims preferred; "
            "Bilibili / Instagram 健身博主 claims need supporting study links or "
            "are marked confidence: low.  Connectors land in v0.20 (PubMed + "
            "Bilibili).  High guard against pseudo-science: lint hints require "
            "RCT / meta-analysis citation for any 'do X to achieve Y' claim."
        ),
        authoritative_sources=["pubmed", "europe_pmc", "bilibili", "wikipedia"],
        frontmatter_optional=[
            ("modality", "strength | hypertrophy | cardio | mobility | nutrition | sleep | mental"),
            ("evidence_grade", "RCT | meta-analysis | observational | expert-opinion | n=1"),
            ("rct_link", "DOI of supporting RCT when claim is causal"),
        ],
        stale_after_days=365,
        lint_hints=[
            "evidence_grade frontmatter REQUIRED for any 'do X to achieve Y' claim.",
            "missing_concept severity=high on supplement / drug names — must link to safety profile.",
            "contradiction severity=high — fitness folklore frequently contradicts trials.",
        ],
        rule_overrides={
            "contradiction": "high",
            "missing_concept": "high",
            "broken_cross_ref": "medium",
        },
    ),
    "cooking": DomainSchema(
        slug="cooking",
        display_name="Cooking",
        folder="cooking",
        position=(
            "中餐 / 西餐 / 烘焙 / 发酵 / 食材保鲜.  Receptive domain: 小红书 + Bilibili 美食 + "
            "下厨房 + Allrecipes (英文) provide candidate recipes; user feedback "
            "(complete-and-rate) drives the PreferenceStore.  Connectors land in "
            "v0.20.  Each recipe page tracks substitutions + per-step constraints."
        ),
        authoritative_sources=["xiaohongshu", "bilibili", "wikipedia"],
        frontmatter_optional=[
            ("cuisine", "chinese-sichuan | chinese-cantonese | italian | japanese | thai | ..."),
            ("technique", "braise | stir-fry | bake | ferment | sous-vide | ..."),
            ("difficulty", "beginner | intermediate | advanced"),
            ("time_active_min", "active cooking time in minutes"),
            ("time_total_min", "total time including waiting"),
        ],
        stale_after_days=730,
        lint_hints=[
            "Recipe pages SHOULD link to at least one source video / blog (broken_cross_ref severity=low).",
            "data_gap severity=low — cooking knowledge is durable.",
        ],
        rule_overrides={
            "data_gap": "low",
            "broken_cross_ref": "low",
        },
    ),
    "travel": DomainSchema(
        slug="travel",
        display_name="Travel",
        folder="travel",
        position=(
            "Destinations, itineraries, transit, lodging, visa, seasonal timing.  "
            "Highly seasonal — Japan cherry-blossom claims valid Mar-Apr only.  "
            "Connectors land in v0.20 (小红书 + 马蜂窝 + TripAdvisor + 携程)."
        ),
        authoritative_sources=["xiaohongshu", "bilibili", "wikipedia"],
        frontmatter_optional=[
            ("country_iso", "ISO 3166-1 alpha-3"),
            ("city", "primary city / region"),
            ("trip_length_days", "suggested itinerary length"),
            ("season", "spring | summer | autumn | winter | year-round"),
            ("budget_tier", "shoestring | mid | premium | luxury"),
        ],
        stale_after_days=180,
        lint_hints=[
            "season + country combinations SHOULD trigger stale_fact when underlying season has passed by > 90d.",
            "visa / safety claims MUST cite government source (broken_cross_ref severity=high).",
        ],
        rule_overrides={
            "stale_fact": "medium",
            "broken_cross_ref": "high",
        },
    ),
    "marketing": DomainSchema(
        slug="marketing",
        display_name="Marketing & Promotion",
        folder="marketing",
        position=(
            "营销策略、文案模式、增长黑客、品牌定位、ROI 案例.  Fast cycle — weekly "
            "trending playbook shifts.  Connectors land in v0.20 (微博热搜 + 抖音 + "
            "营销博主 RSS) + v0.22 (Crunchbase 增长案例)."
        ),
        authoritative_sources=["weibo", "brave_search", "gdelt", "zhihu", "wikipedia"],
        frontmatter_optional=[
            ("channel", "social | seo | content | email | paid-ads | influencer"),
            ("industry", "saas | consumer | b2b | retail | fintech | ..."),
            ("case_company", "company the case study is about"),
            ("roi_metric", "the metric this playbook claims to move"),
        ],
        stale_after_days=60,
        lint_hints=[
            "case studies > 6mo old SHOULD trigger stale_fact (severity=medium).",
            "ROI claims without source severity=high (broken_cross_ref).",
        ],
        rule_overrides={
            "stale_fact": "medium",
            "broken_cross_ref": "high",
        },
    ),
    "enterprise": DomainSchema(
        slug="enterprise",
        display_name="Enterprise Analysis",
        folder="enterprise",
        position=(
            "公司分析 — 团队、组织架构、投融资、产品线、产品迭代、关键人事变动. "
            "Crunchbase / LinkedIn / 财报 PDF / 招股书 are the primary sources "
            "(land in v0.22).  Per-company page is a living dossier; supersedes "
            "old quarterly snapshots via bitemporal.  Used by Application Plane "
            "for enterprise-due-diligence tasks."
        ),
        authoritative_sources=[
            "crossref", "edgar", "brave_search", "wikidata", "wikipedia",
        ],
        frontmatter_required=[
            ("company_id", "Crunchbase UUID or LinkedIn company slug"),
        ],
        frontmatter_optional=[
            ("ticker", "stock ticker if public"),
            ("hq_country", "ISO 3166-1 alpha-3"),
            ("stage", "seed | series-A..F | public | private-equity | acquired"),
            ("vertical", "saas | fintech | bio | retail | ..."),
            ("headcount_band", "<10 | 10-50 | 50-200 | 200-1000 | 1000+"),
            ("funding_total_usd", "cumulative funding raised (USD)"),
        ],
        stale_after_days=90,
        lint_hints=[
            "stale_fact severity=high — outdated company info misleads investment / job decisions.",
            "broken_cross_ref severity=high — links to LinkedIn / Crunchbase must resolve.",
            "data_gap severity=high — missing quarterly update on tracked company is a real gap.",
        ],
        rule_overrides={
            "stale_fact": "high",
            "broken_cross_ref": "high",
            "data_gap": "high",
        },
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


def get_rule_override(domain: str, rule: str) -> str | None:
    """Return the domain's severity override for a lint rule, or None.

    Special value ``"skip"`` means the rule should be suppressed entirely
    for pages in this domain (Karpathy gist: "data_gap is informational
    only" — chat_relationships / fashion / social_* skip data_gap by
    construction).
    """

    if not domain:
        return None
    schema = DOMAIN_SCHEMAS.get(domain) or DOMAIN_SCHEMAS.get(domain.replace("-", "_"))
    if schema is None:
        return None
    return schema.rule_overrides.get(rule) or None
