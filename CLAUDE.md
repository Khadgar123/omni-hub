# Claude Code Entry

本项目的 agent 操作入口与 Codex 共用：**先读 `AGENTS.md`**，再读 `README.md` 和 `api-management/README.md`。
`AGENTS.md` 里的"工程硬约束"对 Claude Code 完全适用——4 条规则违反 = 改动必拒。

Claude Code 在本仓库里有两种身份：

1. **交互工程协作**：开发者打开 Claude Code 在终端里直接对话，按 `.agents/skills/<id>/SKILL.md` 加载的技能干活。
2. **headless worker**：launchd 调度起来的 `omni-hub worker --lane claude` 在后台跑，从 `TaskQueue` 拉任务，subprocess 调 `claude -p`，输出走 `Proposal[T]` 等人 review。

两种身份用同一套契约（TaskPacket / Artifact / Proposal），不要在主仓库新增"chat 模式" 的代码路径。

## API 网关

新增模型渠道、余额监控、协议转换、成本限制和路由策略，应进入 `api-management/metapi` 或 `api-management/ccLoad`，不要恢复主仓库里已经删除的旧 Provider Router / GUI。
