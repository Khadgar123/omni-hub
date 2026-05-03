# 推荐组合架构

万象中枢不应把所有能力都塞进一个框架。更稳妥的方式是让 `omni-hub` 做控制平面，其他系统各司其职。

## 推荐组合

```text
omni-hub
├── Control Plane
│   ├── Operation
│   ├── Policy
│   ├── Audit
│   ├── Approval
│   ├── Skill Registry
│   └── Connector Registry
├── Intelligent Orchestration
│   ├── OpenAI Agents SDK
│   └── LangGraph candidate
├── Deterministic Workflow
│   └── n8n
├── Memory Layer
│   ├── SQLite memory (current)
│   ├── Graphiti candidate
│   └── Mem0 candidate
└── Execution Boundary
    ├── sandbox
    ├── approval queue
    └── audit log
```

## 当前落地状态

| 层 | 当前实现 | 下一步 |
| --- | --- | --- |
| 控制平面 | Operation / Policy / Audit / Skill registry / Skill intelligence | Approval queue / Router |
| 捕获层 | URL / YouTube URL / Markdown vault | 更多连接器 |
| 提案层 | 摘要、实体、关系提案 | LLM 提案生成器 |
| 记忆层 | SQLite documents / entities / relations | Graphiti / Mem0 adapter |
| 工作流层 | 暂未接入 | n8n webhook adapter |
| 智能编排 | 暂未接入 | OpenAI Agents SDK adapter |
| 安全边界 | 风险等级 / sandbox_required | 实际 sandbox executor |

## 为什么这样分

- `omni-hub` 保留最终控制权，负责权限、审计、审批和状态。
- OpenAI Agents SDK 或 LangGraph 只做智能编排，不直接持有所有权限。
- n8n 做确定性、可视化、可重试的外部工作流。
- SQLite memory 先满足本地可控和可测试，Graphiti/Mem0 后续作为增强适配。
- sandbox 和 approval 是高风险动作的硬边界，不依赖提示词自觉。
