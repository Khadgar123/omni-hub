# 万象中枢

个人知识、AI Skill、自动化工作流与本地 API 网关的统一中枢。

## 当前定位（v0.7）

**Control Plane + Worker Pool + Eval/Memory Flywheel** 三层架构，不是聊天工具：

```
TaskPacket
  → AgentJob Queue（SQLite WAL）
  → Worker lane（python / claude / codex / openhands）
  → Artifact → Proposal[T]
  → human approve via propose-list / propose-approve
  → preference / compile / memory 飞轮
```

- **Control Plane**：Operation、Policy、Audit、TaskPacket 契约、`TaskQueue`、`ProposalStore`、Skill Registry。
- **Worker Pool**：可替换 adapter；Codex / Claude Code / OpenHands 在这里是 *headless worker*，不是项目本体。常驻进程是 `omni-hub worker --lane <lane>`，由 launchd 拉起。
- **Eval/Memory Flywheel**：harness（ensemble → judge → preference → DSPy compile → 日/周/月报），外加 4 个 pinned fork（SWE-agent / promptfoo / Argilla / Graphiti）、2 个 RipeMangoBox 上游直连研究资产（ResearchFlow / PaperBite）+ 3 个待升格（DSPy / OpenHands / Opik）。
- **API 管理与网关**：`api-management/metapi` 和 `api-management/ccLoad` 两个 fork 承接余额、额度、模型、路由、协议转换和监控。

工程硬约束写在 [AGENTS.md](AGENTS.md)（4 条），主仓库 `pyproject.toml: dependencies = []` 是 stdlib-only 硬约束。

## 快速开始

推荐 Python 3.12：

```bash
conda env create -f environment.yml
conda activate omni-hub
```

也可以直接运行：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli summarize-text --text "万象中枢是个人知识、AI Skill、自动化工作流与本地 API 网关的统一中枢。" --max-chars 40
```

初始化大型外部服务 fork：

```bash
make setup
```

常用本地能力：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli capture-url --url "https://example.com"
PYTHONPATH=src python3.12 -m omni_hub.cli vault-list --limit 20
PYTHONPATH=src python3.12 -m omni_hub.cli memory-search --query "Graphiti"
PYTHONPATH=src python3.12 -m omni_hub.cli skill-list
PYTHONPATH=src python3.12 -m omni_hub.cli api-management-status
```

Control plane / worker pool（v0.7）：

```bash
# 队列 + 调度
PYTHONPATH=src python3.12 -m omni_hub.cli schedule-tick --period daily   # 入队日常任务
PYTHONPATH=src python3.12 -m omni_hub.cli task-list                      # 看队列状态
PYTHONPATH=src python3.12 -m omni_hub.cli worker --lane python --idle-exit-after-sec 2

# 提案审批（agent worker 产出 + 知识/冗余扫描结果都在这里 review）
PYTHONPATH=src python3.12 -m omni_hub.cli propose-list --state pending
PYTHONPATH=src python3.12 -m omni_hub.cli propose-approve --id <pid> --reason "ok"

# 后台 launchd（macOS）— 需要 Python >= 3.12
PYTHON=/abs/path/to/python3.12+ make schedule-install-dry      # 看渲染的 plist
PYTHON=/abs/path/to/python3.12+ make schedule-install          # 真正装到 ~/Library/LaunchAgents
PYTHON=/abs/path/to/python3.12+ make schedule-uninstall
```

当前所有项目的 API 默认配置在 [api-management/defaults.json](/Users/hzh/Desktop/简历/个人知识库/api-management/defaults.json)：默认 provider 是 DeepSeek，默认模型是 `deepseek-v4-pro`，真实 key 只通过 `local:omni-hub/api/deepseek/default` 保存在本地 secret backend。

运行测试：

```bash
PYTHONPATH=src python3.12 -m unittest discover -s tests
```

## API 管理

`api-management/` 是本地 API 管理部分：

```text
api-management/metapi  -> https://github.com/Khadgar123/metapi
api-management/ccLoad  -> https://github.com/Khadgar123/ccLoad
```

分工：

- Metapi：上游账号、站点、模型发现、余额刷新、低余额告警、成本/余额/使用率路由。
- ccLoad：Claude Code、Codex、Gemini、OpenAI-compatible 本地入口，协议转换、失败切换、令牌限制、RPM/成本限制和请求日志。

工程方式：主仓库是产品编排仓，锁定两个服务 fork 的精确 commit；两个服务仍保留独立仓库、上游 remote、测试和构建系统。日常不要手写 submodule 命令，统一用：

```bash
make setup
make api-update
make harness-update
make test
make compose-config
```

本地启动：

```bash
docker compose --env-file api-management/env.example -f api-management/compose.yml up -d
```

本地改 fork 后构建：

```bash
docker compose --env-file api-management/env.example -f api-management/compose.yml -f api-management/compose.build.yml up -d --build
```

更多说明见 [api-management/README.md](/Users/hzh/Desktop/简历/个人知识库/api-management/README.md)。

## 项目结构

```text
.
├── api-management/         # Metapi + ccLoad fork + compose 入口
├── agent-harness/          # pinned harness modules：SWE-agent / promptfoo / argilla / graphiti
│                           # + RipeMangoBox 上游直连：ResearchFlow / PaperBite
│                           # + pending forks 清单：DSPy / OpenHands / Opik
├── .agents/skills/         # 业务 skill：Claude Code 和 Codex CLI 都通过 symlink 读
├── docs/                   # 架构、权限、提案模型、路线图
├── registry/               # 机器可读 skill 注册表
├── scripts/                # 工具脚本
│   ├── launchd/            # macOS launchd plist 模板（daily/weekly/monthly/worker）
│   ├── install_launchd.py  # 渲染 plist + launchctl bootstrap
│   └── ...
├── src/omni_hub/
│   ├── cli/                # 子命令按域分文件（capture/memory/skill/task/propose/worker/...）
│   ├── harness/            # 自进化飞轮（ensemble/judge/preference/compile/reports）
│   ├── workers/            # WorkerAdapter 协议 + builtin/claude/codex 适配器
│   ├── reports/            # 日/周/月报构建
│   ├── proposals.py        # 统一 Proposal[T] + SQLite ProposalStore
│   ├── queue.py            # AgentJob Queue (SQLite WAL + 原子 claim + lease fencing)
│   ├── builtins.py         # Operation handlers（policy + audit 通道）
│   ├── memory.py / vault.py / skills.py / api_management.py / ...
├── tests/                  # 单元 + 集成测试
├── vault/                  # 本地 Markdown / Obsidian 知识库
├── AGENTS.md               # Codex / 其他 agent 入口（含工程硬约束）
├── CLAUDE.md               # Claude Code 入口（指向 AGENTS.md）
└── README.md
```

## 设计文档

- [架构方案](docs/architecture.md)
- [Operation 模型](docs/operation-model.md)
- [权限与风险模型](docs/permission-model.md)
- [捕获与入库模型](docs/capture-model.md)
- [提案层模型](docs/proposal-model.md)
- [Argilla 反馈数据集](docs/argilla-feedback-datasets.md)
- [Optimizer 模型](docs/optimizer-model.md)
- [记忆层模型](docs/memory-model.md)
- [Skill Registry](docs/skill-registry.md)
- [Skill Intelligence](docs/skill-intelligence.md)
- [推荐组合架构](docs/recommended-stack.md)
- [自进化 Agent Harness](docs/self-evolution-harness.md)
- [Agent 系统与知识库开发设计](docs/agent-system-development-plan.md)
- [Roadmap](docs/roadmap.md)

## 维护原则

- 敏感信息、私钥、Token、账号和证件材料不要提交到 GitHub。
- API key 只放运行时环境或本地 secret backend，仓库只保留示例占位值。
- 主仓库不再维护自研 API 路由器；网关能力进入 `api-management/metapi` 或 `api-management/ccLoad`。
- 每个项目尽量保留目标、输入、输出、依赖和自动化入口。

## 仓库信息

- 中文名：万象中枢
- 仓库名：omni-hub
- 描述：个人知识、AI Skill、自动化工作流与本地 API 网关的统一中枢
