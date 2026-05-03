# 本地控制平面参考项目

调研时间：2026-05-03。

这轮调研的目标不是找一个项目直接搬进来，而是避免从零设计本地 Provider、额度、路由、Skill、MCP、桌面面板和 CLI 控制面。筛选标准如下：

- 最近仍在维护，优先看 2026 年仍有提交或发布的项目。
- 与 `omni-hub` 的本地控制面、Provider 管理、用量监控、Skill/MCP 管理、桌面体验直接相关。
- 许可证允许参考架构；AGPL/GPL 项目只做产品和架构观察，不复制代码。
- 能补足我们之前没考虑充分的工程细节，例如 failover、circuit breaker、quota pre-consume、配置备份、Unix socket daemon、secret handling。

## 参考矩阵

| 项目 | 角色 | 许可证 | 活跃度快照 | 是否下载源码 | 对万象中枢的价值 |
| --- | --- | --- | --- | --- | --- |
| [CC Switch](https://github.com/farion1231/cc-switch) | 桌面 Provider / Skill / MCP / Proxy 面板 | MIT | pushed 2026-05-02 | 是 | 最像我们要做的 Mac/桌面控制面，重点参考 Tauri、SQLite schema、failover、usage log、tray 体验 |
| [LiteLLM](https://github.com/BerriAI/litellm) | Python SDK / LLM Gateway / Router | Other | pushed 2026-05-03 | 是 | 重点参考 routing strategy、health check、budget、rate/concurrency limiter、fallback 语义 |
| [Portkey Gateway](https://github.com/Portkey-AI/gateway) | AI Gateway / request-scoped routing | MIT | pushed 2026-03-25 | 是 | 重点参考 gateway config、retry、fallback、loadbalance、conditional routing、schema validation |
| [One API](https://github.com/songquanpeng/one-api) | LLM API 管理与二次分发 | MIT | pushed 2026-01-09 | 是 | 重点参考 channel、ability、priority、weight、balance、auto disable、quota accounting |
| [OpenUsage](https://github.com/janekbaraniewski/openusage) | 本地 quota / usage dashboard | MIT | pushed 2026-04-30 | 是 | 重点参考 local-first daemon、Unix socket、SQLite、spool、自动发现本地工具和 API key |
| [mTarsier](https://github.com/mcp360/mTarsier) | MCP / Skill / AI client manager | MIT | pushed 2026-04-20 | 是 | 重点参考多 AI 客户端检测、MCP/Skill 配置路径、配置备份、恢复、校验 |
| [Cherry Studio](https://github.com/CherryHQ/cherry-studio) | 跨平台 AI 桌面客户端 | AGPL-3.0 | pushed 2026-05-03 | 否 | 只参考产品形态、模型管理、会话体验；不复制代码 |
| [New API](https://github.com/QuantumNous/new-api) | 新一代 LLM Gateway / AI 资产管理 | AGPL-3.0 | pushed 2026-04-30 | 否 | 只参考聚合网关、模型格式转换、用户级限流、缓存计费；不复制代码 |
| [Helicone](https://github.com/Helicone/helicone) | LLM observability | Apache-2.0 | pushed 2026-05-02 | 否 | 参考观测、日志、分析面板；当前不引入完整观测平台复杂度 |
| [Open WebUI](https://github.com/open-webui/open-webui) | 通用 AI Web UI | Other | pushed 2026-05-01 | 否 | 参考 UI 和本地部署体验；不是控制平面内核 |
| [RouteLLM](https://github.com/lm-sys/RouteLLM) | LLM 路由研究框架 | Apache-2.0 | pushed 2024-08-10 | 否 | 概念可读，但不适合作为当前主参考，活跃度偏弱 |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) | 官方智能编排 SDK | MIT | 官方文档仍推荐 | 否 | 适合作为 agent/tools/handoffs/guardrails/tracing 编排层，不替代本地控制面 |

本地参考源码已下载到 `references/sources/`，当前大小约 421MB，并已通过 `.gitignore` 排除，不进入主仓库。

## 关键发现

### CC Switch

CC Switch 的价值在于它已经把桌面控制面、Provider 切换、Skill/MCP 管理、proxy、用量统计放到同一个产品里。它使用 Tauri 2、React、TypeScript、Rust、SQLite，并且最近仍在快速迭代。

最值得借鉴的是数据模型，而不是界面表层：

- `providers` 不只是名称和 URL，还包含 app 类型、配置、排序、当前启用状态、failover queue 状态。
- `proxy_config` 按 Claude、Codex、Gemini 等 app type 存储独立的代理开关、监听地址、端口、failover、timeout、circuit breaker 阈值。
- `provider_health` 记录健康状态、连续失败、最近成功/失败、错误原因。
- `proxy_request_logs` 记录 provider、model、token、cache token、成本、延迟、first token、状态码、错误、session、streaming 等字段。
- `model_pricing` 独立建表，支持 input/output/cache token 价格。
- `usage_daily_rollups` 用于日级聚合，避免面板每次扫全量请求日志。

设计结论：万象中枢不能只做一个 `providers.json` 加评分函数。Provider 控制面必须从一开始保留 request log、daily rollup、health、failover queue、pricing 和迁移能力。

### LiteLLM

LiteLLM 的价值在于 Router 和 Proxy 细节成熟，已经覆盖多 provider、多 deployment、fallback、rate limit、budget、health check、spend tracking。

值得借鉴的细节：

- routing 策略不是单一“最低价”或“最高质量”，而是 least busy、lowest cost、lowest latency、lowest TPM/RPM、tag-based、retry/fallback 等组合。
- health check 会清理敏感字段，避免把 messages、api key、prompt、云厂商 secret 展示到 UI 或日志。
- health check 有 bounded concurrency，避免探测本身打爆 provider。
- pre-call hook 会先检查 budget、RPM、TPM、parallel request，超限时直接拒绝。
- max budget limiter 是执行前硬约束，不是事后统计。

设计结论：我们的 provider router 应该先做硬过滤，再做排序。硬过滤包括 capability、预算、速率、并发、健康状态、circuit breaker、是否允许 batch/multimodal。

### Portkey Gateway

Portkey 的价值在于 gateway config 模型清晰：single、fallback、loadbalance、conditional routing、guardrails、cache、retry、timeout 都可以作为请求级配置。

值得借鉴的细节：

- request header 中可以携带 gateway config，但必须做 schema validation。
- gateway 默认支持 retries、fallback、load balancing、conditional routing、budget/rate limit、circuit breaker。
- console 默认本地端口启动，适合开发者快速查看状态。

设计结论：万象中枢可以借鉴 request-scoped routing config，但本地版不能允许任意请求直接覆盖高风险路由策略。需要把“用户配置的默认策略”和“单次请求覆盖”分级，并记录到 audit log。

### One API

One API 的价值在于 channel/account 层建模成熟，尤其适合我们思考“多个 API key、多个 base url、多个模型能力”的关系。

值得借鉴的细节：

- `channel` 保存 provider 类型、key、状态、权重、响应时间、base URL、余额、可用模型、分组、已用额度、模型映射、优先级。
- `ability` 把 group、model、channel、priority 关联起来，路由时先按最高 priority 过滤，再在候选中选择。
- balance 监控可自动禁用余额不足的 channel，并通知管理员。
- billing 支持预扣额度，失败后返还。

设计结论：万象中枢需要区分 provider、account/channel、model、ability/route，而不是把它们压成一条模型配置。优先级和权重也应分开：先 priority，再 weight/score。

### OpenUsage

OpenUsage 的价值在于它证明了本地 quota dashboard 不一定要走 Web 服务。它是 local-first、terminal-first，后台 daemon 用 SQLite 存储历史，并通过 Unix domain socket 给前端/CLI 读状态。

值得借鉴的细节：

- 配置支持账号、provider、auth、API key env、probe model、base URL、binary、paths。
- runtime token 不持久化，优先使用运行时 token 或环境变量。
- daemon 有 collect loop、poll loop、watch loop、spool、retention、WAL checkpoint。
- socket server 使用 Unix socket 和严格 timeout，降低 localhost TCP 暴露面。
- dashboard 用 snapshot fingerprint 避免无意义刷新。

设计结论：阶段 1 不应该默认把控制面暴露到外网，也不应该默认用公开 TCP 端口。更稳妥的是 `omni-hubd` 本地 daemon + Unix socket + CLI/Mac app 客户端，必要时再提供 127.0.0.1 Web fallback。

### mTarsier

mTarsier 的价值在于它已经系统化整理了 Claude Desktop、Claude CLI、ChatGPT、Codex Desktop/CLI、OpenCode、Gemini CLI、VS Code、Cursor、Windsurf 等客户端的 MCP/Skill 配置路径。

值得借鉴的细节：

- client registry 记录 client id、名称、类型、各 OS 配置路径、配置格式、检测方式、skills path。
- 支持 JSON/TOML 等配置格式读写。
- 写配置前支持 backup、list backups、restore、validate。

设计结论：万象中枢需要自己的 client registry，但配置写入必须更严格：先 parse validate，再 backup，再 atomic write，再 audit。外部 AI 客户端配置不能直接覆盖。

### OpenAI Agents SDK

OpenAI 官方文档明确把 Agents SDK 定位为 code-first orchestration，适合 agents、tools、handoffs、guardrails、tracing、sandbox execution。它适合放在执行/编排层，但不是本地 provider dashboard、预算、密钥、审计、client config 管理的替代品。

设计结论：`omni-hub` 仍然是控制平面。Agents SDK 作为智能编排器接入，所有高风险工具调用、外部发布、密钥使用、预算消耗都需要经过 `Policy`、`Audit`、`Approval` 和 Router。

## 不直接采用的东西

- 不复制 AGPL/GPL 项目源码到主仓库，例如 Cherry Studio、New API。它们可以作为产品观察和架构参考。
- 不把完整 AI Gateway 一次性搬进来。Portkey/LiteLLM 的完整复杂度适合团队平台，万象中枢阶段 1 只做本地控制面最小闭环。
- 不在数据库里保存 raw API key。数据库只保存 `api_key_ref`，真实 secret 来自 macOS Keychain、环境变量或运行时输入。
- 不默认开放外网访问。阶段 1 是 localhost / Unix socket / Mac desktop / CLI。
- 不无备份地改写 Codex、Claude、Gemini、Cursor、VS Code 等外部工具配置。
- 不把“模型质量”写死成主观分数。质量分可以有，但必须结合能力、成本、上下文长度、稳定性、速率限制、历史失败率、用户偏好和任务类型。

## 对阶段 1 的修正

阶段 1 应从“网页面板 + provider JSON”修正为：

```text
omni-hubd
├── Unix socket API
├── SQLite state store
├── provider / account / model / route registry
├── usage request log + daily rollup
├── health check + circuit breaker
├── budget / rate / concurrency guard
├── client registry + config backup
└── audit log

clients
├── CLI
├── macOS desktop client
└── 127.0.0.1 Web dashboard fallback
```

这样做比直接做网页面板更慢一点，但能保证后续 Mac 桌面、CLI、Web fallback、agent 自动筛选、API failover、Skill 推荐都共用同一套可信状态。
