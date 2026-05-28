"""Skill stub generator (v0.19).

For each of the 19 domain schemas in :mod:`omni_hub.domain_schemas` we
auto-generate a ``.agents/skills/<slug>-wiki/SKILL.md`` stub following the
Anthropic Skills v1.2 spec.  Stubs:

* declare ``name: <slug>-wiki``,
* include a multi-line ``description:`` listing trigger phrases (zh + en),
* embed the per-domain ``authoritative_sources`` + stale threshold + lint
  hint summary so an agent reading the SKILL.md does not need to load the
  full ``_schema.md`` first,
* point at the canonical CLI flows (``omni-hub retrieve`` /
  ``omni-hub wiki-search`` / ``omni-hub context-pack-build``),
* echo the Knowledge-Plane write boundary (``Proposal[T]`` chokepoint).

The stub is **safe to regenerate** — it includes a marker comment
``<!-- omni-skill-stub: v0.19 -->`` and ``regenerate_all`` skips files
that have been hand-edited (marker missing).  This matches
``materialise_all`` in ``domain_schemas.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .domain_schemas import DOMAIN_SCHEMAS, DomainSchema


SKILL_STUB_MARKER = "<!-- omni-skill-stub: v0.40 -->"
SKILL_STUB_VERSION = "v0.40"


# v0.40 — every skill carries a status that the router / UI can read.
# Lets the system distinguish "actually ships" from "scaffolded but
# returns stub data" from "needs an external broker on PATH".
SKILL_STATUSES = (
    "active",            # works end-to-end
    "stub",              # contracts exist; implementation returns placeholder
    "broker_required",   # works only when agent-harness/integrations/<x>/ installed
    "deprecated",        # superseded by another skill; kept for backward compat
)


# v0.40 — namespace hint for future lazy loading (OpenAI tool_search
# pattern: < 10 active per namespace, deferred for the rest).
SKILL_NAMESPACES = (
    "foundation_core",     # read-only, always-on (~6)
    "foundation_write",    # mutation primitives, deferred + approval-gated
    "foundation_eval",     # judge / ab / harness-compile / cross-skill
    "functional",          # cross-domain orchestrators
    "domain",              # the 19 wiki domains, task-routed
)


# ---------------------------------------------------------------------------
# Per-domain trigger phrases (zh + en) — used by the SKILL.md description
# block so a downstream agent knows when to load this skill.  Keyed by the
# DOMAIN_SCHEMAS key (snake_case).
# ---------------------------------------------------------------------------


_TRIGGERS: dict[str, list[str]] = {
    "research": [
        '"调研一下 X"', '"X 的论文 SOTA"', '"compare these two papers"',
        '"OpenReview 上 X 的评审"', '"ICLR 2026 X 方向有哪些工作"',
    ],
    "engineering": [
        '"这个 stack trace 是什么意思"', '"X 框架的 idiomatic 写法"',
        '"refactor this module"', '"为什么 test 挂了"',
    ],
    "ai_progress": [
        '"Claude 4.7 有什么新特性"', '"DSPy 3 怎么用"',
        '"GPT-5 / Gemini 3 / Llama 5 对比"', '"Anthropic Skills 怎么写"',
    ],
    "meta": [
        '"omni-hub 接下来该做什么"', '"哪些 skill 在掉点"',
        '"应该 BUILD 还是 PIN-AS-FORK"', '"v0.19 的下一步"',
    ],
    "fitness_wellness": [
        '"增肌应该怎么练"', '"睡眠不好怎么办"',
        '"creatine RCT meta-analysis"', '"减脂期蛋白质摄入"',
    ],
    "cooking": [
        '"今晚做什么"', '"红烧肉怎么做"',
        '"麻婆豆腐的关键步骤"', '"how do I temper chocolate"',
    ],
    "photography": [
        '"街拍 35mm 还是 50mm"', '"光圈优先 vs 快门优先"',
        '"Lightroom 风格分析"', '"how to expose for shadows in raw"',
    ],
    "fashion": [
        '"春季商务休闲穿搭"', '"婚礼伴郎西装预算 3k"',
        '"SS26 趋势"', '"怎么搭配 oversized 衬衫"',
    ],
    "chat_relationships": [
        '"这条消息该怎么回"', '"老板说 X 是什么意思"',
        '"how to set this boundary"', '"朋友冷战了怎么办"',
    ],
    "travel": [
        '"东京 5 天行程"', '"日本签证"',
        '"川西自驾路线"', '"巴厘岛雨季去合适吗"',
    ],
    "marketing": [
        '"SaaS 早期增长 playbook"', '"小红书 投放经验"',
        '"how to write better ad copy"', '"漏斗优化案例"',
    ],
    "enterprise": [
        '"OpenAI 最新组织架构"', '"X 公司值得加入吗"',
        '"分析这家公司的护城河"', '"due diligence on Y startup"',
    ],
    "finance": [
        '"NVDA 财报"', '"美联储利率路径"',
        '"A 股新能源板块"', '"how to read a 10-K"',
    ],
    "us_policy": [
        '"SCOTUS 2026 大案"', '"Federal Register 最新法规"',
        '"Congress 投票走向"', '"X act 的影响"',
    ],
    "cn_policy": [
        '"2026 中央财办文件"', '"网信办最新规定"',
        '"五年规划 X 章节"', '"国发〔2026〕12号"',
    ],
    "international_relations": [
        '"中美关系最新"', '"俄乌局势"',
        '"台海动态"', '"OPEC 决议"',
    ],
    "agent_systems": [
        '"Letta vs Mem0 怎么选"', '"DSPy GEPA 真的有用吗"',
        '"OpenHands worker 怎么部署"', '"应该 fork 还是 pin"',
    ],
    "social_en": [
        '"这条 tweet 火了"', '"HN 在讨论 X"',
        '"Reddit r/X 的态度"',
    ],
    "social_zh": [
        '"小红书最近在炒什么"', '"微博热搜 X"',
        '"公众号 X 的最新文章"',
    ],
}


# Tiny per-domain "one-line hero" used as the description first line.
_HERO: dict[str, str] = {
    "research": "Scholarly research — papers / citations / venue context.",
    "engineering": "Software engineering — stack traces, frameworks, refactors.",
    "ai_progress": "Frontier AI — models, papers, releases.",
    "meta": "omni-hub self-iteration — what to BUILD / USE / PIN / DEFER.",
    "fitness_wellness": "Training / nutrition / recovery / sleep — RCT-backed.",
    "cooking": "Recipes / techniques / substitutions.",
    "photography": "Visual decisions — light / lens / composition / edit.",
    "fashion": "Outfit / season / fit / budget recommendations.",
    "chat_relationships": "Conversational nuance + privacy-safe relationship context.",
    "travel": "Itinerary / lodging / visa / seasonal timing.",
    "marketing": "Playbooks / channel mix / ROI case studies.",
    "enterprise": "Per-company dossiers — team / funding / product / changes.",
    "finance": "Markets / filings / rates / risk disclosure.",
    "us_policy": "US federal/state policy — bills / regs / SCOTUS.",
    "cn_policy": "中国政策 — 部委文件 / 五年规划 / 央行规定.",
    "international_relations": "Cross-border events / actors / scenarios.",
    "agent_systems": "Agent frameworks / SDKs / BUILD-vs-USE decisions.",
    "social_en": "English social media — Twitter / Reddit / HN.",
    "social_zh": "中文社交媒体 — 微博 / 小红书 / 公众号.",
}


@dataclass(slots=True)
class StubAction:
    skill_id: str
    folder: Path
    action: str  # "written" | "refreshed" | "unchanged" | "hand-edited"


def regenerate_all(
    skills_root: Path | str = ".agents/skills",
    *,
    workspace: Path | str = ".",
) -> list[StubAction]:
    """Materialise SKILL.md stubs for every domain in :data:`DOMAIN_SCHEMAS`.

    Files that lack the ``SKILL_STUB_MARKER`` are presumed hand-edited and
    left alone (the human edit wins).  Files that have the marker are
    refreshed iff the rendered body differs from disk.
    """

    skills_root = Path(workspace) / Path(skills_root)
    skills_root.mkdir(parents=True, exist_ok=True)

    actions: list[StubAction] = []
    for domain_slug, schema in DOMAIN_SCHEMAS.items():
        skill_id = f"{schema.folder}-wiki"
        skill_dir = skills_root / skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)
        target = skill_dir / "SKILL.md"
        new_body = render_skill_stub(domain_slug, schema)
        action = _materialise_one(target, new_body)
        actions.append(StubAction(
            skill_id=skill_id, folder=skill_dir, action=action,
        ))
    return actions


def _materialise_one(target: Path, new_body: str) -> str:
    if not target.exists():
        target.write_text(new_body, encoding="utf-8")
        return "written"
    existing = target.read_text(encoding="utf-8")
    if existing == new_body:
        return "unchanged"
    # Loose marker match: any line starting with "<!-- omni-skill-stub:" is
    # an auto-managed stub, regardless of version.  Lets a marker bump
    # (v0.19 → v0.37) auto-refresh without operator intervention.
    auto_managed = any(
        line.lstrip().startswith("<!-- omni-skill-stub:")
        for line in existing.splitlines()
    )
    if not auto_managed:
        return "hand-edited"
    target.write_text(new_body, encoding="utf-8")
    return "refreshed"


def render_skill_stub(domain_slug: str, schema: DomainSchema) -> str:
    """Render the SKILL.md body for a domain.

    Anthropic Skills v1.2 expects a YAML frontmatter block with at minimum
    ``name`` + ``description``.  We additionally include ``status`` and
    ``license`` so ``wiki-doctor`` / ``skill-list`` can filter.
    """

    skill_id = f"{schema.folder}-wiki"
    hero = _HERO.get(domain_slug, f"{schema.display_name} domain skill.")
    triggers = _TRIGGERS.get(domain_slug, ['"问题里提到 ' + schema.display_name + '"'])
    triggers_md = "\n  - ".join(triggers)
    sources = ", ".join(f"`{s}`" for s in schema.authoritative_sources) or "_(reactive — no cascade by default)_"
    rule_overrides_md = "\n".join(
        f"  - `{rule}` → **{severity}**"
        for rule, severity in sorted(schema.rule_overrides.items())
    ) or "  _(none — uses global defaults)_"

    eval_dimensions = (
        "evidence_coverage / information_density / citation_support / "
        "style_fit / uncertainty_calibration"
    )
    return f"""---
name: {skill_id}
status: active-domain
description: |
  {hero}

  Triggers — invoke this skill when the user asks any of:
  - {triggers_md}

  Source corpus: vault/wiki/domains/{schema.folder}/.  Authoritative
  cascade: {sources}.  Stale threshold: {schema.stale_after_days} days.

  Do NOT trigger for: queries that match a different domain's keywords
  (the task_router in src/omni_hub/app/task_router.py picks the right
  one).  Do NOT use this for writing — all writes go through
  Proposal[T] (see "Write Policy" below).
license: MIT
schema_version: {SKILL_STUB_VERSION}
omni_hub:
  layer: domain
  namespace: domain
  kind: domain_wiki
  display_name: "{schema.display_name} — Wiki Domain Skill"
  status: active
  version: 0.1.0
  entrypoint: "operation:context_pack_build"
  risk_level: L0
  required_permissions: []
{_render_connector_list(schema.authoritative_sources)}
  tags:
    - wiki
    - domain
    - {domain_slug}
---

{SKILL_STUB_MARKER}

# {schema.display_name} — Wiki Domain Skill

This is the **{domain_slug}** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["{domain_slug}"]`.

> {schema.position}

Every domain skill ships the v0.40 **5-section contract** — Retrieve /
Apply / Guardrails / Eval Metric / Write Policy — so reviewers can audit
each domain to the same checklist.

## 1. Retrieve Knowledge

```bash
# In-wiki query (FTS5 + substring fallback; filters superseded by default)
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \\
  --query "..." --backend fts5

# Tier-bounded context bundle (minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \\
  --query "..." --domain {domain_slug} --tier standard

# GraphRAG-style community probe (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \\
  --node <canonical_id_or_slug>
```

Authoritative cascade: {sources}.  When in doubt, default to ``tier=standard``.

## 2. Apply Knowledge

What this skill **does** with the retrieved context (the contract a
caller can rely on):

- Synthesise a cited answer to the user's question, drawing only from
  pages whose ``review_state == approved`` and ``t_valid_to`` either
  null or in the future.
- For factual claims, cite ``claim_id`` from ``.omni/claims.jsonl`` —
  callers can re-resolve via ``claims-show``.
- For methodological / procedural questions, walk the
  ``methods/`` + ``concepts/`` subfolders before falling back to
  ``syntheses/``.
- If the context pack returns empty, surface "no claims yet" rather
  than hallucinating — let the user choose to ingest more evidence
  via the section below.

## 3. Guardrails

{chr(10).join(f"- {hint}" for hint in schema.lint_hints) if schema.lint_hints else "- _(no domain-specific hints — uses global rules)_"}

Lint severity overrides:

{rule_overrides_md}

## 4. Eval Metric

- Composite score = Judge composite ({eval_dimensions}) computed by
  ``omni-hub judge-evaluate --domain {domain_slug} --candidate ...``.
- Per-domain rubric weights live in
  ``src/omni_hub/harness/domain_profiles.py::_DOMAIN_RUBRIC_OVERRIDES``.
- PreferenceStore at ``.omni/preference/{domain_slug}.jsonl`` —
  ``harness-compile-skill --domain {domain_slug}`` consumes this weekly
  and proposes SKILL.md body updates as DSPy 5-component artifacts.
- A/B test variants with ``omni-hub ab-test --domain {domain_slug}``.

## 5. Write Policy

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/{schema.folder}/` directly.

```bash
# 1) Cascade retrieves evidence (read-only)
PYTHONPATH=src python3 -m omni_hub.cli retrieve \\
  --query "..." --domain {domain_slug} --persist-evidence

# 2) Bridge to a Proposal(kind=wiki_update) — humans review
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \\
  --run-id <run_id> --domain {domain_slug}

# 3) Human review
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land approved Proposal → vault/wiki/domains/{schema.folder}/ + claims.jsonl
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>

# Retire stale claims: bitemporal close, never delete.
PYTHONPATH=src python3 -m omni_hub.cli wiki-supersede --old <id> --new <id>
```

### Required frontmatter on new pages

{_render_frontmatter_block(schema)}

---

_Auto-generated stub.  Hand-editing is supported — remove the
`{SKILL_STUB_MARKER}` marker line to opt out of future regenerations._
"""


def _render_connector_list(connectors: list[str]) -> str:
    """YAML-list render for the ``connectors:`` field inside ``omni_hub:``.

    Returns the *complete* ``connectors:`` line.  Empty lists must use
    inline ``[]`` syntax on the same line — the previous "key:\\n    []"
    form was misparsed as a list of strings ``["[]"]`` by some YAML
    parsers, polluting the skill registry (v0.42 fix).
    """

    if not connectors:
        return "  connectors: []"
    items = "\n".join(f"    - {c}" for c in connectors)
    return f"  connectors:\n{items}"


def _render_frontmatter_block(schema: DomainSchema) -> str:
    lines: list[str] = ["```yaml", "---"]
    lines.append("page_type: concept | entity | event | method | synthesis | domain_page")
    lines.append(f"domain: {schema.slug}")
    if schema.frontmatter_required:
        lines.append("# required (domain-specific)")
        for field_name, desc in schema.frontmatter_required:
            lines.append(f"{field_name}: ...   # {desc}")
    if schema.frontmatter_optional:
        lines.append("# optional (domain-specific)")
        for field_name, desc in schema.frontmatter_optional:
            lines.append(f"# {field_name}: ...   # {desc}")
    lines.append("# global bitemporal")
    lines.append("t_valid_from: YYYY-MM-DD")
    lines.append("t_valid_to: null")
    lines.append("review_state: approved | proposed | conflict")
    lines.append("---")
    lines.append("```")
    return "\n".join(lines)


__all__ = [
    "SKILL_STUB_MARKER",
    "SKILL_STUB_VERSION",
    "FOUNDATION_SKILLS",
    "FUNCTIONAL_SKILLS",
    "StubAction",
    "regenerate_all",
    "regenerate_foundation",
    "regenerate_functional",
    "render_skill_stub",
    "render_foundation_stub",
    "render_functional_stub",
]


# ---------------------------------------------------------------------------
# v0.38 — Foundation + Functional skill definitions
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FoundationSkill:
    """Foundation-tier skill spec — always-on primitive, no domain knowledge."""

    skill_id: str            # e.g. "retrieve"
    display_name: str
    hero: str                # one-line elevator
    triggers: list[str]      # verbatim user-language phrases that should invoke
    entrypoint: str          # "operation:<builtin_name>" or "" for routers
    risk_level: str = "L0"   # default read-only
    bucket: str = "knowledge_access"   # knowledge_access | knowledge_update | eval | workflow | channel
    body_md: str = ""        # additional markdown body beyond the auto frontmatter
    status: str = "active"   # active | stub | broker_required | deprecated
    namespace: str = "foundation_core"   # foundation_core | foundation_write | foundation_eval


@dataclass(slots=True)
class FunctionalSkill:
    """Functional-tier skill — cross-domain orchestrator that composes
    foundation skills.  Sits in ``app/`` semantics."""

    skill_id: str
    display_name: str
    hero: str
    triggers: list[str]
    entrypoint: str
    composes: list[str]      # foundation/domain skill_ids this orchestrates
    risk_level: str = "L0"
    body_md: str = ""
    status: str = "active"   # active | stub | broker_required | deprecated
    namespace: str = "functional"


FOUNDATION_SKILLS: list[FoundationSkill] = [
    # Knowledge access (5) ---------------------------------------------
    # The list below is iterated and each item gets its namespace
    # auto-derived from `bucket` at module import time (see
    # ``_apply_namespace_defaults`` further down).
    FoundationSkill(
        skill_id="context-pack",
        display_name="Context Pack Builder",
        hero="Assemble a tier-bounded context bundle (minimal/standard/expanded) "
             "from vault/wiki + research-kb for a given query + domain.",
        triggers=[
            "build a context pack for X",
            "上下文打包 X domain",
            "把 X 主题的 wiki 段抽出来",
        ],
        entrypoint="operation:context_pack_build",
        bucket="knowledge_access",
    ),
    FoundationSkill(
        skill_id="wiki-search",
        display_name="Wiki Search",
        hero="Search vault/wiki via FTS5 (with substring fallback); filter "
             "out superseded / rejected pages by default.",
        triggers=[
            "search the wiki for X",
            "wiki 里有没有 X 的页",
            "find pages about X in the wiki",
        ],
        entrypoint="operation:wiki_search",
        bucket="knowledge_access",
    ),
    FoundationSkill(
        skill_id="claims-show",
        display_name="Claims Lookup",
        hero="Inspect a single ClaimLedger record by claim_id (bitemporal: "
             "shows t_valid_from/to + superseded_by chain).",
        triggers=[
            "show claim c_abc123",
            "看一下 claim c_xyz",
            "look up the supersedes chain of X",
        ],
        entrypoint="operation:claims_show",
        bucket="knowledge_access",
    ),
    FoundationSkill(
        skill_id="memory-search-foundation",
        display_name="Memory Search (Foundation)",
        hero="Query archival memory across documents / entities / relations "
             "by case-insensitive substring (stdlib FTS).",
        triggers=[
            "search memory for X",
            "memory 里提过 X 吗",
            "what do we remember about X",
        ],
        entrypoint="operation:search_memory",
        bucket="knowledge_access",
    ),
    FoundationSkill(
        skill_id="url-capture",
        display_name="URL Capture",
        hero="Fetch a single URL + persist as a Resource under "
             "vault/raw/ with provenance.  Used by Inbox forward-routing.",
        triggers=[
            "capture this URL",
            "fetch + save https://...",
            "把这个链接存到 KB",
        ],
        entrypoint="operation:capture_url",
        risk_level="L1",
        bucket="knowledge_access",
    ),

    # Knowledge update (6) ---------------------------------------------
    FoundationSkill(
        skill_id="wiki-ingest",
        display_name="Wiki Ingest",
        hero="Bridge a retrieve cascade run into a Proposal(kind=wiki_update); "
             "writes vault/evidence + composes candidate claims; humans "
             "approve before content lands in vault/wiki.",
        triggers=[
            "ingest this retrieval run into the wiki",
            "把 run_id 转成 wiki proposal",
            "consolidate this research into a wiki page",
        ],
        entrypoint="operation:wiki_ingest",
        risk_level="L1",
        bucket="knowledge_update",
    ),
    FoundationSkill(
        skill_id="wiki-propose-research",
        display_name="Wiki Propose (Research Asset)",
        hero="Select a single ResearchFlow / PaperBite analysis note as a "
             "Proposal(wiki_update) — for when retrieve cascade isn't needed.",
        triggers=[
            "propose this paperbite note into the wiki",
            "wiki-propose 这条 researchflow",
            "import this analysis into the global truth wiki",
        ],
        entrypoint="operation:wiki_propose_research",
        risk_level="L1",
        bucket="knowledge_update",
    ),
    FoundationSkill(
        skill_id="wiki-apply",
        display_name="Wiki Apply (Approved Proposal)",
        hero="Land an already-approved wiki_update Proposal: write the page, "
             "append claims, append log.md, refresh FTS5, auto-record a "
             "PreferenceRecord(accepted).",
        triggers=[
            "apply approved proposal X to the wiki",
            "wiki-apply --proposal X",
            "把 approved 的 proposal 落地",
        ],
        entrypoint="operation:wiki_apply_proposal",
        risk_level="L1",
        bucket="knowledge_update",
    ),
    FoundationSkill(
        skill_id="wiki-supersede",
        display_name="Wiki Supersede (Bitemporal Close)",
        hero="Close an old claim's t_valid_to window + link superseded_by; "
             "never deletes (Graphiti/Zep pattern).",
        triggers=[
            "supersede claim X with Y",
            "wiki-supersede --new Y --old X",
            "废弃旧 claim 改成新版本",
        ],
        entrypoint="operation:wiki_supersede",
        risk_level="L1",
        bucket="knowledge_update",
    ),
    FoundationSkill(
        skill_id="wiki-lint",
        display_name="Wiki Lint",
        hero="Eight-rule scan (contradiction / stale_fact / orphan_page / "
             "missing_concept / broken_cross_ref / data_gap / "
             "cross_ref_asymmetry / abandoned_page) — emits "
             "Proposal(lint_finding) per issue.",
        triggers=[
            "lint the wiki",
            "wiki-lint --persist",
            "scan for contradictions in the wiki",
        ],
        entrypoint="operation:wiki_lint",
        risk_level="L1",
        bucket="knowledge_update",
    ),
    FoundationSkill(
        skill_id="wiki-dream",
        display_name="Wiki Dream (Offline Consolidation)",
        hero="Local-first dual of Anthropic Dreaming: scan recent retrieval + "
             "raw + claims, propose consolidations (cluster_canonical / "
             "statement_cluster / raw_orphan / stale_active).",
        triggers=[
            "run a wiki-dream consolidation pass",
            "consolidate recent retrieval + claims",
            "offline 整理一下 wiki",
        ],
        entrypoint="operation:wiki_dream",
        risk_level="L1",
        bucket="knowledge_update",
    ),

    # Eval (3) ---------------------------------------------------------
    FoundationSkill(
        skill_id="judge-evaluate",
        display_name="Judge Evaluate",
        hero="Score a candidate answer against a domain rubric (5-dim: "
             "evidence_coverage / information_density / citation_support / "
             "style_fit / uncertainty_calibration).  HeuristicJudge stdlib; "
             "LLMJudge via ccLoad / Anthropic SDK fallback.",
        triggers=[
            "judge this answer against the X rubric",
            "评一下这段输出",
            "score candidate with the LLM judge",
        ],
        entrypoint="operation:judge_evaluate",
        bucket="eval",
    ),
    FoundationSkill(
        skill_id="ab-test",
        display_name="A/B Test",
        hero="Run two candidate variants side-by-side; Judge composite delta; "
             "classify decisive / moderate / marginal / tie; persist in "
             ".omni/ab_tests.sqlite3 for lifetime win-rate.",
        triggers=[
            "ab-test these two prompts",
            "compare A vs B with the judge",
            "对比两版输出",
        ],
        entrypoint="operation:ab_test_run",
        risk_level="L1",
        bucket="eval",
    ),
    FoundationSkill(
        skill_id="harness-compile-skill",
        display_name="Harness Compile Skill",
        hero="Compile a domain's PreferenceStore (accepted / rejected spans) "
             "into the domain's .agents/skills/<x>-wiki/SKILL.md body "
             "(DSPy 5-component artifact).",
        triggers=[
            "recompile the X-wiki skill",
            "harness-compile-skill --domain X",
            "重新编译某 domain 的 SKILL.md",
        ],
        entrypoint="operation:harness_compile_skill",
        risk_level="L1",
        bucket="eval",
    ),

    # Workflow (2) -----------------------------------------------------
    FoundationSkill(
        skill_id="propose-approve",
        display_name="Approve Proposal",
        hero="Human-review gate: approve a pending Proposal[T] so the "
             "downstream apply / supersede / lint-fix step can land it.",
        triggers=[
            "approve proposal X",
            "通过这个 proposal",
            "propose-approve --id X",
        ],
        entrypoint="operation:approve_proposal",
        risk_level="L1",
        bucket="workflow",
    ),
    FoundationSkill(
        skill_id="propose-reject",
        display_name="Reject Proposal",
        hero="Human-review gate: reject a pending Proposal[T] with a reason "
             "the audit log captures.",
        triggers=[
            "reject proposal X — reason: ...",
            "拒掉这个 proposal",
            "propose-reject --id X --reason ...",
        ],
        entrypoint="operation:reject_proposal",
        risk_level="L1",
        bucket="workflow",
    ),
]


def _apply_namespace_defaults() -> None:
    """v0.40: assign each foundation skill its namespace from its bucket."""

    bucket_to_namespace = {
        "knowledge_access": "foundation_core",
        "knowledge_update": "foundation_write",
        "workflow":         "foundation_write",
        "eval":             "foundation_eval",
        "channel":          "foundation_core",
    }
    for skill in FOUNDATION_SKILLS:
        skill.namespace = bucket_to_namespace.get(skill.bucket, "foundation_core")


_apply_namespace_defaults()


FUNCTIONAL_SKILLS: list[FunctionalSkill] = [
    FunctionalSkill(
        skill_id="chat-route",
        display_name="Chat Route",
        hero="Route a conversational query to the right domain skill via "
             "intent classification; recommend the downstream OperationSpec.",
        triggers=[
            "route this question to the right skill",
            "用 chat router 决定 domain",
            "where should this query go",
        ],
        entrypoint="operation:app_route_task",
        composes=["retrieve", "context-pack"],
    ),
    FunctionalSkill(
        skill_id="app-report-build",
        display_name="App Report Build",
        hero="Cross-skill daily / weekly / monthly report — pure data rollup; "
             "--narrate enqueues a claude lane task for trend analysis "
             "(lands as Proposal(generation)).",
        triggers=[
            "build a weekly report",
            "today's daily digest",
            "做一个本月 report",
        ],
        entrypoint="operation:app_report_build",
        composes=["claims-show", "wiki-lint"],
    ),
    FunctionalSkill(
        skill_id="inbox-route",
        display_name="Inbox Route (Forwarded Content)",
        hero="Classify a forwarded item (URL / PDF / .ics / task / wiki).  "
             "v0.40: classifier only — does NOT yet dispatch to the typed "
             "handlers (url-capture, calendar-add, task-add, "
             "wiki-propose-research).  Dispatch lands in v0.41 once each "
             "downstream handler returns a Proposal so audit + approval "
             "apply uniformly.",
        triggers=[
            "I just forwarded this — handle it",
            "把这个内容收进 KB",
            "convert this email into the right action",
        ],
        entrypoint="operation:inbox_classify",
        composes=["url-capture", "calendar-add", "task-add", "wiki-propose-research"],
        status="stub",
    ),
    FunctionalSkill(
        skill_id="project-plan",
        display_name="Project Plan",
        hero="Create a high-level Project row.  v0.40: stub — only persists "
             "the Project; does NOT yet enqueue a claude-lane planner task "
             "or emit a Proposal(kind=project_plan).  Full planner+decompose "
             "flow lands in v0.41.",
        triggers=[
            "plan a project to ship X",
            "decompose this multi-week effort",
            "做个 plan 把 X 拆成子任务",
        ],
        entrypoint="operation:project_plan",
        composes=["task-add"],
        risk_level="L1",
        status="stub",
    ),
    FunctionalSkill(
        skill_id="pptx-build",
        display_name="PPTX Build",
        hero="Render a typed DeckOutline → real .pptx via the python-pptx "
             "shim in agent-harness/integrations/pptx/.  Never generates "
             "raw OOXML.  Requires the ``pptx-omni`` binary on PATH; without "
             "it the operation returns ``skipped: true`` (no error).",
        triggers=[
            "build a pptx from this outline",
            "做一份 deck",
            "render this outline as a slide deck",
        ],
        entrypoint="operation:pptx_build",
        composes=["context-pack"],
        risk_level="L1",
        status="broker_required",
    ),
    FunctionalSkill(
        skill_id="calendar-add",
        display_name="Calendar Add",
        hero="Add a CalendarEvent to vault/users/<user>/calendar/<YYYY-MM>.ics "
             "(stdlib RFC 5545 writer).  iCal-syncable via any CalDAV client.",
        triggers=[
            "add this to my calendar",
            "schedule a meeting on X at Y",
            "记一个日程",
        ],
        entrypoint="operation:calendar_add",
        composes=[],
        risk_level="L1",
    ),
    FunctionalSkill(
        skill_id="schedule-plan",
        display_name="Schedule Plan",
        hero="Deterministic time-block solver: place PersonalTasks into free "
             "Calendar slots by priority + due_at + duration.",
        triggers=[
            "plan my week",
            "auto-block today's tasks",
            "把待办排进日历",
        ],
        entrypoint="operation:schedule_plan",
        composes=["task-add", "calendar-add"],
        risk_level="L1",
    ),
    FunctionalSkill(
        skill_id="task-add",
        display_name="Task Add",
        hero="Append a PersonalTask (user-facing todo, NOT a worker queue "
             "task) to .omni/personal_tasks.sqlite3.",
        triggers=[
            "add a todo: ...",
            "remind me to X",
            "记一个任务: ...",
        ],
        entrypoint="operation:task_add",
        composes=[],
        risk_level="L1",
    ),
    FunctionalSkill(
        skill_id="finance-screen",
        display_name="Finance Screen",
        hero="Read-only stock screening against existing connectors (EDGAR / "
             "FRED / Tushare / Crunchbase).  v0.40: stub — returns ``[]`` "
             "because real screening requires connector API keys + a "
             "structured-query pathway (Tushare is API-only, EDGAR returns "
             "filings not screens).  v0.41 lands a thin SQL-style screen "
             "over locally-cached evidence.",
        triggers=[
            "screen US large-cap AI plays",
            "找 A 股新能源",
            "screen by sector + market_cap",
        ],
        entrypoint="operation:finance_screen",
        composes=["retrieve", "context-pack"],
        status="stub",
    ),
    FunctionalSkill(
        skill_id="order-propose",
        display_name="Order Propose",
        hero="Emit an OrderIntent + RiskCheckResult as "
             "Proposal(kind=order_intent).  Hard-blocks > 25% portfolio "
             "position; warns > 10%; refuses MARKET-without-price.  Human "
             "approves; broker CLI in agent-harness executes.",
        triggers=[
            "propose a BUY of NVDA at limit 195",
            "下一个 limit 单 (走 Proposal)",
            "place an order — Proposal first",
        ],
        entrypoint="operation:order_propose",
        composes=["finance-screen", "propose-approve"],
        risk_level="L2",
    ),
    FunctionalSkill(
        skill_id="meta-cross-skill-scan",
        display_name="Meta Cross-Skill Scan",
        hero="Scan PreferenceStore across all 19 domains; surface tokens "
             "with strong accepted-signal in ≥3 domains but absent in "
             "others; emit CrossSkillFinding for human review.",
        triggers=[
            "find cross-skill patterns",
            "哪些 token 在多个 domain 都被 accept",
            "scan for meta-skill transfer candidates",
        ],
        entrypoint="operation:meta_cross_skill_scan",
        composes=["judge-evaluate"],
    ),
]


# ---------------------------------------------------------------------------
# Foundation / Functional stub renderers
# ---------------------------------------------------------------------------


def render_foundation_stub(skill: FoundationSkill) -> str:
    triggers_md = "\n  - ".join(f'"{t}"' for t in skill.triggers)
    return f"""---
name: {skill.skill_id}
status: active-foundation
description: |
  {skill.hero}

  Triggers — invoke this skill when the user says any of:
  - {triggers_md}

  This is a **foundation primitive** (no domain knowledge baked in).  Use it
  as a building block from any other skill — see also ``app-report-build``,
  ``chat-route``, ``inbox-route``, and the 19 ``*-wiki`` domain skills.
license: MIT
schema_version: {SKILL_STUB_VERSION}
omni_hub:
  layer: foundation
  namespace: {skill.namespace}
  bucket: {skill.bucket}
  display_name: "{skill.display_name}"
  status: {skill.status}
  version: 0.1.0
  entrypoint: "{skill.entrypoint}"
  risk_level: {skill.risk_level}
  required_permissions: []
  connectors: []
  tags:
    - foundation
    - {skill.bucket}
    - {skill.namespace}
---

{SKILL_STUB_MARKER}

# {skill.display_name}

{skill.hero}

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli {skill.skill_id} [--help]
```

(See ``src/omni_hub/cli/`` for the argparse definition — every foundation
skill has a matching CLI subcommand by the same name.)

## When to use

The Trigger phrases above are intentionally narrow.  Foundation primitives
do not carry domain knowledge — if a query mentions a specific domain
(finance, fitness, cooking, etc.), route through ``chat-route`` first,
which will select the right ``*-wiki`` skill and feed the answer back.

{skill.body_md or ""}

## Hard rules

- Foundation skills NEVER write to ``vault/wiki/`` directly — all
  mutating paths land a ``Proposal[T]`` and wait for human approval.
- Foundation skills NEVER call an LLM directly.  Generation happens
  through claude/codex worker lanes — this primitive is the
  deterministic part of the loop.

---

_Auto-generated stub.  Remove the ``{SKILL_STUB_MARKER}`` marker line to
opt out of regeneration (the v0.32 ``materialise_all`` rule)._
"""


def render_functional_stub(skill: FunctionalSkill) -> str:
    triggers_md = "\n  - ".join(f'"{t}"' for t in skill.triggers)
    composes_md = ", ".join(f"`{c}`" for c in skill.composes) or "_(none — pure orchestrator)_"
    status_banner = ""
    if skill.status == "stub":
        status_banner = (
            "\n\n  > **Status: stub** — contracts exist but the operation "
            "returns placeholder data.  See description for what's missing."
        )
    elif skill.status == "broker_required":
        status_banner = (
            "\n\n  > **Status: broker_required** — install the matching "
            "broker under ``agent-harness/integrations/`` for end-to-end "
            "execution.  Without it the operation returns ``skipped=true``."
        )
    elif skill.status == "deprecated":
        status_banner = (
            "\n\n  > **Status: deprecated** — kept for backward compat; "
            "use the canonical replacement listed in the description."
        )
    return f"""---
name: {skill.skill_id}
status: active-functional
description: |
  {skill.hero}{status_banner}

  Triggers — invoke this skill when the user says any of:
  - {triggers_md}

  This is a **functional orchestrator** (Application Plane).  It composes
  the foundation primitives ({composes_md}) into a user-visible product
  flow.  Domain knowledge stays in the routed ``*-wiki`` skills; this
  layer is the cross-domain glue.
license: MIT
schema_version: {SKILL_STUB_VERSION}
omni_hub:
  layer: functional
  namespace: {skill.namespace}
  display_name: "{skill.display_name}"
  status: {skill.status}
  version: 0.1.0
  entrypoint: "{skill.entrypoint}"
  risk_level: {skill.risk_level}
  composes:
{_render_yaml_list(skill.composes)}
  required_permissions: []
  tags:
    - functional
    - orchestrator
    - {skill.status}
---

{SKILL_STUB_MARKER}

# {skill.display_name}

{skill.hero}

## What it composes

{chr(10).join(f"- `{c}` (foundation)" for c in skill.composes) or "_(none — top-level entry point)_"}

## Canonical CLI

```bash
PYTHONPATH=src python3 -m omni_hub.cli {skill.skill_id} [--help]
```

## Hard rules

- Functional skills MAY orchestrate multiple foundation calls.  They do
  NOT bypass the Proposal[T] gate for any mutating step.
- Trigger phrases are the user-visible product surface; tune via
  PreferenceStore + ``harness-compile-skill --functional`` (v0.40).

{skill.body_md or ""}

---

_Auto-generated stub.  Remove the ``{SKILL_STUB_MARKER}`` marker line to
opt out of regeneration._
"""


def _render_yaml_list(items: list[str]) -> str:
    if not items:
        return "    []"
    return "\n".join(f"    - {i}" for i in items)


def regenerate_foundation(
    skills_root: Path | str = ".agents/skills",
    *,
    workspace: Path | str = ".",
) -> list[StubAction]:
    skills_root_path = Path(workspace) / Path(skills_root)
    skills_root_path.mkdir(parents=True, exist_ok=True)
    actions: list[StubAction] = []
    for skill in FOUNDATION_SKILLS:
        skill_dir = skills_root_path / skill.skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)
        target = skill_dir / "SKILL.md"
        new_body = render_foundation_stub(skill)
        action = _materialise_one(target, new_body)
        actions.append(StubAction(
            skill_id=skill.skill_id, folder=skill_dir, action=action,
        ))
    return actions


def regenerate_functional(
    skills_root: Path | str = ".agents/skills",
    *,
    workspace: Path | str = ".",
) -> list[StubAction]:
    skills_root_path = Path(workspace) / Path(skills_root)
    skills_root_path.mkdir(parents=True, exist_ok=True)
    actions: list[StubAction] = []
    for skill in FUNCTIONAL_SKILLS:
        skill_dir = skills_root_path / skill.skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)
        target = skill_dir / "SKILL.md"
        new_body = render_functional_stub(skill)
        action = _materialise_one(target, new_body)
        actions.append(StubAction(
            skill_id=skill.skill_id, folder=skill_dir, action=action,
        ))
    return actions
