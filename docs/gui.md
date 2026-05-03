# GUI

万象中枢当前提供一个本地 Web GUI，用于管理 API 渠道、模型池、代理连接、项目 AI 编组、使用选择、监控检测和 Skills。它是阶段 1 的本机控制台，目标是先把“接入渠道 -> 导入模型池 -> 项目编组 -> 选择使用 -> 持续监控”做成一个清晰的操作面板。

启动：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli gui
```

默认地址：

```text
http://127.0.0.1:8765
```

## 当前能力

- 总览本机 API 渠道、模型池、项目编组、健康记录和调用日志数量。
- 通过左侧导航进入总览、渠道模型、项目编组、使用选择、监控检测和 Skills。
- 渠道模型提供 CC Switch 类中转站预设：CodexOpenAI Official、胜算云、AiHubMix、DMXAPI、优云智算、PIPELLM、OpenRouter、TheRouter、CodexAzure OpenAI、PackyCode、Cubence、AIGoCode、RightCode、SSSAiCode、Micu、CTok.ai、LionCCAPI、DDSHub、E-FlowCode、LemonData、AICodeMirror、AICoding、CrazyRouter 和自定义配置。
- 每个 API 渠道都可以配置代理连接；留空表示该渠道调用时 `unset` 代理。
- 一个 API 渠道可以挂多个模型；模型 ID 默认手写，只有明确存在模型别名时才用别名卡片填充。
- 渠道配置支持两种路径：按中转站配置用于管理密钥、代理、额度和健康；按模型配置用于从模型反推可用渠道和优先级。
- 厂商和模型预设按热度排序，热门项直接展示，完整列表通过下拉选择；接口地址只在高级配置中出现。
- 后续由厂商 `/models`、价格表和调用日志自动补全模型池、价格和可用性。
- 项目编组用于为一个项目配置多个 AI 角色，例如主力、快速、视觉、批量和备用，并允许它们来自不同 API 渠道。
- 使用选择按项目和任务类型一键选择当前模型，不要求输入长任务文本。
- 监控检测用于记录渠道健康状态、代理、延迟、失败信息；后续 worker 会补齐用量、额度、错误率和成本趋势。
- Skills 页面参考 CC Switch 的设计方向：GitHub/ZIP/本地安装、跨客户端同步、symlink/file copy、卸载前备份、冲突检测和项目推荐。
- 各个表格支持搜索和每页 8 条分页浏览。
- 所有按钮点击后都会给出成功或失败反馈。

## 页面结构

- `总览`：展示本机状态指标、快速入口和当前接入概览。
- `渠道模型`：通过预设或自定义配置登记官方 API、中转站、公司网关或本地网关，配置代理并导入模型池。
- `项目编组`：为一个项目配置多个 AI 角色和项目级 route override。
- `使用选择`：按项目和任务类型选择当前可用模型，返回 base URL、provider model、secret ref 和代理状态。
- `监控检测`：检测渠道配置、代理、Base URL 连通性、延迟和错误信息。
- `Skills`：Skill registry、安装、同步、备份、冲突检测和项目推荐的入口。

## 安全边界

- 默认只绑定 `127.0.0.1`。
- 不保存 raw API key，只保存 `env:`、`keychain:`、`runtime:` 形式的 secret ref。
- 不改写 Codex、Claude、Gemini、Cursor、VS Code 等外部客户端配置。
- 不真实调用外部模型 API。
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
- 增加真实模型调用 adapter，但仍强制经过 Router、Policy、Audit。
