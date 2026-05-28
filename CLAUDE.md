# Claude Code Entry

本项目的 agent 操作入口与 Codex 共用：**先读 `AGENTS.md`**，再读 `README.md` 和 `api-management/README.md`。
`AGENTS.md` 里的"工程硬约束"对 Claude Code 完全适用——4 条规则违反 = 改动必拒。

## v0.37 状态实话 (2026-05-28 review 后)

不要再说 "v0.30 production ready" — 准确说法：**scaffolding 95%,closed-loop on real data 10%**。
读 [docs/review-2026-05-28-response.md](docs/review-2026-05-28-response.md) 了解详情。

- Control / Knowledge / Skill / Interface / Application 五个 Plane 的**契约都到位**
- WorkflowKernel = "lightweight local replayable state machine",**不是 Temporal-grade durable execution**
- ProjectionRegistry = "atomic pointer + rollback",**不是 Iceberg-grade table format**
- 19 个 vertical-skill SKILL.md 存在 + registry/skills.json 已合并 (v0.37 修了 三真源 drift)
- 真实数据闭环还没跑过 — vault/wiki / claims.jsonl / PreferenceStore 都是空的,等 dogfood 灌入

Claude Code 在本仓库里有两种身份：

1. **交互工程协作**：开发者打开 Claude Code 在终端里直接对话，按 `.agents/skills/<id>/SKILL.md` 加载的技能干活。
2. **headless worker**：launchd 调度起来的 `omni-hub worker --lane claude` 在后台跑，从 `TaskQueue` 拉任务，subprocess 调 `claude -p`，输出走 `Proposal[T]` 等人 review。

两种身份用同一套契约（TaskPacket / Artifact / Proposal），不要在主仓库新增"chat 模式" 的代码路径。

## API 网关

新增模型渠道、余额监控、协议转换、成本限制和路由策略，应进入 `api-management/metapi` 或 `api-management/ccLoad`，不要恢复主仓库里已经删除的旧 Provider Router / GUI。

## 5-Plane 架构 (v0.19)

v0.19 重组成 5 个 Plane,详见 [docs/architecture-v0.19.md](docs/architecture-v0.19.md):

```
Application Plane  · 日/周/月报 (ReportOrchestrator) + 对话路由 (TaskRouter)
Interface Plane    · CLI · MCP · Email(stdlib) · Feishu/Discord(agent-harness/)
Skill Plane        · 19 vertical skills + DSPy 5-comp + GEPA evolution
Knowledge Plane    · 18 connectors + ClaimLedger + FTS5/Graph/Context-Pack
Control Plane      · OperationRunner + TaskQueue + Proposal + WorkflowKernel
```

19 个 vertical-skill domains (`src/omni_hub/domain_schemas.py`):
research / engineering / photography / fashion / chat_relationships / finance /
us_policy / cn_policy / international_relations / ai_progress / agent_systems /
social_en / social_zh / **meta** / **fitness_wellness** / **cooking** /
**travel** / **marketing** / **enterprise** (粗体为 v0.19 新增,policy 拆 us/cn 也是 v0.19)。

每个 domain 对应 4 处契约:
- `vault/wiki/domains/<slug>/_schema.md` — 自动生成自 `DOMAIN_SCHEMAS`
- `.agents/skills/<slug>-wiki/SKILL.md` — 跑 `omni-hub skill-stubs-sync` 生成
- `agent-harness/domain-profiles.json::<slug>` — TaskPacket 模板
- `src/omni_hub/retrieval/cascade.py::DEFAULT_DOMAIN_CASCADES[<slug>]`

## Knowledge Plane (v0.11–v0.18)

本仓库是 Karpathy LLM-Wiki 母模板：`vault/raw → vault/evidence → vault/wiki → .omni/claims.jsonl → .omni/preference → SKILL.md`。

写入 wiki / claims 的硬规则：

- **Agent 不允许直写 `vault/wiki/`**。所有变更经 `Proposal(kind=wiki_update)` 或 `Proposal(kind=lint_finding)`，人审通过后由 `wiki-apply-proposal` 落地。
- **claim 永不删除**。需要"废弃"一条 claim 时走 `wiki-supersede` 关 `t_valid_to` + 链 `superseded_by`（Graphiti bitemporal 模式）。
- **`vault/wiki/AGENTS.md` 是 schema 真源**。需要改 schema 时改 `src/omni_hub/knowledge_plane.py::WIKI_SCHEMA_BODY` 并 bump `WIKI_SCHEMA_VERSION`；`wiki-init` 会自动 refresh stale 文件。
- **域子 schema 在 `src/omni_hub/domain_schemas.py`**。改完 bump `DOMAIN_SCHEMA_VERSION`；19 个 `vault/wiki/domains/<x>/_schema.md` 自动 refresh。
- **搜索默认过滤过期/被替换页**。`wiki-search` / `claims-list` 默认跳过 `t_valid_to < now` 和 `review_state ∈ {rejected, superseded}`；audit 时显式 `--include-closed`。

## Interface + Application Plane (v0.19-v0.27)

```bash
# Interface 健康检查
omni-hub channel-list                                # 列 5 个 channel + health
omni-hub channel-health --name email                 # 单 channel 详细诊断

# Application 对话路由 (LLM-free heuristic, v0.19 + v0.27 history bias)
omni-hub app-route-task --query "今晚做什么菜"          # → cooking skill + recommended op

# Application 跨技能报告 (纯数据聚合,无 LLM)
omni-hub app-report-build --period daily   --persist
omni-hub app-report-build --period weekly  --persist --narrate    # v0.26: enqueue claude task

# Skill stubs 同步 (改完 DOMAIN_SCHEMAS 后跑)
omni-hub skill-stubs-sync                            # 19 个 SKILL.md → .agents/skills/
```

## Eval + Evolution Plane (v0.23-v0.29)

```bash
# Judge LLM framework (v0.23)
omni-hub judge-list                                  # heuristic 总可用; llm 需要 ccLoad 或 ANTHROPIC_API_KEY
omni-hub judge-evaluate --domain research \
  --candidate "ACE 演化 context [1]..." --judge heuristic

# Cross-skill transfer (v0.28 meta skill)
omni-hub meta-cross-skill-scan --signal-threshold 0.4 --min-strong-domains 3
# → 19 域 PreferenceStore 共同模式 → CrossSkillFinding 列表 → 人审促成 Proposal

# A/B test framework (v0.29)
omni-hub ab-test --domain research --candidate-a "..." --candidate-b "..." --judge llm
omni-hub ab-list   --domain research --limit 20
omni-hub ab-stats  --domain research                 # win-rate aggregate
omni-hub ab-show   --id <run_id>
```

常用入口（按生命周期）：

```bash
# Ingest
omni-hub retrieve --query "..." --persist-evidence
omni-hub wiki-ingest --run-id <id> --domain <X>

# Apply (人审后)
omni-hub propose-list --kind wiki_update --state pending
omni-hub propose-approve --id <pid>
omni-hub wiki-apply-proposal --proposal <pid>   # 自动写 PreferenceRecord + FTS5 reindex

# Query
omni-hub wiki-search --query "..."              # 默认 FTS5,回退 substring
omni-hub claims-stats / claims-list / claims-show
omni-hub context-pack-build --tier minimal|standard|expanded

# Lint (daily 自动跑)
omni-hub wiki-lint --persist
omni-hub wiki-conflict-resolve --proposal <id> --decision supersede

# 飞轮闭环
omni-hub harness-compile --domain <X>            # 编译 system_prompt.md
omni-hub harness-compile-skill --domain <X>      # 编译 .agents/skills/<X>-wiki/SKILL.md
```
