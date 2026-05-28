# Skill Registry（v0.7 + 三真源问题）

Skill = **确定性、可重复、本地的动作**。Agent（chat、planner）不是 skill；调用 LLM 不是 skill；
任何"看 prompt 决定怎么做"的东西都不是 skill。skill 必须输入决定输出。

## 三真源问题（v0.7 仍未完全解）

一个 skill 当前可能存在于**三个地方**，没有自动同步：

```
src/omni_hub/skills.py            ← Python: SkillRegistry / SkillSpec
                                    （注册 / 查询 / disable 的代码层）
.agents/skills/<id>/SKILL.md      ← Markdown: Claude Code / Codex 实际加载的 frontmatter
                                    （agent interactive 用）
registry/skills.json              ← JSON: 给非-python 进程读的注册表
                                    （5 月 3 日创建后没再动过 —— 漂移风险）
```

理论上三者应该等价。**实际上没有同步工具**——`skill-register` CLI 只写 SkillRegistry 内部状态，
不更新 `.agents/skills/` 也不更新 `registry/skills.json`。这是 v0.8 P1-5 要解的债。

当前的 ground truth 默契：

| 用途 | 真源 |
|---|---|
| Claude Code / Codex 交互时加载的 skill | `.agents/skills/<id>/SKILL.md`（frontmatter + body） |
| `skill-list` / `skill-recommend` / `skill-analyze` 读到的 | `SkillRegistry`（内存 + 可选 JSON 持久化） |
| 第三方进程 / MCP server 应读 | `registry/skills.json`（计划用、当前 stale） |

## SkillSpec 数据形状

```python
@dataclass
class SkillSpec:
    skill_id: str                   # kebab-case
    name: str                       # 人类可读
    kind: SkillKind                 # connector | memory | workflow | composite
    description: str
    version: str = "0.1.0"
    status: SkillStatus = DRAFT     # draft | review | active | deprecated | disabled
    entrypoint: str = ""            # e.g. "operation:capture_url"
    risk_level: RiskLevel = READ_ONLY
    required_permissions: list[str] = []
    connectors: list[str] = []
    tags: list[str] = []
    inputs: dict = {}               # JSONSchema
    outputs: dict = {}              # JSONSchema
    source_path: str = ""           # vault/30_Skills/<id>.md 或 SKILL.md 路径
```

## SKILL.md frontmatter

```markdown
---
id: api-management-status
name: API Management Status
kind: connector
risk_level: L0
description: Inspect metapi + ccLoad local stack health
entrypoint: operation:api_management_status
tags:
  - api-management
  - status
---

# 正文……
```

Claude Code 4.x 的 `.agents/skills/` 约定：frontmatter 决定加载行为，body 是 LLM-readable 指令。
我们额外加 `entrypoint:` 字段（如 `operation:capture_url`）让 SKILL.md 可以指向一个真正的 OperationRunner handler。

## 已存在的 skill（截至 2026-05-28）

| skill_id | 位置 | 状态 |
|---|---|---|
| `api-management-status` | `.agents/skills/api-management-status/SKILL.md` | ✓ 完整 |
| `url-capture` | `.agents/skills/README.md` 列了 TODO | ❌ 未实现 |
| `memory-search` | `.agents/skills/README.md` 列了 TODO | ❌ 未实现 |
| `skill-registry` | `.agents/skills/README.md` 列了 TODO | ❌ 未实现 |

## CLI 接口

```bash
omni-hub skill-register --id <id> --name ... --kind connector --description ... \
    --entrypoint operation:capture_url --risk L1 --tag capture
omni-hub skill-list [--kind <kind>] [--status <status>] [--tag <tag>]
omni-hub skill-get --id <id>
omni-hub skill-disable --id <id>
omni-hub skill-recommend --query "search memory"
omni-hub skill-analyze --id <id> --id <id2>
```

`skill-recommend` 是基于 description / tag 的简单文本匹配，不是 LLM-based。

## 工程硬约束

- skill **不写 vault/memory 直接**——必经 OperationRunner（[operation-model.md](operation-model.md)）
- 高风险 skill（L2+）必须有 `required_permissions` 字段，policy engine 会卡审批
- skill_id 必须 kebab-case、唯一；`skill-register` 会拒绝重复

## v0.8 P1-5 计划：`skill-sync` CLI

```bash
omni-hub skill-sync --dry-run   # 看 diff
omni-hub skill-sync --apply     # SKILL.md → SkillRegistry → registry/skills.json
```

扫 `.agents/skills/<id>/SKILL.md` frontmatter，规范化为 `SkillSpec`，写回内部 registry 和 JSON 文件。
解掉"三真源永远漂移"的根因。

## v0.8 P2-2 计划：MCP server 暴露 skill

把 omni-hub 的 skill / operation 暴露成 MCP tools，让 Claude.ai 桌面 / 其他 MCP client 也能用。
那时 `registry/skills.json` 升级为 MCP server 的 tool schema 来源。
