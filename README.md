# 万象中枢

个人知识、AI Skill、自动化工作流与多平台连接的统一中枢。

## 项目定位

万象中枢不只是一个个人知识库，而是未来 AI 工作效率系统的基础入口。它用于沉淀知识、管理项目上下文、配置 AI Skill、编排自动化工作流，并逐步连接飞书、X、B 站、YouTube、小红书等外部平台。

核心目标是把分散的信息源、项目资料、自动化脚本、Agent 能力和平台接口统一到一个可维护、可扩展、可复用的中枢里。

## 当前开发状态

当前仓库已经完成 v0.1 本地内核、v0.2 捕获与知识入库，并开始 v0.3 Provider Router：

- `Operation`：所有动作的原子抽象，可审计、可审批、可重试。
- `Policy`：按风险等级决定是否自动执行、等待人工审批或要求沙箱。
- `Audit`：本地 JSONL 审计日志，默认写入 `.omni/audit/events.jsonl`。
- `CLI`：先用本地命令跑通内核，后续再接飞书、Discord、GitHub、Obsidian 和 Web 控制台。
- `Capture`：v0.2 已开始支持 URL 捕获、HTML 元数据提取、YouTube URL 识别和本地 Inbox 卡片生成。
- `Provider Router`：v0.3 已开始支持本地 SQLite provider/account/model/ability/health 注册与路由模拟。
- `Agent Planner`：自有 agent 调用前先走 Provider Router，支持项目级模型优先级。

## 快速开始

推荐使用 Python 3.12。已有 conda 时可以这样创建环境：

```bash
conda env create -f environment.yml
conda activate omni-hub
```

也可以直接使用本机的 `python3.12`：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli summarize-text --text "万象中枢是个人知识、AI Skill、自动化工作流与多平台连接的统一中枢。" --max-chars 40
```

写入本地知识库：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli write-markdown --path vault/00_Inbox/demo.md --title "Demo" --body "第一条本地知识卡片"
```

检查风险策略：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli check-policy --connector x --action publish --risk L3
```

捕获网页 URL：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli capture-url --url "https://example.com"
```

只登记 YouTube URL，不联网抓取：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli capture-url --url "https://youtu.be/dQw4w9WgXcQ" --no-fetch
```

列出本地 vault 笔记：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli vault-list --limit 20
```

为 Inbox 中的笔记生成知识提案：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli propose-note --path vault/00_Inbox/example.md
```

接受提案并写入本地 SQLite 记忆层：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli memory-digest-proposal --proposal "<proposal_id>"
```

搜索本地记忆：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli memory-search --query "Graphiti"
```

注册 Skill：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli skill-register --id url-capture --name "URL Capture" --kind connector --description "Capture HTTP pages into the inbox." --entrypoint operation:capture_url --risk L1 --connector web --tag capture
```

列出 Skill：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli skill-list
```

推荐 Skill：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli skill-recommend --query "youtube capture" --max-risk L1
```

分析 Skill 组合：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli skill-analyze --id url-capture --id vault-proposal --id memory-digest
```

注册 Provider account：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli provider-add --id openai-main --provider openai --name "OpenAI Main" --base-url "https://api.openai.com/v1" --secret-ref env:OPENAI_API_KEY
```

注册模型：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli model-add --id gpt-5.4 --capability text --capability tools --input-cost 2 --output-cost 10
```

绑定 route ability：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli route-ability-set --account openai-main --model gpt-5.4 --priority 10
```

模拟路由决策：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli route-simulate --capability tools --input-tokens 1000 --output-tokens 500 --max-cost 0.01
```

设置项目级路由 profile：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli route-profile-set --project writing --capability text --prefer-provider anthropic --max-cost 0.02
```

设置项目级 account/model 优先级：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli project-route-set --project writing --account anthropic-main --model claude-opus --priority 50
```

按项目模拟路由决策：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli route-simulate --project writing --capability text
```

规划一次我们自己的 agent 调用：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli agent-plan --project writing --task "帮我整理这个项目的上下文" --capability text --output-tokens 800
```

`agent-plan` 只生成调用计划，不真实请求外部模型。它会先查询 Provider Router，并返回将使用的 provider、account、model、provider 侧模型名、secret ref、成本估算和路由原因。

启动本地 GUI：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli gui
```

默认打开 `http://127.0.0.1:8765`。GUI 是中文本地控制台，包含总览、模型配置、项目编组、监控检测和 Skills；模型配置页按模型厂商分组管理官方渠道和多个中转站渠道，主页面以渠道列表为中心，添加和修改都进入同一个弹窗。Base URL、API Key、代理、并发/RPM/TPM、模型列表是主配置；高级配置只放 API 格式、认证字段、Full URL、模型发现 URL、测试参数和计费参数。网页填写的 API Key 默认写入本地 `.omni/secrets.json`，`.omni/` 已被 git ignore；SQLite 只保存 `local:` 引用。每个厂商下的渠道列表支持修改、模型探测、模型发现、复制默认脚本、查额度、监控，以及拖拽或上移/下移调整启用顺序；路由会按优先级选择，故障时自动切到下一级。项目页改为一键导入项目模型包，输出项目可读 JSON：模型、渠道、base_url、secret_ref、proxy、并发限制、限流、计费和健康状态都会包含，并额外按默认文本、复杂推理、代码与工具、多模态、批处理和检索向量生成候选模型清单；raw key 不会导出。

运行测试：

```bash
PYTHONPATH=src python3.12 -m unittest discover -s tests
```

## 项目结构

```text
.
├── docs/              # 架构、权限、路线图
├── registry/          # 机器可读注册表
├── src/omni_hub/      # 控制平面核心代码
├── tests/             # 基础单元测试
├── vault/             # 本地 Markdown / Obsidian 知识库
│   ├── 00_Inbox/      # 临时收集
│   ├── 10_Knowledge/  # 知识沉淀
│   ├── 20_Projects/   # 项目上下文
│   ├── 30_Skills/     # AI Skill 配置与说明
│   ├── 40_Workflows/  # 自动化工作流
│   ├── 50_Connectors/ # 平台连接
│   └── 90_Archive/    # 归档
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
- [GUI](docs/gui.md)
- [推荐组合架构](docs/recommended-stack.md)
- [本地控制平面参考项目](docs/local-control-plane-references.md)
- [Provider Router 设计](docs/provider-router-design.md)
- [参考项目](docs/reference-projects.md)
- [Roadmap](docs/roadmap.md)

## 维护原则

- 先沉淀上下文，再抽象为可复用的 Skill 或工作流。
- 每个项目尽量保留目标、输入、输出、依赖和自动化入口。
- 每个连接器记录账号范围、授权方式、API 限制和安全注意事项。
- 敏感信息、私钥、Token、账号、证件材料不要提交到 GitHub。

## 仓库信息

- 中文名：万象中枢
- 仓库名：omni-hub
- 描述：个人知识、AI Skill、自动化工作流与多平台连接的统一中枢
