# 万象中枢

个人知识、AI Skill、自动化工作流与本地 API 网关的统一中枢。

## 当前定位

仓库只保留两类能力：

- 本地知识内核：Operation、Policy、Audit、Capture、Vault、Memory、Skill Registry。
- API 管理与网关：维护 `api-management/metapi` 和 `api-management/ccLoad` 两个 fork，用成熟项目承接余额、额度、模型、路由、协议转换和监控。

原来自研的 Provider Router、Agent Planner 和 GUI 已移除，避免重复造低质量网关。后续 API 能力优先在两个 fork 中维护，主仓库只保留最小状态检查和文档入口。

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

常用本地能力：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli capture-url --url "https://example.com"
PYTHONPATH=src python3.12 -m omni_hub.cli vault-list --limit 20
PYTHONPATH=src python3.12 -m omni_hub.cli memory-search --query "Graphiti"
PYTHONPATH=src python3.12 -m omni_hub.cli skill-list
PYTHONPATH=src python3.12 -m omni_hub.cli api-management-status
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
├── api-management/    # Metapi + ccLoad fork 与 compose 入口
├── docs/              # 架构、权限、路线图
├── registry/          # 机器可读注册表
├── src/omni_hub/      # 本地知识内核与 API 管理状态入口
├── tests/             # 单元测试
├── vault/             # 本地 Markdown / Obsidian 知识库
└── README.md
```

## 设计文档

- [架构方案](docs/architecture.md)
- [Operation 模型](docs/operation-model.md)
- [权限与风险模型](docs/permission-model.md)
- [捕获与入库模型](docs/capture-model.md)
- [提案层模型](docs/proposal-model.md)
- [记忆层模型](docs/memory-model.md)
- [Skill Registry](docs/skill-registry.md)
- [Skill Intelligence](docs/skill-intelligence.md)
- [推荐组合架构](docs/recommended-stack.md)
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
