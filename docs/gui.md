# GUI

万象中枢当前提供一个本地 Web GUI，用于管理官方厂商配置、调用优先级队列、代理连接、项目 AI 编组、使用选择、监控检测和 Skills。它是阶段 1 的本机控制台，目标是先把“选择官方厂商 -> 填写 API Key 或密钥引用 -> 加入配置列表 -> 调整优先级 -> 项目编组 -> 选择使用 -> 持续监控”做成一个清晰的操作面板。

启动：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli gui
```

默认地址：

```text
http://127.0.0.1:8765
```

## 当前能力

- 总览本机官方厂商配置、模型配置、项目编组、健康记录和调用日志数量。
- 通过左侧导航进入总览、模型配置、项目编组、使用选择、监控检测和 Skills。
- 模型配置页先按官方厂商分类：OpenAI、Claude、Qwen、DeepSeek、GLM、MiniMax。
- 每个厂商可以保存多套配置；配置列表支持修改、流式健康检查、模型发现、复制默认脚本、查额度、监控，以及拖拽或上移/下移调整调用优先级。
- 网页端提供 API Key 输入框；raw key 不写入 SQLite，macOS 默认写入 Keychain，数据库只保存 `keychain:` 引用，并生成默认环境脚本。
- 每个厂商配置都可以设置代理连接；留空表示该渠道调用时 `unset` 代理。
- 一个厂商配置可以挂多个模型，路由会按优先级选择；某个配置健康状态为 down 时自动切到下一级候选。
- 接口地址只在高级配置中出现。
- 后续由厂商 `/models`、价格表和调用日志自动补全模型、价格和可用性。
- 项目编组用于为一个项目配置多个 Agent 角色，例如研究 Agent、代码 Agent、视觉 Agent、批量 Agent 和备用 Agent，并为每个角色选择模型、渠道和 Skills。
- 使用选择按项目和任务类型一键选择当前模型，不要求输入长任务文本。
- 监控检测用于记录模型配置健康状态、代理、实时延迟、额度来源、失败信息和真实流式健康检查结果。健康检查会按渠道协议发起最小流式请求，收到首个 chunk 即判定连通，记录模型延迟、HTTP 错误码、request id 和响应头里的限流/额度信号，可能产生极小 API 费用。
- 模型发现使用 OpenAI 兼容 `/v1/models` 候选接口；遇到 Anthropic 兼容子路径时会额外尝试剥离子路径后的 `/v1/models` 和 `/models`。
- 余额查询独立于健康检查，当前接入 DeepSeek、StepFun、SiliconFlow、OpenRouter 和 Novita AI 的专用余额接口；未支持厂商会回退展示配置里的额度入口。
- Skills 页面参考 CC Switch 的设计方向：GitHub/ZIP/本地安装、跨客户端同步、symlink/file copy、卸载前备份、冲突检测和项目推荐。
- 各个表格支持搜索和每页 8 条分页浏览。
- 所有按钮点击后都会给出成功或失败反馈。

## 页面结构

- `总览`：展示本机状态指标、快速入口和当前接入概览。
- `模型配置`：按官方厂商配置 API，维护厂商配置列表和自动切换队列。
- `项目编组`：为一个项目配置多个 Agent 角色、模型、渠道、Skills 和项目级 route override。
- `使用选择`：按项目和任务类型选择当前可用模型，返回 provider model、secret ref 和代理状态。
- `监控检测`：检测模型配置、代理、真实流式请求、实时延迟、HTTP 错误码和额度/限流信号。
- `Skills`：Skill registry、安装、同步、备份、冲突检测和项目推荐的入口。

## 安全边界

- 默认只绑定 `127.0.0.1`。
- 不保存 raw API key，只保存 `env:`、`keychain:`、`runtime:` 形式的 secret ref。
- 不改写 Codex、Claude、Gemini、Cursor、VS Code 等外部客户端配置。
- 只有“流式健康检查”会真实调用外部模型 API，并使用最小请求；Agent 规划和使用选择只生成路由计划，不调用外部模型。
- 不把完整 agent task 写入 Operation payload；GUI agent plan API 只向 Planner 传递截断后的 `task_preview`、字符数和 token 估算。

如果确实需要绑定非 localhost 地址，必须显式开启：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli gui --host 0.0.0.0 --allow-non-localhost
```

这个模式会暴露本地控制面，不建议在不可信网络中使用。

## 后续方向

- 把当前 HTTP API 抽成 `omni-hubd` daemon。
- macOS 桌面端复用同一套 daemon API。
- 增加 health worker、usage log 面板、budget 面板。
- 增加更多厂商的模型发现、余额查询和真实调用 adapter，但仍强制经过 Router、Policy、Audit。
