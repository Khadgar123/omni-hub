# 推荐组合架构

万象中枢主仓库只保留知识内核和最小控制入口；API 网关不再自研，交给 `api-management/` 下的两个 fork。

## 当前组合

```text
omni-hub
├── Knowledge Core
│   ├── Operation
│   ├── Policy
│   ├── Audit
│   ├── Capture
│   ├── Memory
│   └── Skill Registry
├── API Management
│   ├── Metapi fork
│   └── ccLoad fork
└── Execution Boundary
    ├── local files
    ├── approval policy
    └── audit log
```

## 分工

| 层 | 当前实现 | 维护位置 |
| --- | --- | --- |
| 知识内核 | Operation / Policy / Audit / Capture / Memory / Skill | 主仓库 |
| API 余额与模型管理 | 账号、站点、余额、模型发现、告警、成本路由 | `api-management/metapi` |
| API 本地网关 | Claude Code / Codex / Gemini / OpenAI-compatible、协议转换、限流、日志 | `api-management/ccLoad` |
| 工作流层 | 暂未接入 | 后续可接 n8n webhook adapter |
| 智能编排 | 暂未接入 | 后续可接 OpenAI Agents SDK 或 LangGraph |

## 为什么删除旧网关

- 主仓库里的 Provider Router、GUI 和 Agent Planner 与 Metapi/ccLoad 功能重叠。
- 自研实现缺少完整余额、成本、告警、限流、协议转换和运维界面。
- 直接维护成熟 fork 更容易跟上上游，也更利于把精力放回知识库和项目上下文。

## 本地验证

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli api-management-status
docker compose --env-file api-management/env.example -f api-management/compose.yml config
```
