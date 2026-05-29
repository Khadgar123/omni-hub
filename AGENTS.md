# Agent Entry

Codex 或其他代码 agent 第一次进入仓库时，先读这个文件，再读 `README.md` 和 `api-management/README.md`。

## v0.37 状态实话 (2026-05-28 review 后)

**不要再用 "production ready" / "v1.0 触手可及" 措辞**。准确框架: scaffolding 95%,
closed-loop on real data 10%。

工程硬约束 #7 (新增,2026-05-28 review):

- WorkflowKernel / ProjectionRegistry **是 lightweight local 实现**,share contract shape with
  Temporal / Iceberg 但**不 share semantics**。文档若声称"Temporal-grade"或"Iceberg-grade"算违规。
- "完成度"必须区分 **scaffolding 完成度** (contracts + code paths) vs **closed-loop 完成度**
  (real data 已经流过整条管线)。前者高,后者低 — 不混淆。
- 写代码前先 `omni-hub skill-list | jq '.output.skills | length'` 确认运行时 skill 数 ≥ 文档声称的
  数;若不一致,先跑 `skill-sync --apply`。
- 跑全测试: `make test` 必须 **0 ResourceWarning + 0 失败**;有 warning 就修,不留作"后续清理"。

详见 [docs/review-2026-05-28-response.md](docs/review-2026-05-28-response.md)。


## API 管理原则

- 不要把 raw API key 写入仓库、README、docs、测试夹具或 compose 示例。
- 本地 API 管理已交给 `api-management/metapi` 和 `api-management/ccLoad` 两个 fork。
- Metapi 负责上游账号、余额、模型发现、成本/余额/使用率路由和告警。
- ccLoad 负责本地网关、协议转换、失败切换、令牌/RPM/成本限制和请求监控。
- 主仓库只保留最小状态检查；新增网关能力优先改对应 fork，不要恢复旧 Provider Router 或 GUI。
- 全项目当前默认 DeepSeek：配置声明在 `api-management/defaults.json`，真实 key 只允许写入 `local:omni-hub/api/deepseek/default`。

## 操作模式 (v0.19 5-Plane 重组)

本仓库是 **5-Plane 架构**，**不是聊天工具**:

- **Control Plane** — OperationRunner / TaskQueue / Proposal / Policy / Audit / WorkflowKernel / ProjectionRegistry
- **Knowledge Plane** — Acquisition (18 connectors) + Storage (vault + ClaimLedger + projections) + Internal Retrieval (FTS5 + Graph + Context-Pack)
- **Skill Plane** — Design Layer (DSPy 5-comp + Anthropic Skills) + Registry (19 vertical skills, see ``src/omni_hub/domain_schemas.py``) + Evolution Layer (GEPA + Preference + auto skill-sync)
- **Interface Plane** — CLI + MCP + Email (stdlib `imaplib`/`smtplib`) + Feishu/Discord stubs (real impl in `agent-harness/integrations/`)
- **Application Plane** — `ReportOrchestrator` (cross-skill 日/周/月报) + `TaskRouter` (对话任务路由器; LLM-free heuristic in v0.19, swap to LLM-as-Judge in v0.23)

详细架构见 [docs/architecture-v0.19.md](docs/architecture-v0.19.md)。

老的 3 层视图 (Control Plane + Worker Pool + Eval/Memory Flywheel) 仍然成立 — v0.19 是把它们重新组织成 5 个清晰的 Plane,旧契约 (TaskPacket / Artifact / Proposal[T]) 全部保留:

```
TaskPacket
  → AgentJob Queue（SQLite）
  → Worker lane（python / claude / codex / openhands）
  → Artifact
  → Proposal[T]（高风险任务）
  → human approve via propose-list / propose-approve
  → preference / compile / memory 飞轮
```

Codex / Claude Code 在这套架构里是 **headless worker**，不是项目本体。日常调度通过 launchd plist
(`scripts/launchd/*.plist`)，常驻进程是 `omni-hub worker --lane <lane>`。

## 工程硬约束

下列规则保证 policy + audit + Proposal 覆盖率不漏：违反 = PR 必拒(5 条规则,v0.11+ 起含 Knowledge Plane 写入)。

### 1. CLI 子命令与写操作

- **新增 CLI 子命令必须放在 `src/omni_hub/cli/<area>.py`**，按域分文件（capture / memory / skill / task / propose / worker / harness / reports / ...）。不允许新加 `if` 分支到任何根入口。每个域文件 export `register(subparsers)` + `COMMANDS` dict，`cli/__init__.py` 自动发现。
- **新增写操作必须先注册到 `src/omni_hub/builtins.py`** 并通过 `OperationRunner.run(OperationSpec(...))` 调用，让 policy + audit 全覆盖。**禁止**业务代码裸调 SQLite/写文件后再补审计。
- 读操作可以直接调底层函数，但若调用者是 CLI 子命令则仍推荐过 Runner 保持出入口一致。

### 2. Worker / Queue 流程

- 后台任务、定时任务、agent 调用 **必须经 `TaskQueue`**。
  - 入队走 `omni-hub task-enqueue --lane <python|claude|codex|...>` 或 `schedule-tick --period <daily|weekly|monthly>`。
  - 拉取走 `omni-hub worker --lane <lane>`（launchd KeepAlive 常驻）或一次性任务的 `--max-iterations 1`。
- **Agent worker (headless Claude / Codex) 的输出强制走 `Proposal[T]`**，不允许直接写 vault / memory / registry。`propose-list` → `propose-approve` 是唯一合规出口。
- 写操作风险等级超过 `LOCAL_WRITE` 的（`EXTERNAL_SEND` / `EXTERNAL_PUBLISH` / `SANDBOX_EXECUTION`）默认 `requires_approval=True`，policy 引擎会自动卡住。

### 3. 测试 + 模块登记

- 每个新模块必须有至少一份单测（`tests/test_<name>.py`），并通过 `make test`。
- 删除 / 重命名公共类必须同步：
  - `src/omni_hub/__init__.py` 的 `from ... import` 和 `__all__`
  - 所有调用方测试
- 文档漂移：触碰核心契约（TaskPacket / GenerationRecord / Artifact / Proposal）时同步更新 `docs/agent-system-development-plan.md` 中的契约段。

### 4. Fork / Submodule 流程

- 新增第三方 fork 必须**先**写进 `agent-harness/manifest.json::pending_forks`（带 upstream / role / next_step），**再**由 `scripts/add_pending_harness_forks.sh <id>` 转 submodule。
- 如果用户对上游仓库有协作权限（例如 `RipeMangoBox/ResearchFlow`、`RipeMangoBox/PaperBite`），可以登记为 `decision=upstream-direct` 并直接 pin 上游 gitlink，不要绕到个人 fork。
- **禁止**手动 `git submodule add` 跳过这条流程。理由：manifest 是 `make harness-status` 的源、fork 决策有 `decision_log` 留痕。

### 5. Knowledge Plane 写入

v0.11 起 `vault/wiki/` + `.omni/claims.jsonl` 是 Karpathy LLM-Wiki 母模板的真源。写入规则：

- **禁止 agent 直写 `vault/wiki/`**。所有页面变更走 `Proposal(kind="wiki_update")`（来自 `wiki-ingest` 或 `wiki-propose-research`），人审通过后由 `wiki-apply-proposal` 落地。`vault/wiki/log.md` 是唯一例外（append-only 审计,通过 `wiki-log` 公共接口）。
- **claim 永不删除**。需要废弃时调 `wiki-supersede --new <new_id> --old <old_id>` 关时间窗（写 `t_valid_to`）+ 链 `superseded_by`/`supersedes`。这是 Graphiti / Zep bitemporal 模式，满足 EU AI Act 风格的审计要求。
- **schema 文档是代码生成的**：`vault/wiki/AGENTS.md` 由 `src/omni_hub/knowledge_plane.py::WIKI_SCHEMA_BODY` 生成，12 个 `domains/<x>/_schema.md` 由 `src/omni_hub/domain_schemas.py::DOMAIN_SCHEMAS` 生成。改 schema = 改代码 + bump `*_SCHEMA_VERSION`,**不要直接编辑生成的文件**(stale 会被 `wiki-init` 自动覆盖)。
- **wiki-lint 六规则的域 override 写在 `DomainSchema.rule_overrides`**。新增/改 override = 改代码,不要在 finding 端 patch severity。
- **`.omni/preference/<domain>.jsonl` 是飞轮真源**。`wiki-apply-proposal` 自动 append `decision=accepted` 一条;不要在其他路径直写 preference,确保 `harness-compile` / `harness-compile-skill` 能稳定消费。

### 5.5 Eval Flywheel (v0.41+) 数据飞轮

`vault/evals/<domain>/v0.X/` 是 benchmark 真源 — capability / regression / calibration 三类 EvalCase JSONL。详见 [docs/eval-flywheel-v0.41.md](docs/eval-flywheel-v0.41.md)。

- **HR #11 — Eval pack 不可手编辑 v0.X**。一旦 freeze (写入 `vault/evals/<domain>/v0.X/`),只能新建 v0.X+1。`EvalStore.create_pack(existing version)` 主动 raise。审核失误的 case → 在 v0.X+1 修;原 v0.X 保留作历史回归。
- **HR #12 — Holdout 不进 git + 双因子访问**。`vault/evals/*/v*/holdout-private.jsonl` 加 `.gitignore`;v0.42 起 `EvalStore.list_cases(include_holdout=True)` 必须先设 `OMNI_EVAL_HOLDOUT=1` (二次确认门槛),否则 raise `HoldoutAccessDenied`。Burn (已被公开评分) 后强制 rotate,不允许重用同一 holdout 评 prod。
- **HR #13 — Graduation 必经 Proposal**。`PreferenceStore[domain].accepted_count >= 100` 触发候选 → `propose_pack_upgrade` emit `Proposal(kind=eval_pack_upgrade)` → 人审 → 写 v0.X+1。**不允许自动 promotion** (Anthropic 2026-01 + UC Berkeley 反 reward-hacking 共识)。
- **HR #14 — `eval_class` 必填**。每个 EvalCase 必须显式标 `capability | regression | calibration` (Anthropic 2026-01 taxonomy);capability 通过阈值 0.55,regression 0.85,calibration 0.70 (rubric)。混类 = 反模式。
- **HR #15 — case_id 必须 sha256(domain|class|span) 派生** (v0.42 加)。禁用 Python 内置 `hash()` (PYTHONHASHSEED 随机化); `evals.promote._stable_case_id()` 是唯一允许的生成路径,保证跨进程 idempotency。
- **HR #16 — EvalRunner 必走 SkillAdapter** (v0.42 加)。`run()` 默认调 `builtin_skill_adapters(workspace)` + `pick_adapter(case)` 拿真实 skill 输出;只在 `--echo-only` 显式 flag 下退回 echo。"评了 ground-truth 自己" 的反模式 (v0.41 全量回归全过) 不再允许。Write-class skill (`order-propose` / `calendar-add` / 等) 的 adapter 强制只 describe 不执行,防 eval 副作用污染。

### 5.6 Skill taxonomy 不变量 (v0.47 加,填补 #8/#9/#10 空缺)

机器校验见 `src/omni_hub/skill_audit.py::audit_skills`(report-mode lint,后续接入 `wiki-doctor` 作硬门)。

- **HR #8 — Skill 必须声明 layer**。每个 domain `*-wiki` skill 的 SKILL.md frontmatter 必须显式带 `status: active-domain`(不靠 id 后缀推断),reviewer / router 才能据此推理 always-on context tax。
- **HR #9 — Atomic identity / trigger 不重叠**。任意两个 domain skill 的 trigger 短语 Jaccard 重叠必须 < 0.30,否则 router 无法消歧(=反模式,直接拒)。
- **HR #10 — Domain skill 只 answer**。`*-wiki` 正文**不得**在可运行的 fenced 代码块里内联 retrieve/curate/write 动词(`wiki-ingest` / `wiki-apply` / `retrieve --persist` / `wiki-supersede` 等);那是 foundation/pipeline 的职责(CQRS 读写分离)。散文里引用可以,放进可执行块就是泄漏。

### 6. Interface + Application Plane (v0.19)

- **新 Channel adapter 必须实现 `omni_hub.channels.Channel` Protocol** (`listen` / `reply` / `health_check` / `shutdown`)。
- **重依赖 SDK 的 Channel** (lark-oapi / discord.py) 实现进 `agent-harness/integrations/<name>/`,主仓库只放 stub。Email channel 用 stdlib `imaplib`/`smtplib` 可以进主仓库。
- **Application Plane 不直接调 LLM**。`ReportOrchestrator` 是纯数据聚合; `TaskRouter` 是关键词启发式 → 推荐 OperationSpec。需要 LLM 生成时 enqueue claude/codex lane,走 `Proposal[T]`。
- **trace_id 必须跨 Channel + Application + Skill 全链路**。`InboundMessage.trace_id` → `OperationSpec.trace_id` → `OutboundMessage.trace_id` 三段都同值。
- **新域要走 DOMAIN_SCHEMAS**。增加 domain = 改 `src/omni_hub/domain_schemas.py` + 改 `retrieval/cascade.py` DEFAULT_DOMAIN_CASCADES + 改 `agent-harness/domain-profiles.json` + `bump DOMAIN_SCHEMA_VERSION`,跑 `omni-hub skill-stubs-sync` 自动生成 SKILL.md。

## 本地检查

```bash
make test                                                                # 全量测试
PYTHONPATH=src python3.12 -m omni_hub.cli api-management-status          # API 网关状态
PYTHONPATH=src python3.12 -m omni_hub.cli task-list                      # 任务队列状态
PYTHONPATH=src python3.12 -m omni_hub.cli propose-list --state pending   # 待审 proposal
docker compose --env-file api-management/env.example -f api-management/compose.yml config
```

调度安装 / 卸载：

```bash
make schedule-install-dry    # 看渲染出来的 plist，不安装
make schedule-install        # 写到 ~/Library/LaunchAgents/ 并 launchctl bootstrap
make schedule-uninstall      # 反向：bootout + 删除
```

Worker（手动一次性 smoke test）：

```bash
make worker-python           # 拉取 python lane 直至 idle 退出
make worker-claude           # 拉取 claude lane（需要 claude CLI 已安装）
make worker-codex            # 拉取 codex lane（需要 codex CLI 已安装）
```
