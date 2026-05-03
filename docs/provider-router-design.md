# Provider Router 设计

Provider Router 是万象中枢阶段 1 的本地控制面核心。它不只是“哪个模型便宜就调用哪个”，而是把模型能力、账号额度、API 健康度、调用上限、预算、并发、失败切换和审计统一到一个可观察系统里。

## 设计目标

- 支持 OpenAI、Anthropic、Gemini、OpenRouter、DeepSeek、Moonshot、xAI、本地 Ollama、企业代理等多 provider。
- 支持同一 provider 下多个 account/channel/API key。
- 支持纯文本、多模态、长上下文、低延迟、批处理、函数调用、结构化输出等能力筛选。
- 支持 API key、base URL、默认模型、优先级、预算、额度、速率限制、并发限制配置。
- 支持 provider 挂掉时自动降级或切换，同时保留原因、成本和失败日志。
- 支持 CLI、Mac app、localhost Web dashboard 共用同一套本地 daemon 状态。

## 核心实体

```text
provider
  OpenAI / Anthropic / Gemini / OpenRouter / Ollama / custom compatible endpoint

account_channel
  某个 provider 下的一组凭证和 base_url，可启用/禁用，可有余额和速率限制

model
  模型目录，保存能力、上下文窗口、价格、是否支持 batch/multimodal/tool 等

route_ability
  account_channel 对某个 model 或 model pattern 的可用性、priority、weight、model_mapping

provider_health
  account_channel + model 的健康状态、连续失败、最近错误、延迟、最近检查时间

circuit_breaker
  运行时熔断状态，避免持续打到不可用 endpoint

usage_request_log
  每次请求的 token、成本、延迟、状态、错误、route decision、trace id

usage_daily_rollup
  每日聚合，服务 dashboard 和预算分析

budget_guard
  按全局、项目、provider、account、model、用户维度限制成本

rate_window
  RPM / TPM / 并发窗口

secret_ref
  指向 Keychain、环境变量或运行时 token，不保存 raw key
```

## 推荐 SQLite 表

第一版可以用 SQLite，后续如果需要服务化再迁移 Postgres。表结构要从一开始保留迁移版本。

| 表 | 用途 |
| --- | --- |
| `provider_accounts` | provider、account/channel、base_url、secret_ref、状态、分组、备注 |
| `model_catalog` | model id、display name、能力、上下文、价格、cache 价格、状态 |
| `route_abilities` | account/model 可用性、priority、weight、model_mapping、启用状态 |
| `provider_health` | 健康状态、连续失败、最近成功/失败、错误摘要、延迟 |
| `circuit_breakers` | 熔断状态、opened_at、half_open 计数、阈值 |
| `failover_queues` | 每类 app/task 的候选 channel 顺序 |
| `usage_request_logs` | 单次请求明细，用于审计、成本、debug |
| `usage_daily_rollups` | 日级聚合，减少 dashboard 查询成本 |
| `budget_limits` | 成本上限、token 上限、周期、维度 |
| `rate_limits` | RPM、TPM、并发、batch 限制 |
| `client_configs` | Codex、Claude、Gemini、Cursor、VS Code 等客户端检测结果 |
| `config_backups` | 外部配置改写前的备份和 restore 元数据 |

## 路由流程

```text
RouteRequest
  -> normalize task profile
  -> filter by capability
  -> filter by status / route ability
  -> filter by budget / rate / concurrency
  -> filter by health / circuit breaker
  -> rank by priority
  -> rank by score within priority
  -> pre-consume budget if strict
  -> execute request
  -> record usage / latency / error
  -> update health / circuit breaker
  -> refund pre-consume on failure
  -> emit audit event
```

硬过滤必须在排序前执行。排序分数只在“可用候选”之间比较，不能让高质量分绕过预算、限流、权限和健康状态。

## 评分维度

第一版评分可以保持简单，但字段要为后续扩展留口：

| 维度 | 含义 |
| --- | --- |
| `priority` | 用户设定的硬优先级，先选最高 priority |
| `weight` | 同一 priority 内的权重 |
| `capability_fit` | 与任务能力的匹配度 |
| `reliability` | 历史成功率、连续失败、健康检查 |
| `latency` | 最近延迟和 first token 延迟 |
| `cost` | 预计 input/output/cache token 成本 |
| `quota_headroom` | 剩余额度、RPM/TPM headroom |
| `user_preference` | 用户显式偏好，例如“写作优先 Claude，代码优先 GPT” |

## 配置策略

本地配置分三层：

| 层 | 用途 | 可被请求覆盖 |
| --- | --- | --- |
| 全局默认 | 默认 provider、默认预算、默认 failover 策略 | 否 |
| 项目策略 | 某个项目的模型偏好、预算、连接器权限 | 部分 |
| 单次请求 | 本次任务的能力、预算、偏好、是否允许降级 | 只能收窄，不能扩大权限 |

单次请求可以要求更低风险，例如 `max_cost_usd=0.02` 或 `require_local_only=true`；不能绕过全局或项目的预算、secret 权限、外部发布权限。

当前实现已经支持项目级配置，但只作用于万象中枢自己的 Router 和未来自己的 agent，不改写 Codex、Claude、Gemini、Cursor 等外部客户端配置：

- `project_route_profiles`：项目默认能力、预算上限、batch 要求、provider/account 偏好。
- `project_route_overrides`：某个项目内对指定 account/model 的 priority、weight、禁用状态覆盖。
- `route-simulate --project <project_id>`：按项目 profile 和 override 得出路由结果。

项目级配置只能影响候选排序和更严格的限制，不能绕过全局 account/model 的禁用、健康状态、预算和 secret 规则。

## Secret 规则

- 数据库只保存 `secret_ref`，例如 `env:OPENAI_API_KEY`、`keychain:omni/openai/main`、`runtime:session-id`。
- dashboard 不展示 raw key，只展示来源、末四位哈希、最后验证时间。
- health check 和错误日志必须清理 prompt、messages、api key、云厂商 secret。
- 导出配置时默认不包含 secret；需要显式授权才导出 secret ref。
- macOS 版本优先接 Keychain，CLI 版本优先支持环境变量。

## 本地 daemon

阶段 1 推荐引入 `omni-hubd`：

```text
CLI / Mac app / Web dashboard
  -> Unix domain socket
  -> omni-hubd
  -> SQLite + audit log + provider health worker
```

原因：

- Unix socket 比公开 localhost TCP 更适合本机控制面。
- dashboard 关闭后，daemon 仍可做健康检查、用量归档和日志聚合。
- CLI、Mac app、Web dashboard 不需要各自维护状态。
- 可以用 snapshot fingerprint 或 event stream 控制刷新，避免轮询浪费。

Windows/Linux 后续可用 named pipe 或 127.0.0.1 fallback。Mac 用户优先做原生桌面 client，但 Web dashboard 仍作为跨平台 fallback。

## 外部客户端配置

万象中枢后续会管理 Codex、Claude Code、Gemini CLI、OpenCode、Cursor、VS Code、Windsurf 等客户端的 provider、MCP、Skill 配置。任何改写外部配置的动作都必须走：

```text
detect client
  -> parse current config
  -> validate intended patch
  -> create backup
  -> atomic write
  -> verify reload/readback
  -> audit event
```

不允许直接覆盖配置文件。失败时必须能恢复到上一个备份。

## 第一阶段命令

CLI 先服务开发者和自动化脚本：

```text
omni-hub provider add/list/get/disable/test
omni-hub model add/list
omni-hub route simulate
omni-hub route explain
omni-hub usage list/stats
omni-hub health list/check
omni-hub client detect/list/backup/restore
```

当前已实现的 CLI 子集：

```text
omni-hub provider-add
omni-hub provider-list
omni-hub provider-disable
omni-hub model-add
omni-hub model-list
omni-hub route-ability-set
omni-hub route-profile-set
omni-hub route-profile-list
omni-hub project-route-set
omni-hub project-route-list
omni-hub provider-health-set
omni-hub route-simulate
omni-hub provider-router-stats
```

当前实现只做本地注册、项目级路由配置和路由模拟，还不真实转发 API 请求，也不改写 Codex、Claude、Gemini、Cursor 等外部客户端配置。

Mac app / Web dashboard 对应页面：

- Provider Accounts：API key ref、base URL、状态、余额、速率、优先级。
- Models：能力、价格、上下文、是否 batch/multimodal/tool。
- Router：任务类型、默认策略、failover 顺序、simulate/explain。
- Usage：今日成本、token、错误率、延迟、provider 分布。
- Health：最近健康检查、熔断状态、自动禁用原因。
- Clients：Codex/Claude/Gemini/Cursor/VS Code 的配置状态和备份。
- Secrets：Keychain/env/runtime secret ref，不展示 raw key。

## 与 Skill 推荐的关系

Provider Router 负责“这个任务应该用哪个模型/账号执行”。Skill Intelligence 负责“这个项目应该安装哪些 Skill、哪些会冲突、哪些组合有价值”。两者通过 task profile 连接：

```text
project context
  -> skill recommendation
  -> task profile
  -> provider route
  -> operation execution
  -> audit + usage + memory
```

这样可以让 LLM 先做基础筛选，但最后的执行仍经过可审计的本地控制面。

## 与自有 Agent 的关系

万象中枢自己的 agent 不应该在代码里手写模型名，也不应该去修改 Codex、Claude、Gemini、Cursor 等外部客户端配置。当前实现提供 `agent-plan` 作为自有 agent 的调用前入口：

```text
agent task
  -> task profile
  -> route-simulate equivalent
  -> planned invocation
  -> future model call adapter
```

`agent-plan` 会返回 provider、account、base URL、secret ref、内部 model id、provider 侧 model id、成本估算、路由原因和 warning。它目前只规划调用，不发起外部 API 请求。

为了降低审计日志泄露风险，CLI 不把完整 `--task` 写入 operation payload，只写入截断后的 `task_preview` 和 `task_chars`。后续真实执行时，完整 prompt 应在 agent runtime 内部流转，并由专门的 prompt/audit 策略决定是否落盘。
