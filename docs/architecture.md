# 架构（v0.7 / v0.8 audit-fix）

万象中枢是 **single-user local Control Plane** ——不是聊天工具、不是 SaaS、不是聊天框架。
项目本体是契约 + 队列 + 审计 + 提案 + 评测飞轮。Codex / Claude Code / OpenHands 是
**可替换的 worker**，不是核心。

## 5 层总览

```
┌──────────────────────────────────────────────────────────────────────┐
│ CLI Layer · src/omni_hub/cli/<area>.py                                │
│   capture / memory / skill / propose / task / worker /                │
│   harness / reports / optimizer / api_management / policy             │
└──────────────────┬────────────────────────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────────────────────────┐
│ Control Plane                                                         │
│   OperationRunner  ← policy + audit 通道                              │
│   TaskQueue        ← SQLite WAL + 原子 claim + lease_epoch fencing    │
│   ProposalStore    ← 统一 Proposal[T]（kind/state/payload）           │
│   AuditLogger      ← .omni/audit/events.jsonl                         │
└──────────────────┬────────────────────────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────────────────────────┐
│ Worker Pool · src/omni_hub/workers/                                   │
│   BuiltinAdapter (python)  – 直接走 OperationRunner                   │
│   ClaudeAdapter            – subprocess claude -p --bare              │
│   CodexAdapter             – subprocess codex exec --json --sandbox   │
│   OpenHandsAdapter (v0.8) – Docker REST 调用                          │
│   _GATED_LANES = {claude, codex, openhands}                           │
│         成功 artifact 必经 ProposalStore，等人 approve                │
└──────────────────┬────────────────────────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────────────────────────┐
│ Eval / Memory Flywheel · src/omni_hub/harness/ + reports/             │
│   TaskPacket → ensemble (ccLoad fan-out) → judge (multi + bias audit) │
│     → preference (Argilla schema) → DSPy compile → daily/weekly/monthly│
│   MemoryStore   – documents/entities/relations（SQLite）              │
│   reports/      – build_daily / build_weekly / build_monthly          │
└──────────────────┬────────────────────────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────────────────────────┐
│ Optimizer Layer · src/omni_hub/optimizer/                             │
│   DatasetSplit, EvalGate, OptimizationRun, OptimizerStore,            │
│   SkillVersion – DSPy / GEPA-ready skill evolution contracts          │
└───────────────────────────────────────────────────────────────────────┘
```

辅助层（项目编排但不在主仓核心）：

```
agent-harness/    pinned modules（SWE-agent / promptfoo / argilla / graphiti）
                  + RipeMangoBox upstream modules（ResearchFlow / PaperBite）
                  + 3 pending（DSPy / OpenHands / Opik，由 manifest.json 管理）
api-management/   metapi（上游账号/余额/路由）+ ccLoad（本地协议转换/限流）
                  Docker compose 启停
.agents/skills/   业务 skill 入口（SKILL.md），Claude Code / Codex 经 symlink 读
vault/            用户的 Markdown / Obsidian 知识库
scripts/launchd/  macOS launchd plist 模板（daily/weekly/monthly/worker）
```

## 核心契约

| 名字 | 文件 | 作用 |
|---|---|---|
| `OperationSpec` / `OperationResult` | [models.py](../src/omni_hub/models.py) | CLI ↔ Runner 的输入/输出 |
| `RiskLevel` | [models.py](../src/omni_hub/models.py) | L0=READ_ONLY / L1=LOCAL_WRITE / L2=EXTERNAL_SEND / L3=EXTERNAL_PUBLISH / L4=SANDBOX_EXECUTION |
| `TaskPacket` | [harness/models.py](../src/omni_hub/harness/models.py) | 飞轮的输入契约（domain/sources/constraints/rubric） |
| `GenerationRecord` | [harness/models.py](../src/omni_hub/harness/models.py) | 飞轮的输出契约（N 候选 + judge + 人审 + regression） |
| `Task` | [queue.py](../src/omni_hub/queue.py) | 队列行，含 `lease_epoch` fencing token |
| `Proposal` | [proposals.py](../src/omni_hub/proposals.py) | 统一审批原语（kind ∈ knowledge/duplicate/stale/conflict/low_signal/generation） |
| `Artifact` | [workers/base.py](../src/omni_hub/workers/base.py) | worker 输出包装（kind: generation/report/patch/scan_result/text） |

## 通用调用链

**人触发**：

```
   人 → CLI → OperationSpec → OperationRunner → policy.evaluate
                                              → audit.record(operation_evaluated)
                                              → handler(spec) → OperationResult
                                              → audit.record(operation_succeeded)
   stdout: { operation_id, status, output, audit_id }
```

**launchd 后台触发**：

```
   launchd → schedule-tick --period daily → TaskQueue.enqueue × N
                                          (idempotency_key 防重)
   omni-hub worker --lane python (常驻 KeepAlive)
        → queue.claim()  (atomic + 增 lease_epoch)
        → BuiltinAdapter.run(task)
        → queue.complete(claimed_by, lease_epoch)
```

**Agent worker 触发**（claude/codex/openhands lane）：

```
   人或调度 → task-enqueue --lane claude --packet-json '{...}'
   omni-hub worker --lane claude
        → ClaudeAdapter.run(task) → subprocess claude -p --bare ...
        → 成功 artifact → ProposalStore.store(Proposal(kind="generation"))
        → queue.complete(...)
   人 → propose-list --kind generation --state pending → propose-approve --id <pid>
```

## 工程硬约束（详见 [AGENTS.md](../AGENTS.md)）

1. 新 CLI 子命令必须在 `src/omni_hub/cli/<area>.py`，写操作必须过 `OperationRunner`。
2. 后台任务过 `TaskQueue`；agent worker 成功输出必经 `Proposal[T]`，不允许直接写 vault/memory。
3. 删 / 改公共类必须同步 `__init__.py` 的 export 和测试。
4. 新第三方 fork 必须先入 `agent-harness/manifest.json::pending_forks`，由 `scripts/add_pending_harness_forks.sh` 转 submodule；用户有上游协作权限的一方模块可登记为 `upstream-direct`，直接 pin 上游 gitlink。

## 设计文档导航

| 主题 | 文件 |
|---|---|
| Operation 模型 | [operation-model.md](operation-model.md) |
| 权限与风险 | [permission-model.md](permission-model.md) |
| 提案层（Proposal[T]）| [proposal-model.md](proposal-model.md) |
| 记忆层（MemoryStore + 三层 memory）| [memory-model.md](memory-model.md) |
| Skill Registry（三真源问题）| [skill-registry.md](skill-registry.md) |
| 推荐技术栈 | [recommended-stack.md](recommended-stack.md) |
| 自进化 harness | [self-evolution-harness.md](self-evolution-harness.md) |
| Agent 系统开发设计 | [agent-system-development-plan.md](agent-system-development-plan.md) |
| Roadmap | [roadmap.md](roadmap.md) |
