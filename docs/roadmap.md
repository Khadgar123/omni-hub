# Roadmap

## v0.6 Self-evolution Harness（2026-05-28 完成 12 周交付）

完整开发计划见 [agent-system-development-plan.md](agent-system-development-plan.md)。
12 周路线全部落地在主仓库 `src/omni_hub/harness/` + `src/omni_hub/reports/`，
**不依赖任何外部 fork 即可单独运行**；外部 fork 通过 graceful-fallback 模式
按需启用（DSPy / Graphiti / OpenHands / Opik）。

| 周 | 模块 | 状态 |
| --- | --- | --- |
| 1–2 | TaskPacket / GenerationRecord 契约 + ensemble (无权重多模型投票) | ✅ |
| 3–4 | ensemble 集成测试 + ccLoad 单一真源 | ✅ |
| 5–6 | grounding 原子声明 + 5 维 bias_audit + judge_ensemble (本地启发式 + LLM) | ✅ |
| 7–8 | preference 偏好库 (Argilla schema 兼容) + dspy_compile (BootstrapFewShot fallback) | ✅ |
| 9 | graphiti_bridge (SQLite fallback) + redundancy 四类 proposal | ✅ |
| 10 | openhands_bridge (dispatch stub) + 日/周/月报模板 (写入 vault/40_Reports/) | ✅ |
| 11 | domain_profiles 加载 + 8 域 TaskPacket 模板（finance/policy/IR 强化权重）| ✅ |
| 12 | opik_bridge (JSONL fallback) + replay/stats 数据飞轮统计 | ✅ |

测试覆盖：**101 个单测全过**（v0.5 时 45 个 → v0.6 新增 56 个）。

新 CLI 子命令（24 个）：

- 输入契约：`harness-task-validate` / `harness-task-template` / `harness-domain-list` / `harness-domain-get`
- 生成与评测：`harness-ensemble` / `harness-judge` / `harness-ground`
- 偏好飞轮：`harness-preference-add` / `harness-preference-stats` / `harness-compile`
- 记忆与冗余：`harness-redundancy-scan`
- 报表：`harness-report-daily` / `harness-report-weekly` / `harness-report-monthly`
- 观测：`harness-stats` / `harness-replay`

下一步候选（v0.7）：把 pending forks（DSPy / OpenHands / Opik）转成真实 submodule，
替换对应 bridge 模块的 fallback 实现。`make harness-add-pending all`。

## v0.1 本地内核

- Operation / Policy / Audit 基础模型
- 本地 CLI
- 本地 Markdown 写入
- 文本摘要占位操作
- 架构文档

## v0.2 捕获与知识入库

- 普通网页 URL 捕获
- YouTube URL 识别与元数据卡片
- Obsidian / Markdown vault 读取
- 原始材料、摘要、实体、关系提案分层存储
- 本地 SQLite 记忆层
- Graphiti 或 Mem0 初步接入评估

## v0.3 智能编排

- OpenAI Agents SDK 包装层
- Skill registry
- Skill recommendation / conflict analysis
- Router：根据任务选择 Skill / Workflow / Connector
- Guardrail：输入注入、权限越界、发布风险检查

## v0.4 外部入口

- 飞书消息入口
- Discord 消息入口
- GitHub webhook
- 每日计划与每日总结

## v0.5 确定性工作流

- 长任务队列
- 重试、超时、取消、恢复
- Temporal 或 n8n 接入
- 定时任务

## v1.0 控制台

- 任务列表
- 审批队列
- Connector 管理
- Skill 管理
- 审计日志搜索
- 知识图谱浏览与重构建议
