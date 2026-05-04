# GUI

万象中枢当前提供一个本地 Web GUI，用于管理模型厂商、官方/中转渠道、调用优先级、代理连接、项目模型包、监控检测和 Skills。它是阶段 1 的本机控制台，目标是先把“选择模型厂商 -> 添加官方或中转渠道 -> 发现模型/测试连接 -> 拖拽排序 -> 一键导入项目模型包 -> 刷新余额与状态”做成一个清晰的操作面板。

启动：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli gui --open
```

默认地址：

```text
http://127.0.0.1:8765
```

## 当前能力

- 总览本机模型厂商、渠道配置、项目模型包、健康记录和调用日志数量。
- 通过左侧导航进入总览、模型配置、项目编组、监控检测和 Skills。
- 模型配置页按模型厂商分类：OpenAI、Claude、Qwen、DeepSeek、Kimi、GLM、MiniMax。主页面以渠道列表为中心，添加和修改都进入同一个弹窗。
- 一个模型厂商可以保存多套渠道，包括官方 API 和多个中转站 API；当前厂商列表只展示该厂商渠道，便于查找和排序。
- OpenAI 和 Claude 厂商下内置 CursorLink 待配置条目；它们直接出现在对应厂商渠道列表里，点击 `配置` 会预填爬到的 `https://apicursor.com/v1`、模型别名和 CursorLink 余额查询方式。
- 配置列表支持刷新余额、复制条目、导出 export 脚本、导出 Codex 配置、修改、删除，以及拖拽调整调用优先级。
- 网页端提供 API Key 输入框；raw key 不写入 SQLite。所有平台默认写入本地 `.omni/secrets.json`，数据库只保存 `local:` 引用，并生成默认环境脚本。
- 每个厂商配置都可以设置代理连接；留空表示该渠道调用和余额查询都 `unset` 代理，不继承系统代理。可填写 `http://127.0.0.1:7890` 或 `env:HTTPS_PROXY`。
- Base URL、API Key、密钥引用、代理、并发上限、RPM/TPM 和模型列表是主配置；高级配置只放 API 格式、认证字段、完整端点模式、模型发现 URL、测试参数和计费参数。
- 一个渠道可以挂多个模型，同一模型厂商下的不同渠道按优先级启用；某个渠道健康状态为 down 时自动切到下一级候选。
- 后续由厂商 `/models`、价格表和调用日志自动补全模型、价格和可用性。
- 项目编组用于为项目一键导入模型包，不再要求用户逐个填写 Agent 角色。默认分类是：默认文本、复杂推理、代码与工具、多模态、批处理/低价、检索向量。
- 项目模型包会输出项目和 agent runtime 可读 JSON，包括模型、渠道、`base_url`、`secret_ref`、代理、并发限制、RPM/TPM、计费参数和健康状态，并额外生成 `slot_routes` 候选清单；raw key 不会导出。
- 监控检测用于记录模型配置健康状态、代理、实时延迟、余额、失败信息和连接测试结果。刷新会同时做最小模型探测和余额查询，并直接回写当前表格；连接测试走 `/api/model-probe`，会按渠道协议发起最小流式请求，收到首个 chunk 即判定连通，可能产生极小 API 费用。
- 配置列表里的 `测0-10并发/RPS` 会发起 0-10 阶梯并发探测、0-10 RPS 探测和批处理端点探测，并用实测值覆盖 `max_concurrency`、`rps_limit`、`rpm_limit`、`batch_support`，供项目模型包和 agent runtime 使用。并发和 RPS 各测试 1 到 10 级，每级必须全部成功且没有 429 才通过；一次完整探测最多会发起 110 个最小模型请求，可能产生小额 token 成本。
- 模型发现使用 OpenAI 兼容 `/v1/models` 候选接口；遇到 Anthropic 兼容子路径时会额外尝试剥离子路径后的 `/v1/models` 和 `/models`。
- 余额查询独立于连接测试，当前接入 DeepSeek、Kimi、StepFun、SiliconFlow、OpenRouter、Novita AI、NewAPI、通用 `/v1/usage` 和 CursorLink；未支持厂商会回退展示配置里的额度入口。余额接口支持单独的 `用量超时秒数` 和 `用量重试次数`，TLS/网络超时会显示可操作的代理提示。
- Skills 页面参考 CC Switch 的设计方向：GitHub/ZIP/本地安装、跨客户端同步、symlink/file copy、卸载前备份、冲突检测和项目推荐。
- 各个表格支持搜索和每页 8 条分页浏览。
- 异步按钮点击后会显示转圈和等待文案，并在请求期间禁用，完成后恢复并给出成功或失败反馈。

## 页面结构

- `总览`：展示本机状态指标、快速入口和当前接入概览。
- `模型配置`：按模型厂商配置官方和中转渠道，维护厂商内渠道列表和拖拽优先级。
- `项目编组`：为项目一键导入模型包，生成运行时可读配置和项目级 route override。
- `监控检测`：刷新余额、代理、实时延迟、HTTP 错误码和额度/限流信号。
- `Skills`：Skill registry、安装、同步、备份、冲突检测和项目推荐的入口。

## 安全边界

- 默认只绑定 `127.0.0.1`。
- 不保存 raw API key 到 SQLite，只保存 `env:`、`local:`、`runtime:` 形式的 secret ref；本地 secret 文件位于 `.omni/secrets.json`，随 `.omni/` 被 git ignore。
- 不改写 Codex、Claude、Gemini、Cursor、VS Code 等外部客户端配置；只导出可复制的 shell 或 Codex 配置片段。
- 只有“测试连接”会真实调用外部模型 API，并使用最小请求；项目模型包只生成路由配置，不调用外部模型。
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
