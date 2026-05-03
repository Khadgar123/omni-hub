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

适合 OpenAI、Claude、Qwen、DeepSeek、GLM、MiniMax 官方 API。

操作路径：

1. 打开 `模型配置`。
2. 选择模型厂商。
3. 点击 `添加渠道`。
4. 填 API Key、接口地址、模型列表、默认模型、代理和并发/限流。
5. 保存后点击 `刷新` 查看余额或错误。
6. 在 `项目编组` 一键导入项目模型包。

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

如果已经拿到真实 API Key，可以这样写入：

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

- `刷新`：查询余额并回写到当前行；失败时显示错误。
- `复制条目`：复制为第二条渠道，复用 secret ref 和模型绑定，便于改 base URL、代理或优先级。
- `导出 export 脚本`：复制 shell 环境变量，包含本地解析出的 key，适合临时终端使用。
- `导出 Codex`：复制 `config.toml` 片段，不包含 raw key。
- `删除`：删除渠道，并级联移除相关 route ability、健康记录和项目覆盖。
- 拖拽左侧排序块：调整同一模型厂商下的启用优先级。

## 项目如何使用

项目页导出的模型包包含：

- `provider`
- `account_id`
- `model_id`
- `base_url`
- `secret_ref`
- `proxy_url`
- `max_concurrency`
- `rpm_limit`
- `tpm_limit`
- `wire_api`
- `requires_openai_auth`
- `pricing`
- `health`

项目 runtime 或 agent 只需要读取这些字段，再用 secret ref 在本机解析 key。不要把 raw key 写进项目模型包。
