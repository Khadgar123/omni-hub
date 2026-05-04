# API 配置与导入

这个文档是给人和代码 agent 共用的。目标是让 Codex、Claude Code 或其他本地 agent 第一次接手项目时，知道应该把模型/API 配置写到万象中枢，而不是把 key 散落到外部客户端配置里。

## 本地入口

启动并直接打开控制台：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli gui --open
```

如果浏览器没有自动打开，手动访问：

```text
http://127.0.0.1:8765
```

默认只监听 localhost。不要在不可信网络里使用 `--host 0.0.0.0 --allow-non-localhost`。

## Agent 首次进入项目会看什么

- Codex 类 coding agent：先看仓库根目录的 `AGENTS.md`，再结合 `README.md` 和相关 `docs/`。
- Claude Code 类客户端：常见入口是 `CLAUDE.md`，本项目让它转向 `AGENTS.md`。
- CC Switch：它不是项目 agent 入口，而是外部 API/客户端配置管理参考；万象中枢只兼容它的配置思想和余额模板，不直接依赖它。

因此本仓库保留两个薄入口：`AGENTS.md` 和 `CLAUDE.md`。真正的 API 配置规则放在本文件。

## 三类渠道

### 官方配置

适合 OpenAI、Claude、Qwen、DeepSeek、Kimi、GLM、MiniMax 官方 API。

操作路径：

1. 打开 `模型配置`。
2. 选择模型厂商。
3. 点击 `添加渠道`。
4. 填 API Key、接口地址、模型列表、默认模型、代理和并发/限流。
5. 保存后点击 `刷新` 查看余额或错误。
6. 在 `项目编组` 为每个能力槽填写模型名顺序并保存。

代码 agent 也可以直接写入本地控制面：

```bash
curl -sS -X POST http://127.0.0.1:8765/api/official-provider-config \
  -H 'Content-Type: application/json' \
  -d '{
    "provider": "openai",
    "name": "OpenAI 官方",
    "base_url": "https://api.openai.com/v1",
    "api_key": "<raw key, only sent to local GUI>",
    "model_ids": "gpt-5.4\ngpt-5.4-mini",
    "default_model": "gpt-5.4",
    "priority": 100
  }'
```

保存后 `.omni/provider-router.sqlite3` 只会记录 secret ref，raw key 会进入本地 secret backend。

### CC Switch 兼容配置

很多中转站和 CC Switch 类似，通常有三种余额查询方式：

- `newapi`：New API 后台常见结构，路径类似 `/api/user/self`，可能需要单独的 access token。
- `generic`：通用余额接口，默认先试 `/v1/usage`，可自定义 `usage_endpoint`。
- `cursorlink`：CursorLink 这类查询页，OpenAI-compatible base URL 是模型调用地址，余额查询走站点自己的 `/api/cursor/queryCredits`。

配置示例：

```json
{
  "provider": "openai",
  "name": "OpenAI 中转 · example",
  "base_url": "https://api.example.com/v1",
  "api_key": "<provider api key>",
  "model_ids": "gpt-5.5\ngpt-5.5-high",
  "default_model": "gpt-5.5",
  "usage_template": "generic",
  "usage_endpoint": "/v1/usage",
  "priority": 90
}
```

万象中枢不会执行任意 JS extractor。CC Switch 的自定义脚本应转换成 `usage_template`、`usage_base_url`、`usage_endpoint` 和固定解析器，避免本地控制面执行未知代码。

### 可爬取第三方页面

如果供应商只给了一个查询页，agent 应先爬取页面，抽取以下字段，再写入本地控制面：

- 模型调用 `base_url`
- 可用模型或模型别名
- API Key 获取方式：页面秘钥、token、售后 token 是否不同
- 余额接口路径、方法、字段
- 充值、封禁、退款等高风险接口，只记录，不自动调用

CursorLink 的当前结构是：

- 查询页：`GET /api/cursor/query?key=...`
- 换取 API Key：`POST /api/cursor/query`，字段 `secretKey`
- 查询余额：`POST /api/cursor/queryCredits`，字段 `apiKey`
- 复制调用地址：`POST /api/cursor/getCopyUrl`
- 充值/封禁/推广记录接口存在，但属于高风险或财务动作，不应自动执行

GUI 已把这次爬到的 CursorLink 结果做成 OpenAI 和 Claude 厂商下的待配置条目，不再放在独立模板区。进入对应厂商列表后，点击 `配置` 会打开同一个添加渠道弹窗，并预填 base URL、模型别名、默认模型和余额查询方式：

- OpenAI/Codex：`cx-5.5`、`cx-5.5-high`、`cx-5.5-xhigh`、`cx-5.4`、`cx-5.4-high`、`cx-5.4-xhigh`
- Claude：`op-4.6`、`so-4.6`

如果已经拿到真实 API Key，也可以直接通过本地 API 写入：

```json
{
  "provider": "openai",
  "name": "OpenAI 中转 · CursorLink",
  "base_url": "https://apicursor.com/v1",
  "api_key": "<real API key, not 16-char query secret>",
  "model_ids": "op-4.6\nso-4.6\ncx-5.5\ncx-5.5-high\ncx-5.5-xhigh\ncx-5.4\ncx-5.4-high\ncx-5.4-xhigh",
  "default_model": "cx-5.5",
  "usage_template": "cursorlink",
  "usage_base_url": "https://cursorlink.net",
  "priority": 89
}
```

## GUI 操作语义

- `刷新`：同时做最小模型连接探测和余额查询，并把健康状态、延迟、余额或错误直接回写到当前行。没有 key 时显示 `待填写 API Key`，不会再暴露底层 secret 异常。
- 余额查询和模型调用一样跟随该渠道的 `代理连接`。留空表示真正 unset，不继承系统代理；如果 CursorLink 这类用量域名 TLS 握手超时，给该渠道填 `http://127.0.0.1:7890` 或 `env:HTTPS_PROXY`，也可以在高级配置里调高 `用量超时秒数` 和 `用量重试次数`。
- `测0-10并发/RPS`：用当前渠道的首个启用模型做 0-10 阶梯并发探测和 0-10 RPS 探测，并尝试访问批处理接口；结果会覆盖手填的 `max_concurrency`、`rps_limit`、`rpm_limit`，并回写 `probed_concurrency_range`、`probed_rps_range`、`batch_support` 和 `batch_probe`。并发探测会依次测试 1、2、...、10 个同时请求；RPS 探测会依次测试 1、2、...、10 个请求在 1 秒窗口内发出。每一级必须全部成功且没有 429 才算通过，因此一次完整探测最多会发起 110 个最小模型请求。
- 批处理探测不会创建批处理任务。OpenAI 兼容渠道默认检查 `/v1/batches?limit=1`，Anthropic 原生渠道检查 `/v1/messages/batches`；2xx 表示支持，404/405/501 表示不支持，401/403/429 只表示鉴权或限流导致未确认。
- `复制条目`：复制为第二条渠道，复用 secret ref 和模型绑定，便于改 base URL、代理或优先级。
- `导出 export 脚本`：复制 shell 环境变量，包含本地解析出的 key，适合临时终端使用。
- `导出 Codex`：复制 `config.toml` 片段，不包含 raw key。
- `删除`：删除渠道，并级联移除相关 route ability、健康记录和项目覆盖。
- 拖拽左侧排序块：调整同一模型厂商下的启用优先级。
- 异步按钮会进入等待态：按钮禁用、显示转圈和“刷新中/探测中/导出中”等文案，完成或失败后恢复。

## Agent/Skill 写入方式

人可以点 GUI，Codex/CC 这类 agent 应优先按 `vault/30_Skills/provider-channel-config/SKILL.md` 直接操作本地控制面：

1. 读取 `GET /api/state`，确认厂商、已有渠道、模型绑定和 secret ref。
2. 如果页面或文档可爬取，先抽取 base URL、模型别名、余额接口、计费口径、代理要求和限制信息。
3. POST `/api/official-provider-config` 创建或更新具体条目。不要新增抽象模板，也不要直接修改外部客户端配置。
4. 保存后调用 `/api/model-fetch`、`/api/model-probe`、`/api/balance-check` 和 `/api/channel-capability-probe` 回填可用模型、健康状态、余额、并发和批处理能力。
5. 最后让用户刷新 GUI 或直接打开 `http://127.0.0.1:8765` 查看结果。

raw key 只允许进入本地 GUI API 的 request body 或本地 secret backend，不允许写入仓库、文档、测试、项目模型包和聊天总结。

## 项目如何使用

项目页现在只要求项目选择多个模型名并决定顺序，不要求项目逐个选择 API 渠道。推荐流程：

1. 在 `模型配置` 页发现或手动录入所有可用模型名。
2. 拖拽渠道排序，决定同名模型优先使用哪个官方或中转渠道。
3. 在 `项目编组` 填项目 ID。
4. 在默认文本、复杂推理、代码与工具、多模态、批处理、检索向量等能力槽里，从可选模型库点击添加模型，并用上移/下移调整顺序。
5. 点击 `保存模型顺序`。
6. 点击 `复制项目模型包` 给项目 runtime 或 agent 使用。

项目模型包会包含两部分：

- `model_orders`：项目自己保存的能力槽模型名顺序。
- `slot_routes`：按当前全局渠道优先级、健康状态、额度状态解析出来的候选渠道。

候选渠道包含 `provider`、`account_id`、`model_id`、`provider_model_id`、`base_url`、`secret_ref`、`proxy_url`、`max_concurrency`、`rps_limit`、`rpm_limit`、`tpm_limit`、`quota_ref`、`health_status`、`latency_ms` 和 `usable/reject_reason`。项目 runtime 或 agent 只需要读取 `selected`；调用失败时再按 `candidates` 的顺序重试。换中转站、换 API Key、调整代理或修正并发限制时，只改模型配置页，项目的模型顺序不用变。不要把 raw key 写进项目模型包。

更完整的跨工具适配矩阵见 [项目模型接入设计](project-model-integration.md)。
