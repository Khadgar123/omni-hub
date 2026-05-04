# 项目模型接入设计

项目不应该直接复制某个中转站 API Key，也不应该手写一大段模型名文本。项目只保存“能力槽 -> 模型名顺序”，万象中枢负责把模型名解析成当前可用的渠道、base URL、secret ref、代理、并发和限流参数。

## 调研结论

| 工具 | 配置方式 | 对万象中枢的适配 |
| --- | --- | --- |
| OpenAI Codex | `~/.codex/config.toml` 中配置 `model`、`model_provider` 和 `[model_providers.*]` | 生成 provider snippet；项目内只放 manifest，不写 raw key |
| Claude Code | 用户/项目 `.claude/settings.json`，模型可用 `model`、环境变量和 `apiKeyHelper` | 项目可 pin 一个模型；多模型 fallback 由 Omni resolver 先解析 |
| Gemini CLI | 用户 `~/.gemini/settings.json` 或项目 `.gemini/settings.json`，`model.name` 控制模型 | 项目 settings pin 当前首选模型；fallback 仍在 Omni |
| Continue | `config.yaml`，`models` 支持 provider/model/apiBase/roles | 把能力槽映射到 `chat`、`edit`、`apply`、`autocomplete` 等角色 |
| Cline | UI/remote config 配置 Provider、Base URL、API Key、Model ID | 生成 OpenAI Compatible 设置说明；不直接写 VS Code secret |
| Aider | `.aider.conf.yml`、环境变量、`.env`、模型 metadata/settings | 生成 repo-local `.aider.conf.yml` 和模型 metadata，secret 用 env/ref |
| OpenAI Agents SDK | `ModelProvider` 解析模型名；agent/run 可设置 model | 自定义 `ModelProvider` 调用 `/api/project-resolve` |
| LangChain/LangGraph | `init_chat_model` 支持 provider:model 和运行时 configurable model | 只开放 model/model_provider，不允许 runtime 改 `api_key/base_url` |
| LiteLLM | `config.yaml` 的 `model_list`、router settings、预算/限流 | 适合作为后续 Omni Gateway 后端或导出目标 |
| Portkey | Gateway config 支持 fallback、conditional、load balance | 适合把能力槽导出成 fallback targets |

参考来源：[Claude Code settings/model config](https://code.claude.com/docs/en/model-config)、[Gemini CLI settings](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/settings.md)、[Continue config.yaml](https://docs.continue.dev/reference)、[Cline OpenAI-compatible provider](https://docs.cline.bot/provider-config/openai-compatible)、[Aider configuration](https://aider.chat/docs/config.html)、[OpenAI Agents SDK models](https://openai.github.io/openai-agents-python/models/)、[LangChain init_chat_model](https://reference.langchain.com/python/langchain/chat_models/base/init_chat_model)、[LiteLLM proxy docs](https://docs.litellm.ai/)、[Portkey gateway configs](https://portkey.ai/docs/product/ai-gateway/fallbacks)。

## 万象中枢标准

项目仓库内推荐文件：

```text
.omni/omni-hub.project.json
```

内容结构：

```json
{
  "schema": "omni-hub.project.v1",
  "project_id": "auto-driving-research",
  "resolver": "http://127.0.0.1:8765/api/project-resolve",
  "slots": {
    "default": {
      "model_order": ["deepseek-chat", "gpt-5.5-mini"],
      "selected": {
        "model_id": "deepseek-chat",
        "account_id": "deepseek-main",
        "base_url": "https://api.deepseek.com",
        "secret_ref": "local:omni-hub/deepseek-main"
      }
    }
  },
  "secret_policy": "raw keys stay in local secret backend; resolve secret_ref at runtime"
}
```

运行时请求：

```bash
curl -sS -X POST http://127.0.0.1:8765/api/project-resolve \
  -H 'Content-Type: application/json' \
  -d '{"project_id":"auto-driving-research","slot":"reasoning"}'
```

返回的 `selected` 是当前首选渠道；`candidates` 是降级顺序。项目 runtime 调用失败时按 `candidates` 重试。换 API Key、换中转站、代理、额度或优先级时，只改万象中枢模型配置页。

## UI 原则

- 左侧是多个项目，不把所有项目配置平铺在一个页面。
- 右侧点开单个项目后显示项目 ID、能力槽和接入文件。
- 模型选择从“可选模型库”点击添加，不手写模型名。
- 每个能力槽内用模型 chip 展示顺位，支持上移、下移和移除。
- 项目接入文件只在当前项目详情里显示，默认不暴露 raw key。
