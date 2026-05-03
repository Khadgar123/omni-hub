# Skill Registry

Skill registry 是万象中枢的能力目录。它不执行任务，只记录可复用能力的契约，让控制平面知道有哪些 Skill、入口在哪里、风险等级是什么、需要哪些权限。

## 存储位置

```text
registry/skills.json          # 机器可读注册表，可提交 Git
vault/30_Skills/<skill_id>.md # Obsidian 可读 Skill 卡片
```

`registry/skills.json` 不应存放密钥、token 或账号凭据。它只存放元数据。

## Skill 类型

- `project`：项目上下文能力，例如项目链接、项目总结。
- `connector`：平台连接能力，例如 URL 捕获、飞书消息、GitHub issue。
- `workflow`：多步骤流程，例如每日总结、内容发布草稿。
- `agent`：智能编排能力，例如研究 agent、写作 agent。
- `memory`：记忆与检索能力。
- `utility`：通用工具能力。

## CLI

注册 Skill：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli skill-register \
  --id url-capture \
  --name "URL Capture" \
  --kind connector \
  --description "Capture HTTP pages into the inbox." \
  --entrypoint operation:capture_url \
  --risk L1 \
  --connector web \
  --tag capture
```

列出 Skill：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli skill-list
```

读取单个 Skill：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli skill-get --id url-capture
```

禁用 Skill：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli skill-disable --id url-capture
```

## 和 Operation 的关系

Skill 是能力契约，Operation 是实际动作。

```text
Skill: url-capture
└── entrypoint: operation:capture_url
    └── OperationSpec(name="capture_url", risk_level=L1)
```

后续 Router 会根据任务意图选择 Skill，再由 Skill 的 entrypoint 生成具体 Operation。
