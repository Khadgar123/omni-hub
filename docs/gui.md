# GUI

万象中枢当前提供一个本地 Web GUI，用于管理 API 渠道、模型来源、路由策略、项目偏好和调用预演。它是阶段 1 的本机控制台，目标是先把“接入 API -> 选择策略 -> 项目差异化 -> 调用前检查”做成一个清晰的操作面板。

启动：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli gui
```

默认地址：

```text
http://127.0.0.1:8765
```

## 当前能力

- 总览本机 API 渠道、已知模型、默认策略、项目偏好、健康记录和调用日志数量。
- 通过左侧导航进入总览、API 接入、路由策略、项目偏好和调用预演。
- API 接入提供 OpenAI、OpenRouter、DeepSeek、SiliconFlow 和自定义中转站模板，减少手填字段。
- 模型目录在 GUI 中只读展示，不再要求用户手工维护；后续由厂商 `/models`、价格表和调用日志自动补全。
- 路由策略用于设置默认 API 渠道和模型选择顺序。
- 项目偏好用于为不同项目设置能力、预算和项目专属优先级。
- 调用预演用于真实花钱前检查当前策略会选中哪个 API 渠道和模型。
- 各个表格支持搜索和每页 8 条分页浏览。
- 所有按钮点击后都会给出成功或失败反馈。

## 页面结构

- `总览`：展示本机状态指标、快速入口和当前接入概览。
- `API 接入`：通过模板登记官方 API、中转站、公司网关或本地网关。
- `路由策略`：配置默认 account/model priority、weight 和 provider 侧模型名映射。
- `项目偏好`：配置项目 profile 和项目级 route override。
- `调用预演`：根据项目、能力、预算和输出 token 生成一次 dry run 调用计划。

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
