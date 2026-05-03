# GUI

万象中枢当前提供一个本地 Web GUI，用于管理 Provider Router 和 Agent Planner 的本地状态。

启动：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli gui
```

默认地址：

```text
http://127.0.0.1:8765
```

## 当前能力

- 查看 provider account、model、route ability、project profile、project override 的当前状态。
- 添加或更新 provider account。
- 添加或更新 model catalog。
- 添加或更新全局 account/model route ability。
- 添加或更新项目级 route profile。
- 添加或更新项目级 account/model priority override。
- 规划一次自有 agent 调用，返回 provider、account、model、secret ref、成本估算和路由原因。

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
