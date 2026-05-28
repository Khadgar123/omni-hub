# Agent Entry

Codex 或其他代码 agent 第一次进入仓库时，先读这个文件，再读 `README.md` 和 `api-management/README.md`。

## API 管理原则

- 不要把 raw API key 写入仓库、README、docs、测试夹具或 compose 示例。
- 本地 API 管理已交给 `api-management/metapi` 和 `api-management/ccLoad` 两个 fork。
- Metapi 负责上游账号、余额、模型发现、成本/余额/使用率路由和告警。
- ccLoad 负责本地网关、协议转换、失败切换、令牌/RPM/成本限制和请求监控。
- 主仓库只保留最小状态检查；新增网关能力优先改对应 fork，不要恢复旧 Provider Router 或 GUI。
- 全项目当前默认 DeepSeek：配置声明在 `api-management/defaults.json`，真实 key 只允许写入 `local:omni-hub/api/deepseek/default`。

## 操作模式

本仓库是 **Control Plane + Worker Pool + Eval/Memory Flywheel** 三层架构，**不是聊天工具**：

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

下列规则保证 policy + audit + Proposal 覆盖率不漏：违反 = PR 必拒。

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
- **禁止**手动 `git submodule add` 跳过这条流程。理由：manifest 是 `make harness-status` 的源、fork 决策有 `decision_log` 留痕。

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
