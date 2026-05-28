# Review Response — 2026-05-28

A focused review came in flagging the gap between "scaffolding done"
(what v0.30 / v0.36 actually delivered) and "production ready" (what
prior summaries implied).  This file tracks the response.

## Reviewer's findings — confirmed

| Severity | Finding | Status |
|---|---|---|
| P0 | Skill three-truth-source drift: 25 SKILL.md vs 5 in `skill-list`; `skill-sync` exists but the operator hasn't run `--apply`, and the auto-generated SKILL.md stubs lack the `omni_hub:` metadata block needed for a real SkillSpec | **fix this turn** |
| P1 | TaskRouter routes "分析 OpenAI 最新组织架构" → `ai_progress` (because `OpenAI` is a single high-weight keyword) instead of `enterprise` where the intent phrase actually belongs | **fix this turn** |
| P1 | Knowledge plane projection snapshots are null; claims.jsonl empty; reports all 0 — the closed loop (research → wiki → claims → projection) hasn't actually been exercised | doc honesty + dogfood (data work, not code work) |
| P1 | retrieve-doctor: 17 ok / 8 warn / 15 off; key-gated sources (Tushare/Crunchbase/Brave/XHS/...) need user configuration | doc honesty |
| P1 | Workflow / Projection language overhyped vs Temporal / Iceberg | **fix this turn** (doc) |
| P1 | Only CLI/MCP channels actually work end-to-end; Email needs OMNI_EMAIL_* env; Feishu/Discord are stubs pointing at agent-harness | already documented in stubs; **add to "completion" framing** |
| P2 | DSPy/Judge/A/B/CrossSkill pipeline exists but PreferenceStore has no real data yet | dogfood |
| P2 | SQLite ResourceWarning leaks (queue.py:587 etc.) — `with conn:` commits but doesn't close in Python 3.12+ | **fix this turn** |

The reviewer's prioritisation is correct: P0 first (skill merge), P1
next (router intent), P1 (doc honesty), P2 (mechanical cleanup),
then everything that needs real data.

## Acknowledgement

Several of the prior session summaries claimed "v0.30 production ready"
or "all 5 Planes done".  More accurate framing:

* **Control Plane** — ✅ contracts complete, lightweight implementations
  (NOT Temporal-grade durable execution; NOT Iceberg-grade table format)
* **Knowledge Plane** — ✅ scaffolding complete; **empty in practice**
  (no claims, no projection snapshots, no per-domain wiki content)
* **Skill Plane** — 🟡 19 stubs exist as SKILL.md text, but NOT as
  runtime SkillSpec until the v0.37 merge fix lands
* **Interface Plane** — 🟡 CLI + MCP genuinely work; Email needs env
  config; Feishu/Discord are documented stubs
* **Application Plane** — 🟡 9 apps exist as Python modules + contracts;
  end-to-end real-task closure not exercised on actual data

Completion: scaffolding **~95%**, real closed-loop on real data **~10%**.

## Fix order (this turn = v0.37)

1. **P0 — Skill Registry merge** (mechanical + small extension)
   * Extend `SkillSpec` to allow `kind=DOMAIN_WIKI` (no required entrypoint)
   * Rewrite `skill_stubs.py` to embed `omni_hub:` block with
     `kind: domain_wiki`, `entrypoint: operation:context_pack_build`,
     domain-tagged inputs
   * Regenerate all 19 `.agents/skills/<slug>-wiki/SKILL.md`
   * Run `skill-sync --apply` to populate `registry/skills.json`
   * Verify `omni-hub skill-list` returns ~25 skills

2. **P1 — TaskRouter intent classification**
   * Add `_INTENT_PHRASES` map (per domain, 2× weight) alongside the
     existing `_KEYWORDS` map (1× weight)
   * Enterprise intent: "组织架构", "公司分析", "值得加入", "护城河",
     "due diligence", "团队组成", "投融资", "招聘趋势"
   * Finance intent: "估值", "目标价", "进/出场点位", "回撤"
   * Verify "分析 OpenAI 最新组织架构 值得加入" → enterprise

3. **P2 — SQLite connection lifecycle hygiene**
   * Audit every `with self._connect() as conn:` in queue / proposals /
     workflow / projection / ab/store / users / scheduling /
     projects / event_log
   * Replace with `with contextlib.closing(self._connect()) as conn:`
   * Verify no more `ResourceWarning: unclosed database` in `make test`

4. **Doc honesty pass**
   * `docs/architecture-v0.19.md` + `v0.31.md` + AGENTS.md / CLAUDE.md
   * Reframe workflow / projection as **lightweight local
     implementations** that share contract shape with Temporal /
     Iceberg but not their semantics
   * Reframe "v0.30 production ready" claims as "scaffolding + contracts
     complete, real closed loops require dogfood"

## Deferred to v0.38+ (real data work)

These require dogfood, not more code:

* **Knowledge sedimentation closed loop** — needs actual wiki-ingest
  runs and human approvals to populate claims.jsonl and projection
  snapshots.  Mechanism is right; reservoir is empty.
* **Preference flywheel** — needs accepted/rejected spans accumulating
  over weeks of use before DSPy compile produces non-trivial output.
* **Channel real adapters** — Feishu/Discord need real SDK shim in
  `agent-harness/integrations/<channel>/` plus user-side auth setup.
* **Key-gated connectors** — Tushare/Crunchbase/Brave/XHS/Zhihu/Weibo
  need user API keys / broker self-host.

## SOTA invariants — adopt without copying the stack

Per the reviewer's table, the unifying advice is **adopt the invariants,
not the implementations**:

| Domain | Invariant to adopt | Stay away from |
|---|---|---|
| Workflow | event_history + replay + activity idempotency | Temporal cluster |
| Storage | SQLite WAL for single-machine, atomic pointer for snapshots | Iceberg / Delta |
| Context | minimum-viable high-signal context | giant retrieval dumps |
| Skill | versioned bundle + manifest, dynamic loading | one-mega-prompt |
| Memory | short/long/semantic/episodic/procedural separation | flat dump |
| Graph | bitemporal entity / event / fact model | full GraphRAG cluster |
| Observability | trace_id + span emit + metric | OTel collector + Loki |
| Policy | policy-as-code, data-driven rules | OPA daemon |

omni-hub v0.37 onward will be evaluated against this invariant table,
not against feature-parity with Temporal / Iceberg / OPA.
