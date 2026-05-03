# 记忆层模型

当前 v0.2 先实现一个本地 SQLite memory。它不是为了替代 Graphiti 或 Mem0，而是为了让万象中枢先拥有一个可测试、可审计、可迁移的 canonical memory 接口。

## 数据流

```text
vault note / captured card
└── propose-note
    └── .omni/proposals/<proposal_id>.json
        └── memory-digest-proposal
            └── .omni/memory.sqlite3
```

## SQLite 表

- `documents`：稳定文档摘要，按 `source_path` 幂等更新。
- `entities`：实体候选合并后的 canonical entity。
- `relations`：实体间关系，按 `source / relation / target / source_path` 幂等更新。

## CLI

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli propose-note --path vault/00_Inbox/example.md
PYTHONPATH=src python3.12 -m omni_hub.cli memory-digest-proposal --proposal <proposal_id>
PYTHONPATH=src python3.12 -m omni_hub.cli memory-search --query "Graphiti"
PYTHONPATH=src python3.12 -m omni_hub.cli memory-stats
```

## 后续替换点

SQLite memory 当前只做关键词搜索和关系展开。后续接入 Graphiti 或 Mem0 时，保留 Operation 名称和 CLI 语义，把内部存储替换为 adapter：

- `digest_proposal`
- `search_memory`
- `memory_stats`
