# Skill Intelligence

Skill 太多时，万象中枢不能只做注册表，还需要帮助判断：

- 应该安装或启用哪些 Skill。
- 哪些 Skill 质量更可信。
- 哪些 Skill 可以组合。
- 哪些 Skill 会冲突。
- 哪些 Skill 风险太高，需要审批或沙箱。

## 当前实现

当前版本是本地启发式推荐，不依赖外部 LLM：

- 根据 query 匹配 skill id、名称、描述、类型、标签、连接器和入口。
- 根据状态、入口、描述、标签、输入输出契约、来源路径和风险生成 metadata quality 评分。
- 根据风险等级扣分。
- 根据 `draft / disabled / deprecated` 状态扣分或隐藏。
- 根据权限需求生成 warning。
- 根据共享 entrypoint、共享外部写连接器、发布类能力检测冲突。

这里的 quality 不是“实际效果保证”，而是本地可解释的质量初筛。真正的质量层后续还需要接入执行成功率、测试结果、用户反馈、来源信誉、签名和更新时间。

## CLI

推荐 Skill：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli skill-recommend --query "youtube capture" --max-risk L1
```

分析 Skill 组合：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli skill-analyze --id url-capture --id vault-proposal --id memory-digest
```

## 后续升级

后续可以接入：

- 外部 skill manager 扫描：Codex、Claude Code、Cursor、emp-agent、gh skill。
- 使用 embedding 做语义检索。
- 根据 GitHub stars、更新时间、签名、来源、历史执行结果生成质量分。
- 用 approval queue 管理高风险 skill 启用。
- 用 Web UI 展示推荐、依赖、冲突和风险。
