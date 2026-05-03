# Operation 模型

万象中枢不应以 Agent 作为最小抽象，而应以 Operation 作为最小抽象。

## 概念关系

```text
Task        = 用户目标，例如“总结这个视频并发到飞书”
Workflow    = 多个 Operation 的编排
Operation   = 可审计、可审批、可重试的原子动作
Connector   = 外部平台适配器
Skill       = 可复用能力契约
Memory      = 带来源、时间、关系和置信度的知识对象
```

## Operation 必备字段

- `name`：操作处理器名称，例如 `summarize_text`
- `connector`：平台或执行域，例如 `local`、`feishu`、`github`
- `action`：具体动作，例如 `read`、`write`、`send_message`、`publish`
- `payload`：输入参数
- `actor`：触发者
- `source`：入口，例如 CLI、飞书、Discord、GitHub webhook
- `risk_level`：风险等级
- `required_permissions`：需要的权限
- `approval_required`：是否显式要求审批
- `sandbox_required`：是否显式要求沙箱
- `idempotency_key`：幂等键
- `timeout_seconds`：超时
- `retry_policy`：重试策略

## 典型 Operation

- `fetch_url`
- `summarize_document`
- `extract_entities`
- `create_graph_edges`
- `write_markdown`
- `send_feishu_message`
- `create_github_issue`
- `publish_x_post`
- `run_sandbox_command`

## 节点与边的重构策略

自动建图不能直接污染主知识图谱。建议分三层：

- Raw layer：原文、字幕、评论、网页快照，只追加不重写。
- Proposal layer：Agent/规则提出实体、关系、标签、摘要、引用。
- Canonical layer：经过合并、去重、置信度评估后的稳定知识节点。

重构不交给单次 Agent 决定，而应做成周期性工作流：

- 找出重复实体、过细标签、孤立节点、低置信边。
- 生成合并建议和影响范围。
- 小风险自动合并，高风险进入人工审批。
- 所有重构必须保留来源和回滚记录。
