# 推荐组合架构

万象中枢主仓库只保留知识内核和最小控制入口；API 网关不再自研，交给 `api-management/` 下的两个 fork。成熟工程形态选择“产品编排仓 + 独立服务 fork + 精确版本锁定”，而不是把所有源码复制进主仓库。

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
├── Agent Harness
│   ├── SWE-agent fork
│   ├── promptfoo fork
│   ├── Argilla fork
│   └── Graphiti fork
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
| 工程迭代 harness | issue-to-patch、评测回归、人类偏好、知识图谱 | `agent-harness/` |
| 工作流层 | 暂未接入 | 后续可接 n8n webhook adapter |
| 智能编排 | 暂未接入 | 后续可接 OpenAI Agents SDK 或 LangGraph |

## 大型工程方式

| 责任 | 落点 |
| --- | --- |
| 产品配置、默认模型、compose、状态检查、文档、主测试 | `omni-hub` |
| 余额/账号/路由管理服务源码 | `api-management/metapi` fork |
| 本地网关/协议转换服务源码 | `api-management/ccLoad` fork |
| 工程迭代、评测、人类偏好、记忆服务源码 | `agent-harness/*` forks |
| 版本锁定 | 主仓库 gitlink 指针 |
| 新人初始化 | `make setup` |
| 上游同步 | `make api-update` / `make harness-update`，只做 fast-forward，冲突进 fork 内解决 |

这种方式接近大型产品的 polyrepo/service ownership 模型：服务边界清楚，主仓库可复现，外部上游可以持续同步；脚本和 Makefile 把 submodule 的复杂度收起来。

## 为什么删除旧网关

- 主仓库里的 Provider Router、GUI 和 Agent Planner 与 Metapi/ccLoad 功能重叠。
- 自研实现缺少完整余额、成本、告警、限流、协议转换和运维界面。
- 直接维护成熟 fork 更容易跟上上游，也更利于把精力放回知识库和项目上下文。

## 本地验证

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli api-management-status
docker compose --env-file api-management/env.example -f api-management/compose.yml config
```
