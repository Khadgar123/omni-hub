# Skill Taxonomy v0.38 — Foundation / Functional / Domain 三层

**写于 2026-05-28**。基于 2026 Q2 agent skill 文献综述
([Anthropic Skills repo](https://github.com/anthropics/skills),
[Scaling Coding Agents via Atomic Skills, arXiv 2604.05013](https://arxiv.org/abs/2604.05013),
[Letta HITL tools](https://docs.letta.com/guides/core-concepts/tools/human-in-the-loop/),
[OpenAI Practical Guide to Building Agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/),
[DSPy 3 + GEPA](https://dspy.ai/api/optimizers/GEPA/overview/),
LangGraph subgraph patterns)。

## 核心洞察 (从研究综述)

1. **Atomic in identity, rich in body**: 一个 skill 对应一个 task class,不要按 verb/source 拆。SKILL.md body 可以厚,但 identity 必须 atomic。
2. **8-12 always-on skill 是甜区**: 超过后 context tax 上涨,Claude 触发可靠性下降。
3. **三类边界**: tools (atomic primitives) < skills (reusable callable modules for goal class) < plans (one-time scaffolds)。omni-hub 的 OperationSpec ≈ tool;SKILL.md ≈ skill;ProjectLifecycle ≈ plan。
4. **Foundation 与 Domain 必须分离**: Anthropic 的 `pdf` (foundation) vs `internal-comms` (domain bundle) 镜像 omni-hub 的 `retrieve` vs `research-wiki`。
5. **每个 mutating skill 必经 Proposal[T]**: Letta HITL tool gating 直接对应,omni-hub 已经强制。

## 现状盘点 (v0.37 后)

`registry/skills.json` 现有 30 skill,分布:

| Layer | 数量 | 例子 |
|---|---|---|
| Domain wiki | 19 | `research-wiki`, `enterprise-wiki`, ... |
| Foundation - knowledge access | 5 | `retrieve`, `retrieve-cascade`, `retrieve-grade-and-fuse`, `retrieve-evidence-pin`, `retrieve-domain-source-map` |
| Utility | 1 | `api-management-status` |
| Legacy (命名不一致) | 5 | `memory-digest`, `memory-search`, `skill-registry`, `url-capture`, `vault-proposal` |

**缺口**: ~80 个 operation 在 builtins.py 注册但 **没有对应 SKILL.md**,Claude / Codex 发现不到。Foundation 的 knowledge-update / eval / workflow tier 完全空,Functional 层只有 19 wiki domain 是可见的。

## v0.38 三层结构

```
┌──────────────────────────────────────────────────────────────────┐
│ Functional Skills (8-12) — orchestrators, cross-domain            │
│   report-build · chat-route · inbox-route · project-plan         │
│   pptx-build · calendar-add · schedule-plan · task-add           │
│   finance-screen · order-propose · meta-cross-skill-scan         │
├──────────────────────────────────────────────────────────────────┤
│ Domain Skills (19, namespace-loaded by task_router)               │
│   research-wiki · engineering-wiki · ai-progress-wiki            │
│   enterprise-wiki · finance-wiki · us-policy-wiki · cn-policy-wiki│
│   fitness-wellness-wiki · cooking-wiki · travel-wiki ...         │
├──────────────────────────────────────────────────────────────────┤
│ Foundation Skills (15-20) — always-on primitives                  │
│  Knowledge Access     · retrieve · context-pack · wiki-search    │
│                       · claims-show · memory-search              │
│  Knowledge Update     · wiki-ingest · wiki-propose-research      │
│                       · wiki-apply · wiki-supersede              │
│                       · wiki-lint · wiki-dream                   │
│  Eval                 · judge-evaluate · ab-test · harness-compile│
│  Workflow             · propose-approve · propose-reject         │
│  Channel              · channel-list · app-report-build          │
└──────────────────────────────────────────────────────────────────┘
```

## 各层职责 + 冲突分析

### Foundation 层 (always-on,~17 个)

**职责**: 每次会话开始时 Claude / Codex 一定看到。不携带 domain 知识,**只携带"如何做某类操作"**。

**Knowledge Access (5)**
- `retrieve` — 联邦检索 router (已有,SOTA confirmed: 不要拆 `retrieve-arxiv` 等;那是 connector 不是 skill)
- `context-pack` (新) — 把 wiki + research-kb 装成 tier-bounded 上下文包 [合并 `context_pack_build` operation]
- `wiki-search` (新) — 查 vault/wiki (FTS5 + substring fallback)
- `claims-show` (新) — 查 ClaimLedger
- `memory-search` (已有) — 查 archival memory

**Knowledge Update (6,全部 Proposal-gated)**
- `wiki-ingest` (新) — 检索证据 → vault/raw 沉淀 + 一份 `wiki_update` Proposal
- `wiki-propose-research` (新) — 选 ResearchFlow / PaperBite 单条作为 Proposal
- `wiki-apply` (新) — 把已 approved 的 Proposal 落地写 wiki + claims + FTS5 reindex
- `wiki-supersede` (新) — bitemporal 关时间窗 + 链 superseded_by
- `wiki-lint` (新) — 8 规则扫描,emit `lint_finding` Proposal
- `wiki-dream` (新) — 离线 consolidation pass (Anthropic Dreaming 模式)

**Eval (3)**
- `judge-evaluate` (新) — HeuristicJudge / LLMJudge 评 candidate
- `ab-test` (新) — pairs variant A/B,Judge composite delta,SQLite 持久化
- `harness-compile-skill` (新) — PreferenceStore → SKILL.md body 编译

**Workflow (2)**
- `propose-approve` (新) — 人审通过一个 Proposal[T]
- `propose-reject` (新) — 拒绝

**Channel/Reports (1,因为 channel-* 是 IO 不是 cognitive,常用聚合在一个)**
- `app-report-build` (新) — 跨 skill 日/周/月报

合计 **17 个 foundation skill**,符合 SOTA 8-12 + 5 (knowledge-update is genuinely separate) 的甜区。

### Domain 层 (19,task-routed)

不变,继续 19 个 wiki domain (research / engineering / finance / ... )。Task_router 永远只激活 1 个,所以 always-on context tax = 0。

### Functional 层 (11,orchestrators)

**职责**: 跨 domain + 跨 foundation 的 application orchestrator。

| Skill | 现状 | Composes |
|---|---|---|
| `chat-route` (新 SKILL.md) | operation `app_route_task` 已有 | + `context-pack` + 选 domain |
| `inbox-route` (新 SKILL.md + builtin) | 类已有 (`ForwardedContentRouter`),无 operation | URL 检测 → `capture_url`; .ics → `calendar-add`; 任务语 → `task-add` |
| `project-plan` (新 SKILL.md + builtin) | `ProjectStore` 已有 | + `task-enqueue claude` planner |
| `pptx-build` (新 SKILL.md + builtin) | `StubPPTXBuilder` 已有 | + agent-harness/integrations/pptx broker |
| `calendar-add` (新 SKILL.md + builtin) | `CalendarStore` 已有 | iCal write |
| `schedule-plan` (新 SKILL.md + builtin) | `TimeBlockPlanner` 已有 | + `task-list` + `calendar-list` |
| `task-add` (新 SKILL.md + builtin) | `PersonalTaskStore` 已有 | SQLite write |
| `finance-screen` (新 SKILL.md + builtin) | `FinanceAnalyst` 已有 | + `retrieve --domain finance` |
| `order-propose` (新 SKILL.md + builtin) | `OrderIntent` + `risk_check` 已有 | + `Proposal(order_intent)` |
| `meta-cross-skill-scan` (新 SKILL.md) | operation `meta_cross_skill_scan` 已有 | + PreferenceStore 全域扫描 |
| `app-report-build` | (已在 Foundation 层) | |

合计 **10 个 functional skill** (排除已在 Foundation 的 report-build)。

## 冲突 + 合并决策 (基于 SOTA brief)

### ✅ 已对的 (保持)

1. **`retrieve` family**: 1 个 router (`retrieve`) + 4 stage skills (`retrieve-cascade`/`-grade-and-fuse`/`-evidence-pin`/`-domain-source-map`)。SOTA: 这是"atomic skill in identity, namespaced stages"模式,**不要折成单一 mega-retrieve,也不要再细到 `retrieve-arxiv`**。
2. **19 domain wiki** namespace 化,task_router 一次激活一个。SOTA: 这是 LangGraph supervisor + per-domain subgraph 模式。
3. **Proposal[T] 写屏障**: 每个 mutating skill 都过 Proposal。SOTA: Letta HITL tool gating 直接对应。

### 🟡 需要合并 / refactor

4. **`memory-digest` + `vault-proposal`**: 都是 v0.7 时代的 `Proposal(kind=knowledge)` 写入路径。v0.11 之后被 `wiki-ingest` + `wiki-apply` 取代。**Deprecate 这两个** (mark status=deprecated 在 registry,SKILL.md 加 deprecated header)。
5. **`skill-registry` skill**: 其 entrypoint 是 `register_skill`,但现在更 idiomatic 的入口是 `skill-sync` (v0.37) + `skill-stubs-sync`。**Rename to `skill-sync` + 更新 entrypoint**,删旧 `skill-registry`。
6. **`url-capture`**: 已有 operation `capture_url`,但 SKILL.md 缺。**改成 foundation skill,加 SKILL.md** (因为转发内容路由依赖它)。

### ❌ 不要合并 (SOTA 警告)

7. **不要把 `wiki-ingest` + `wiki-apply` + `wiki-supersede` 折成"knowledge-update"**: 不同 schema 写操作,不同 review 表面,折成一个会触发 SOTA brief 列出的 **skill-collapse 反模式** (description 变模糊,trigger 触发率下降)。保持 3 个独立 skill,namespace 在 SKILL.md 描述里互引。
8. **不要把 `judge-evaluate` + `ab-test` + `cross-skill-scan` 折成 "eval"**: 同样 — bundle 在 `omni_hub/judge/`,`omni_hub/ab/`,`omni_hub/meta/` 模块层 OK,但 skill 层各自独立,各自 trigger phrase 不同。
9. **不要把 19 domain wiki 折成 1 个 "domain-wiki"**: 各自 trigger phrase / 各自 cascade source / 各自 lint override。

## v0.38 实施动作 (concrete)

### 1. 扩展 `skill_stubs.py` 支持 3 层 stub 生成

旧版只跑 `regenerate_all(domain wikis)`,新版加 `regenerate_foundation()` + `regenerate_functional()`。Stub 模板按层结构略有不同:

- **Foundation stub**: trigger 短,description 强调"this is the canonical path for X",entrypoint 指向具体 builtin operation。
- **Functional stub**: trigger 包含真实用户句式,description 列 composes 哪些 foundation。
- **Domain stub**: 不变 (v0.37 已经稳定)。

### 2. 新增 functional 层的 builtin operation

| Operation | 当前状态 | 动作 |
|---|---|---|
| `inbox_route` | 类已有,未注册 | 注册 builtin |
| `project_plan` | 类已有,未注册 | 注册 builtin |
| `pptx_build` | Protocol 已有,未注册 | 注册 builtin (subprocess dispatch 到 broker) |
| `calendar_add` | 类已有,未注册 | 注册 builtin |
| `schedule_plan` | 类已有,未注册 | 注册 builtin |
| `task_add` | 类已有,未注册 | 注册 builtin |
| `finance_screen` | 类已有,未注册 | 注册 builtin (返回空列表的存根) |
| `order_propose` | 类已有,未注册 | 注册 builtin (emit `Proposal(order_intent)`) |

### 3. 修 registry/skills.json drift

- 标记 `memory-digest` / `vault-proposal` 为 `status=deprecated`
- 把 `skill-registry` skill_id 重命名为 `skill-sync` + 更新 entrypoint 到 `operation:skill_sync`
- 给 `url-capture` 补写 SKILL.md

### 4. 验证最终 skill-list 数

预期: 17 foundation + 19 domain + 11 functional - 1 (report-build 双重计数) + 2 legacy (`api-management-status` + `url-capture` 保留) = **48 skill**。其中:
- `status=active` 的常用 skill 大约 **45**
- `status=deprecated` 的 **2** (memory-digest, vault-proposal)

8-12 always-on 由 task_router 控制 — 19 domain 永远只有 1 激活,functional 11 个只激活相关的几个 → 实际 always-on **~5-7 个 foundation core** + **1 routed domain** + **0-3 functional based on task** = **6-11 skill** 实际加载,正好在甜区。

## v0.38 工程不变量 (新增)

继承 v0.10-v0.37 全部 + 新增:

- **HR #8 — Skill 三层结构**: 每个新 SKILL.md 必须明确标 `layer: foundation | functional | domain` (在 `omni_hub:` 块)。Reviewer 通过 layer 字段验收。
- **HR #9 — Atomic identity**: SKILL.md 描述里不要试图覆盖多个 task class。1 个 skill = 1 个 task class,trigger 不重叠。Reviewer 用 trigger 重叠度 > 30% 拒。
- **HR #10 — Foundation 不含 domain 知识**: foundation 层 SKILL.md 不出现具体 domain 名 (research / finance / ...),否则换 functional 或 domain。

## 路线图后续

- **v0.38** (本轮): 三层 taxonomy + stub generator 三层 + 缺失 functional builtin 注册 + skill-list 显示分层
- **v0.39**: SKILL.md body 真实内容 (现在 stub 是模板,实际 trigger phrase 还需要校准)
- **v0.40**: harness-compile-skill 跑全 19 domain,从 PreferenceStore 编译进 SKILL.md body
- **v0.41+**: dogfood 真实数据,看哪些 functional 没人触发 (剪掉),哪些 domain 经常被错路由 (intent 短语调整)

## 参考

来自 v0.38 SOTA brief (`docs/skill-taxonomy-research-2026-05-28.md` archive 在 commit 历史):
- [anthropics/skills](https://github.com/anthropics/skills)
- [Anthropic engineering: Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Scaling Coding Agents via Atomic Skills (arXiv 2604.05013)](https://arxiv.org/abs/2604.05013)
- [Letta HITL tools](https://docs.letta.com/guides/core-concepts/tools/human-in-the-loop/)
- [OpenAI Practical Guide to Building Agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)
- [LangGraph subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)
- [Claude Code Skills practical guide 2026 (Nimbalyst)](https://nimbalyst.com/blog/claude-code-skills-guide/)
- [calmops AI Agent Skills 2026 guide](https://calmops.com/ai/ai-agent-skills-complete-guide-2026/)
