# Operation 模型

每个写操作 = 一个 `OperationSpec` → 过 `OperationRunner` → 留 `OperationResult` + audit event。
这是项目里最稳的契约，所有 CLI 写都必须经过它（含 v0.7 新增的 task/propose/worker/optimizer 路径）。

## 数据形状

```python
@dataclass
class OperationSpec:
    name: str                           # builtins.py 里 register 过的 handler 名
    action: str = "run"
    connector: str = "local"
    payload: dict = {}                  # operation-specific input
    risk_level: RiskLevel = READ_ONLY   # L0..L4
    approval_required: bool | None = None  # 覆盖默认 policy
    sandbox_required: bool | None = None

@dataclass
class OperationResult:
    operation_id: str
    status: OperationStatus              # SUCCEEDED | FAILED | WAITING_APPROVAL | BLOCKED
    output: dict | None = None
    error: str | None = None
    policy_reason: str | None = None
    audit_id: str | None = None
```

## 运行时管线

```
CLI → OperationSpec
        ↓
   PolicyEngine.evaluate(spec)
        ├── allowed?
        ├── requires_approval?
        └── requires_sandbox?
        ↓ audit.record('policy_evaluated')
   if requires_approval and not approved:
        → status=WAITING_APPROVAL
   if requires_sandbox and not sandbox_enabled:
        → status=BLOCKED
        ↓
   registry.get(spec.name)  # builtins.OperationRegistry
        ↓ audit.record('operation_started')
   handler(spec) → output dict
        ↓ audit.record('operation_succeeded' or 'operation_failed')
   OperationResult
```

## 注册的 operations（截至 v0.7）

| 域 | operation | risk | 入口 |
|---|---|---|---|
| 摘要 | `summarize_text` | L0 | `summarize-text` |
| 写 markdown | `write_markdown` | L1 | `write-markdown` |
| 捕获 | `capture_url` | L1 | `capture-url` |
| Vault | `list_vault_notes` / `read_vault_note` | L0 | `vault-list` / `vault-read` |
| 知识提案 | `propose_knowledge` / `digest_proposal` | L1 | `propose-note` / `memory-digest-proposal` |
| 提案审批 | `list_proposals` / `approve_proposal` / `reject_proposal` | L0 / L1 / L1 | `propose-list` / `propose-approve` / `propose-reject` |
| 记忆 | `search_memory` / `memory_stats` | L0 | `memory-search` / `memory-stats` |
| Skill | `register_skill` / `list_skills` / `get_skill` / `disable_skill` / `recommend_skills` / `analyze_skills` | L1/L0/L0/L1/L0/L0 | `skill-*` |
| API 状态 | `api_management_status` | L0 | `api-management-status` |
| Harness 写 | `harness_preference_add` / `harness_compile` / `harness_redundancy_scan` | L1 | `harness-preference-add` / `harness-compile` / `harness-redundancy-scan` |
| Argilla 反馈 | `argilla_export_proposals` / `argilla_sync_feedback` | L1 | `argilla-export-proposals` / `argilla-sync-feedback` |
| 报表 | `build_daily_report` / `build_weekly_report` / `build_monthly_report` | L1 | `harness-report-*` |
| 任务队列 | `enqueue_task` / `claim_task` / `complete_task` / `fail_task` / `list_tasks` | L1/L1/L1/L1/L0 | `task-*` |
| 调度 | `schedule_tick` | L1 | `schedule-tick` |
| Optimizer | `optimizer_register_skill_version` / `optimizer_record_run` / `optimizer_list_skill_versions` / `optimizer_list_runs` | L1/L1/L0/L0 | `optimizer-*` |

## Policy 默认

```python
PolicyConfig(
    auto_approve_until = LOCAL_WRITE,        # L1 及以下自动批准
    require_approval_from = EXTERNAL_PUBLISH,
    require_sandbox_from = SANDBOX_EXECUTION,
)
```

- **L0 (READ_ONLY)** / **L1 (LOCAL_WRITE)**：默认自动批准、记 audit
- **L2 (EXTERNAL_SEND)**：检查 `external_write_allowlist`（`connector:action` 或单 `connector`），未在允许列表则强制 `requires_approval=True`
- **L3 (EXTERNAL_PUBLISH)** / **L4 (SANDBOX_EXECUTION)**：始终需 `--approve`；L4 还需 `sandbox_enabled=True`

CLI 可以用 `--approve` 显式批准（capture-url 当前不需要、write-markdown 需要）。
具体 risk 分级见 [permission-model.md](permission-model.md)。

## Audit 日志

```jsonl
{"event_type":"policy_evaluated","operation_id":"...","data":{"operation":{...},"decision":{...}},"event_id":"...","timestamp":"..."}
{"event_type":"operation_started","operation_id":"...","data":{"operation":{...},"decision":{...}},"event_id":"...","timestamp":"..."}
{"event_type":"operation_succeeded","operation_id":"...","data":{"operation":{...},"result":{...}},"event_id":"...","timestamp":"..."}
```

`audit_id` 在 `OperationResult` 里回填——CLI stdout 拿到的 `audit_id` 可以直接 `grep` audit jsonl。
v0.8 计划接 hash-chain（Hermes 模式），让 audit log 可校验不可篡改。

## 工程硬约束（重申）

- **新写操作必须先在 `src/omni_hub/builtins.py::build_default_registry` 注册**，再在 `cli/<area>.py` 暴露子命令。
- CLI handler 几乎都是 `return run_and_print(runner, OperationSpec(...))`——除非是 daemon 类（如 `worker`）才直接写 stdout。
- Worker daemon 内部对 `TaskQueue.complete/fail` 必须传 `claimed_by=worker_id` + `lease_epoch=task.lease_epoch`，否则下游 LLM 副作用无法 fence（详见 P0-1 lease-epoch 设计）。

## 扩展点

- 自定义 `PolicyConfig`：通过 `OperationRunner(registry, policy=PolicyEngine(custom_config))` 注入。
- 自定义 `AuditLogger`：默认 `.omni/audit/events.jsonl`，可替换。
- 自定义 sandbox：当前未实现具体 sandbox，`sandbox_enabled=False` 默认让 L4 操作直接 BLOCKED；后续接 OpenHands Docker sandbox。
