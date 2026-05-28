# 记忆层模型（v0.7）

万象中枢的记忆分多个独立 SQLite store + 一个 audit JSONL，**互不交叉**：

```
.omni/memory.sqlite3      ← 长期知识 (documents/entities/relations)
.omni/proposals.sqlite3   ← 等待人审的写 (统一 Proposal[T])
.omni/queue.sqlite3       ← 后台任务队列 (Task)
.omni/audit/events.jsonl  ← 所有 operation 的审计日志
```

外加一个**派生层**（不是 source-of-truth）：

```
.omni/proposals/<id>.{json,md}   ← 给 Obsidian 看的 knowledge proposal 卡片，
                                   状态以 SQLite 为准
```

## MemoryStore（长期知识）

[memory.py](../src/omni_hub/memory.py)：三表 SQLite WAL。

```sql
documents (
    source_path TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL,
    proposal_id TEXT NOT NULL,   -- 来源的 Proposal[T]
    updated_at  TEXT NOT NULL
);

entities (
    name        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    evidence    TEXT NOT NULL,
    confidence  REAL NOT NULL,   -- 0..1
    updated_at  TEXT NOT NULL
);

relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT, relation TEXT, target TEXT,
    evidence TEXT, confidence REAL,
    source_path TEXT, proposal_id TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(source, relation, target, source_path)
);
```

PRAGMAs（v0.8 P0-3 起每个连接都设）：

```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 30000;
```

## 写入：`digest_proposal` 是唯一入口

```
vault note → propose-note → Proposal(kind="knowledge", state="pending")
                                    ↓ propose-approve（人审）
                                    ↓
                            memory-digest-proposal
                                    ↓
                           MemoryStore.digest_proposal(Proposal)
                                    ↓
                  upsert documents/entities/relations
```

`digest_proposal` 只接受 `Proposal(kind="knowledge")`——其他 kind 抛 ValueError。
这是"防能力坍缩"硬边界：**任何写 vault/memory 的路径都先经 Proposal[T] 等人审**。

## 读取

| 入口 | 行为 |
|---|---|
| `MemoryStore.search(query, limit=10)` | 全文匹配（lower-case substring）打分，返回 documents+entities+relations 混合结果 |
| `MemoryStore.list_documents(limit=100)` | 仅 documents，按 updated_at DESC（供 `reports/core.py` 和 `harness/redundancy.scan` 使用） |
| `MemoryStore.search_documents(query, limit=20)` | 仅 documents 的 LIKE 子串匹配（与 `search` 区别：不打分、专给 Graphiti bridge fallback 用） |
| `MemoryStore.stats()` | `{documents, entities, relations}` 计数 |

## Graphiti bridge 关系

[harness/graphiti_bridge.py](../src/omni_hub/harness/graphiti_bridge.py)：`LocalSQLiteBackend` 是 `MemoryStore.list_documents` /
`search_documents` 的**薄包装**（v0.7 起委托，不再自己写 SQL），暴露 `KnowledgeRecord` 数据形状给 Graphiti API。

未来真接入 Graphiti fork 时，`graphiti_bridge.get_backend(prefer="graphiti")` 会切到 `GraphitiBackend`，
MemoryStore 仍是 fallback。

## 与 ProposalStore 的关系

```
MemoryStore               ProposalStore
    ↑                          │
    │ digest_proposal           │ store (any kind)
    │                          │
    └── 经 propose-approve ──────┘
```

`ProposalStore` 写完后**不会自动**进 MemoryStore——必须人 approve 后再调 `memory-digest-proposal`。
这是有意的：approve 表示"这个 proposal 是真的"，digest 表示"实际灌入 memory"，两步分开避免连锁错误。

详见 [proposal-model.md](proposal-model.md) 的 "approve 不自动触发任何下游动作"段。

## 三层 memory（v0.8 规划，未实现）

业界 2026 标配（Letta / Mem0）：

| 层 | 容量 | 内容 | 现状 |
|---|---|---|---|
| **core** | ~10KB | 长期不变的事实（user identity、preferences、recurring entities） | 未实现 |
| **recall** | session-aged | 最近会话的 high-confidence 摘要 | 未实现 |
| **archival** | 不限 | 完整 documents/entities/relations（**目前的 MemoryStore 全部归这层**） | ✓ 已有 |

v0.8 P2-4 计划：扩 `MemoryStore` 三张表 + `memory-recall(query, tier=...)` CLI + preference flywheel 把 accepted 内容
promote 到 recall。

## 调试技巧

- 看现在 MemoryStore 多大：`omni-hub memory-stats`
- 看某主题：`omni-hub memory-search --query "Graphiti"`
- 看待审 knowledge proposal：`omni-hub propose-list --kind knowledge --state pending`
- 看 MemoryStore 内部状态（开发场景）：`sqlite3 .omni/memory.sqlite3 'SELECT * FROM documents LIMIT 5;'`

## 性能上限

SQLite WAL + 单写线程在 Apple Silicon 测试：

- 单 user 单机 documents ≤ 100k：日常 search/digest 都 < 50ms
- 写入并发 ≤ 8 worker：`PRAGMA busy_timeout=30000` 足以撑过任何 30s 内的写锁等待
- 超过 ~10 万 documents 后 search 性能开始下降（线性扫描）——届时考虑加 SQLite FTS5 index 或迁 Graphiti
