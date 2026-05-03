# 万象中枢

个人知识、AI Skill、自动化工作流与多平台连接的统一中枢。

## 项目定位

万象中枢不只是一个个人知识库，而是未来 AI 工作效率系统的基础入口。它用于沉淀知识、管理项目上下文、配置 AI Skill、编排自动化工作流，并逐步连接飞书、X、B 站、YouTube、小红书等外部平台。

核心目标是把分散的信息源、项目资料、自动化脚本、Agent 能力和平台接口统一到一个可维护、可扩展、可复用的中枢里。

## 当前开发状态

当前仓库已经完成 v0.1 本地内核，并开始 v0.2 捕获与知识入库：

- `Operation`：所有动作的原子抽象，可审计、可审批、可重试。
- `Policy`：按风险等级决定是否自动执行、等待人工审批或要求沙箱。
- `Audit`：本地 JSONL 审计日志，默认写入 `.omni/audit/events.jsonl`。
- `CLI`：先用本地命令跑通内核，后续再接飞书、Discord、GitHub、Obsidian 和 Web 控制台。
- `Capture`：v0.2 已开始支持 URL 捕获、HTML 元数据提取、YouTube URL 识别和本地 Inbox 卡片生成。

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
- [推荐组合架构](docs/recommended-stack.md)
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
