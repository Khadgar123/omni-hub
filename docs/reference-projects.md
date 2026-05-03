# 参考项目

我没有把这些项目的源码直接 fork 或复制进 `omni-hub`。当前阶段更适合保留“参考清单 + 架构对照”，避免主仓库被大型外部代码库污染。真正需要深度复用时，优先级是：

1. 直接作为外部服务/API 集成。
2. 用 adapter 包装其核心能力。
3. 用 git submodule 固定版本。
4. 最后才 fork 并维护补丁。

## 最接近的方向

### TideMind

- 地址：https://github.com/SawyerHan-AI/TideMind
- 定位：local-first AI memory layer，把 AI 工具、笔记和思考连接成知识图谱。
- 关键点：MCP、SQLite、本地优先、Obsidian/Logseq/Apple Notes、图谱 recall、background metabolism。
- 对万象中枢的启发：三类工具 `prepare / recall / digest` 很适合借鉴为 Memory API。
- 差异：TideMind 更像记忆层和图谱浏览器，不是多平台工作流控制平面。

### Agentic Local Brain

- 文章/Gist：https://gist.github.com/agent-creativity/a4e090f888a516b313ddd1302e51c286
- 相关仓库：https://github.com/agent-creativity/agentic-local-brain
- 定位：LocalBrain，强调 Agent + IM + Skill + CLI。
- 关键点：Skill 作为可组合能力插件，CLI 作为 agent-friendly 接口。
- 对万象中枢的启发：Skill 入口和 CLI-first 的设计非常贴近我们的方向。
- 差异：目前更像本地知识采集产品路线，不是完整的审批、审计、工作流控制面。

### OpenClaw

- 地址：https://github.com/openclaw/openclaw
- 定位：本地运行的个人 AI 助手，连接聊天渠道、技能、浏览器、文件和自动化。
- 关键点：Gateway、channels、sessions、skills、browser control、cron、webhooks。
- 对万象中枢的启发：多入口、多渠道、Skill registry、Gateway 思路值得借鉴。
- 差异：OpenClaw 更偏“能做事的个人助手”，万象中枢更强调 Operation、权限、审计和知识重构。

## 可作为模块或外部服务的项目

### Khoj

- 地址：https://github.com/khoj-ai/khoj
- 定位：self-hostable AI second brain，支持本地/在线 LLM、Web/docs、Obsidian、WhatsApp、自定义 agents、scheduled automations。
- 用法：参考其文档摄取、个人搜索、agent 配置和多入口设计。

### Graphiti

- 地址：https://github.com/getzep/graphiti
- 定位：给 AI agents 构建 temporal context graph。
- 用法：适合作为后续关系记忆层候选，尤其是 provenance、time-aware facts、incremental update。

### Mem0

- 地址：https://github.com/mem0ai/mem0
- 定位：universal memory layer for AI agents。
- 用法：适合作为用户偏好、agent memory、会话长期记忆候选。

### Dify

- 地址：https://github.com/langgenius/dify
- 定位：LLM app development platform，包含 workflow、RAG、agent、model management、observability。
- 用法：适合作为 AI 应用原型或可视化 workflow 参考，不建议作为万象中枢内核。

### n8n

- 地址：https://github.com/n8n-io/n8n
- 定位：workflow automation platform，400+ integrations，native AI capabilities。
- 用法：适合作为确定性外部工作流执行器，不适合作为个人知识图谱核心。

### Activepieces

- 地址：https://github.com/activepieces/activepieces
- 定位：开源 Zapier 替代，AI workflow automation，pieces 可作为 MCP servers。
- 用法：可作为连接器生态参考，尤其是 piece/MCP 的打包方式。

### LangGraph

- 地址：https://github.com/langchain-ai/langgraph
- 定位：stateful agents as graphs，支持 durable execution、human-in-the-loop、memory。
- 用法：如果后续不只用 OpenAI Agents SDK，可参考其图式编排和持久执行模型。

### AnythingLLM / Open WebUI / Flowise

- AnythingLLM：https://github.com/Mintplex-Labs/anything-llm
- Open WebUI：https://github.com/open-webui/open-webui
- Flowise：https://github.com/FlowiseAI/Flowise
- 用法：适合作为 UI、RAG、agent builder 和本地部署体验参考。

## 当前判断

已经有人在做相邻方向，但还没有看到一个完全等同于万象中枢的开源项目。最接近的是：

- TideMind：最接近记忆/知识图谱层。
- Agentic Local Brain：最接近本地知识采集 + Skill + CLI 思路。
- OpenClaw：最接近多渠道个人 AI 助手和 Skill 生态。
- n8n/Activepieces：最接近连接器和确定性自动化。

因此万象中枢的差异化应继续放在控制平面：

- Operation 作为统一原子动作。
- Policy 和 human approval 作为权限边界。
- Audit log 作为系统记忆和安全基础。
- Proposal layer 作为自动知识重构的缓冲层。
- Connectors / Skills / Workflows 都挂在控制平面下，而不是彼此平级。
