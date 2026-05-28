# Claude Code Entry

本项目的 agent 操作入口与 Codex 共用：**先读 `AGENTS.md`**，再读 `README.md` 和 `api-management/README.md`。
`AGENTS.md` 里的"工程硬约束"对 Claude Code 完全适用——4 条规则违反 = 改动必拒。

Claude Code 在本仓库里有两种身份：

1. **交互工程协作**：开发者打开 Claude Code 在终端里直接对话，按 `.agents/skills/<id>/SKILL.md` 加载的技能干活。
2. **headless worker**：launchd 调度起来的 `omni-hub worker --lane claude` 在后台跑，从 `TaskQueue` 拉任务，subprocess 调 `claude -p`，输出走 `Proposal[T]` 等人 review。

两种身份用同一套契约（TaskPacket / Artifact / Proposal），不要在主仓库新增"chat 模式" 的代码路径。

## API 网关

新增模型渠道、余额监控、协议转换、成本限制和路由策略，应进入 `api-management/metapi` 或 `api-management/ccLoad`，不要恢复主仓库里已经删除的旧 Provider Router / GUI。

## Knowledge Plane (v0.11–v0.16)

本仓库现在是 Karpathy LLM-Wiki 母模板：`vault/raw → vault/evidence → vault/wiki → .omni/claims.jsonl → .omni/preference → SKILL.md`。

写入 wiki / claims 的硬规则：

- **Agent 不允许直写 `vault/wiki/`**。所有变更经 `Proposal(kind=wiki_update)` 或 `Proposal(kind=lint_finding)`，人审通过后由 `wiki-apply-proposal` 落地。
- **claim 永不删除**。需要"废弃"一条 claim 时走 `wiki-supersede` 关 `t_valid_to` + 链 `superseded_by`（Graphiti bitemporal 模式）。
- **`vault/wiki/AGENTS.md` 是 schema 真源**。需要改 schema 时改 `src/omni_hub/knowledge_plane.py::WIKI_SCHEMA_BODY` 并 bump `WIKI_SCHEMA_VERSION`；`wiki-init` 会自动 refresh stale 文件。
- **域子 schema 在 `src/omni_hub/domain_schemas.py`**。改完 bump `DOMAIN_SCHEMA_VERSION`；12 个 `vault/wiki/domains/<x>/_schema.md` 自动 refresh。
- **搜索默认过滤过期/被替换页**。`wiki-search` / `claims-list` 默认跳过 `t_valid_to < now` 和 `review_state ∈ {rejected, superseded}`；audit 时显式 `--include-closed`。

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
