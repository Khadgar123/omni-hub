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


SKILL_STUB_MARKER = "<!-- omni-skill-stub: v0.19 -->"
SKILL_STUB_VERSION = "v0.19"


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
    if SKILL_STUB_MARKER not in existing:
        # Hand-edited — leave it alone.
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
  Proposal[T] (see "Write boundary" below).
license: MIT
schema_version: {SKILL_STUB_VERSION}
---

{SKILL_STUB_MARKER}

# {schema.display_name} — Wiki Domain Skill

This is the **{domain_slug}** domain skill, auto-generated from
`src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS["{domain_slug}"]`.

> {schema.position}

## When to use

Triggers (subset):

- {triggers_md}

## Reading

```bash
# Targeted query in this domain
PYTHONPATH=src python3 -m omni_hub.cli wiki-search \\
  --query "..." --backend fts5

# Build a context pack (progressive disclosure: minimal / standard / expanded)
PYTHONPATH=src python3 -m omni_hub.cli context-pack-build \\
  --query "..." --domain {domain_slug} --tier standard

# Inspect the domain's GraphRAG community structure (v0.18-J)
PYTHONPATH=src python3 -m omni_hub.cli wiki-graph \\
  --node <canonical_id_or_slug>
```

## Ingesting new evidence

```bash
# 1) Run the federated retrieval cascade for this domain
PYTHONPATH=src python3 -m omni_hub.cli retrieve \\
  --query "..." --domain {domain_slug} --persist-evidence

# 2) Bridge the retrieval evidence into a wiki_update Proposal
PYTHONPATH=src python3 -m omni_hub.cli wiki-ingest \\
  --run-id <run_id> --domain {domain_slug}

# 3) Human approves the Proposal
PYTHONPATH=src python3 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3 -m omni_hub.cli propose-approve --id <pid>

# 4) Land the approved Proposal — writes vault/wiki/domains/{schema.folder}/...
PYTHONPATH=src python3 -m omni_hub.cli wiki-apply-proposal --proposal <pid>
```

## Write boundary (hard rule from AGENTS.md §5)

**Agents propose, humans approve.**  This skill MAY NOT write to
`vault/wiki/domains/{schema.folder}/` directly.  All page changes go
through `Proposal(kind=wiki_update)`.  All claim retirements go
through `wiki-supersede` (bitemporal window close, never delete).

## Domain-specific lint hints

{chr(10).join(f"- {hint}" for hint in schema.lint_hints) if schema.lint_hints else "- _(no domain-specific hints — uses global rules)_"}

### Severity overrides

{rule_overrides_md}

## Required frontmatter on new pages

{_render_frontmatter_block(schema)}

## Eval metric (Skill Evolution Layer)

Preference records for this skill land at
`.omni/preference/{domain_slug}.jsonl`.  `harness-compile-skill --domain
{domain_slug}` reads them weekly (see launchd weekly schedule) and
proposes prompt updates as new versions of this SKILL.md body.

---

_Auto-generated stub.  Hand-editing is supported — remove the
`{SKILL_STUB_MARKER}` marker line to opt out of future regenerations._
"""


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
    "StubAction",
    "regenerate_all",
    "render_skill_stub",
]
