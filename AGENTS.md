# Agent Entry

万象中枢的本地模型/API 配置以 GUI 和本地 HTTP API 为准。Codex 或其他代码 agent 第一次进入仓库时，先读这个文件，再读 `README.md`、`docs/api-configuration.md` 和 `vault/30_Skills/provider-channel-config/SKILL.md`。

## API 配置原则

- 不要把 raw API key 写入仓库、README、docs 或测试夹具。
- 新增或修改模型渠道时，优先写入本地控制面：启动 GUI 后 POST `http://127.0.0.1:8765/api/official-provider-config`。
- GUI 和 API 会把网页填写的 key 存进本地 secret backend；SQLite 只保存 `local:`、`env:`、`keychain:` 或 `runtime:` 引用。
- 官方接口、中转站、CC Switch/NewAPI 风格余额查询、可爬取第三方查询页的处理方式见 `docs/api-configuration.md`。
- 新增厂商或中转站时，按 `vault/30_Skills/provider-channel-config/SKILL.md` 收集字段、写入条目、发现模型、刷新余额，并探测 0-10 并发、RPS 和批处理能力。
- 项目运行时只读取项目模型包里的 `secret_ref`、`base_url`、`proxy_url`、并发/限流和模型映射，不读取 raw key。

## 本地 GUI

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli gui --open
```

如果 `--open` 失败，手动打开 `http://127.0.0.1:8765`。
