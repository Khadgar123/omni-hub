# 万象中枢

个人知识、AI Skill、自动化工作流与多平台连接的统一中枢。

## 项目定位

万象中枢不只是一个个人知识库，而是未来 AI 工作效率系统的基础入口。它用于沉淀知识、管理项目上下文、配置 AI Skill、编排自动化工作流，并逐步连接飞书、X、B 站、YouTube、小红书等外部平台。

核心目标是把分散的信息源、项目资料、自动化脚本、Agent 能力和平台接口统一到一个可维护、可扩展、可复用的中枢里。

## 当前开发状态

当前仓库已经进入 v0.1 本地内核阶段，先实现控制平面的最小骨架：

- `Operation`：所有动作的原子抽象，可审计、可审批、可重试。
- `Policy`：按风险等级决定是否自动执行、等待人工审批或要求沙箱。
- `Audit`：本地 JSONL 审计日志，默认写入 `.omni/audit/events.jsonl`。
- `CLI`：先用本地命令跑通内核，后续再接飞书、Discord、GitHub、Obsidian 和 Web 控制台。

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

运行测试：

```bash
PYTHONPATH=src python3.12 -m unittest discover -s tests
```

## 项目结构

```text
.
├── docs/              # 架构、权限、路线图
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
