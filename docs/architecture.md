# 架构方案

万象中枢的定位不是另一个聊天机器人，也不是把 n8n、Dify、OpenClaw、知识库、爬虫脚本简单拼在一起。它应该是一个个人 AI 工作系统的控制平面，负责统一调度、权限、状态、审计和记忆。

## 核心分层

```text
omni-hub control plane
├── Registry       # 连接器、Skill、Agent、Workflow、数据源登记
├── Router         # 决定任务交给谁处理
├── Policy         # 权限、风险等级、审批、预算
├── State          # 任务状态、运行上下文、幂等键
├── Memory API     # 统一读写 Graphiti / Mem0 / Obsidian / 向量库
├── Event Bus      # 飞书、Discord、GitHub、定时任务、网页捕获
└── Audit          # 输入、输出、决策、审批、失败、重试记录

execution plane
├── OpenAI Agents SDK     # 智能编排、工具调用、handoff、guardrail、tracing
├── deterministic worker  # Python operation executor
├── workflow engine       # MVP 先本地队列，后续接 Temporal / n8n
├── sandbox               # 高风险命令、文件批处理、爬取任务隔离执行
└── connectors            # Feishu / Discord / GitHub / Obsidian / Web / YouTube 等

data plane
├── Postgres / SQLite     # 任务、配置、审计、运行状态
├── object storage        # 原始网页、视频字幕、附件、截图
├── graph memory          # 实体、关系、时间线、来源
├── vector index          # 语义检索
└── vault                 # 本地 Markdown / Obsidian 知识库
```

## 第一原则

- Chat 是入口，不是架构。
- Agent 是执行者，不是根系统。
- Workflow 是骨架，用来承载长期、可重试、可观察的任务。
- Operation 是原子动作，必须可审计、可审批、可重试。
- Sandbox 是高风险边界。
- Human approval 是刹车，不是事后补丁。

## 当前 v0.1 范围

当前版本先实现最小内核：

- Operation 模型
- Policy 风险决策
- Audit JSONL 日志
- 本地 CLI
- 本地 Markdown 写入
- 文本摘要占位操作

这些能力看起来朴素，但它们是后续接入飞书、Discord、GitHub、OpenAI Agents SDK、Temporal、Graphiti 的公共底座。
