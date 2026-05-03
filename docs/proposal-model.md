# 提案层模型

自动化系统不应该直接改写稳定知识库。v0.2 引入 Proposal layer，让 Agent 或规则先提出建议，再由人工或更严格的工作流确认。

## 当前提案类型

```text
vault note
└── propose_knowledge operation
    ├── summary
    ├── entity proposals
    ├── relation proposals
    ├── .omni/proposals/<proposal_id>.json
    └── .omni/proposals/<proposal_id>.md
```

## 当前实现

当前版本是确定性启发式实现，不依赖外部 LLM：

- 从 Markdown frontmatter 提取元数据。
- 从标题、标签、wikilink、Markdown link 和已知术语中提出实体。
- 从文档标题到实体生成 `mentions` 关系。
- 从 Markdown link 生成 `links_to` 关系。
- 所有提案只写入 `.omni/proposals`，不直接进入 `vault/10_Knowledge`。

## 后续升级

后续接入 OpenAI Agents SDK 后，LLM 只替换“提案生成器”，不改变整体边界：

- Operation 仍然负责审计、权限和幂等。
- ProposalStore 仍然负责落盘。
- 高置信低风险提案可以自动接受。
- 低置信或高影响提案进入审批队列。
