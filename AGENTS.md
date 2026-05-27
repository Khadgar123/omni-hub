# Agent Entry

Codex 或其他代码 agent 第一次进入仓库时，先读这个文件，再读 `README.md` 和 `api-management/README.md`。

## API 管理原则

- 不要把 raw API key 写入仓库、README、docs、测试夹具或 compose 示例。
- 本地 API 管理已交给 `api-management/metapi` 和 `api-management/ccLoad` 两个 fork。
- Metapi 负责上游账号、余额、模型发现、成本/余额/使用率路由和告警。
- ccLoad 负责本地网关、协议转换、失败切换、令牌/RPM/成本限制和请求监控。
- 主仓库只保留最小状态检查；新增网关能力优先改对应 fork，不要恢复旧 Provider Router 或 GUI。
- 全项目当前默认 DeepSeek：配置声明在 `api-management/defaults.json`，真实 key 只允许写入 `local:omni-hub/api/deepseek/default`。

## 本地检查

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli api-management-status
docker compose --env-file api-management/env.example -f api-management/compose.yml config
```
