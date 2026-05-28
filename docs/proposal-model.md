# 提案层模型

自动化系统不应该直接改写稳定知识库。`Proposal[T]` 是项目里 **唯一的**
"等待人审"原语——所有 agent / 调度任务的写操作都被收敛到这一层，
人通过 `propose-list` / `propose-approve` / `propose-reject` 把控产出。

## 统一 Proposal 数据模型

v0.7 起，KnowledgeProposal / RedundancyProposal 的两套并行实现被合并成一个
统一的 `Proposal` 类型（[src/omni_hub/proposals.py](../src/omni_hub/proposals.py)），
通过 `kind` 字段区分子类：

```python
@dataclass
class Proposal:
    proposal_id: str
    kind: str               # knowledge | duplicate | stale | conflict | low_signal | generation
    state: str              # pending | approved | rejected
    payload: dict           # kind-specific（entities/relations、source_paths、generation text 等）
    confidence: float
    suggested_action: str
    title: str
    summary: str
    source_path: str
    source_paths: list[str]
    source_task_id: str | None
    reason: str
    decided_by: str
    decided_at: str | None
    created_at: str
```

| kind | 来源 | payload 主要字段 | suggested_action |
| --- | --- | --- | --- |
| `knowledge` | `propose_knowledge` op，扫 vault note | `entities`、`relations` | `digest_into_memory` |
| `duplicate` | `harness-redundancy-scan` | `source_paths`（同标题同摘要） | `merge_proposal` |
| `stale` | `harness-redundancy-scan` | 单个老 source | `archive_proposal` |
| `conflict` | `harness-redundancy-scan` | 同标题不同摘要 | `review_proposal` |
| `low_signal` | `harness-redundancy-scan` | 低信息密度摘要 | `demote_proposal` |
| `generation` | `omni-hub worker --lane claude/codex/openhands` 成功 artifact | `text`、`model`、`tokens_*`、`cost_usd`、`artifact_id` | `review_generation` |

## 存储

- **权威存储**：`.omni/proposals.sqlite3`（WAL）。所有状态变更（approve / reject）都改写这一张表。
- **派生视图**：knowledge proposal 会同时渲染 `.omni/proposals/<id>.{json,md}` 卡片给 Obsidian 用，
  方便人在 vault 里直接 review。这是 *derived*，不是 source of truth。
- v0.6 的 `.omni/proposals/redundancy.jsonl` 双轨在 v0.7 移除——
  reports 现在直接查 ProposalStore，不再 append 到 jsonl。

## 写入边界（硬约束）

所有"agent 产出的内容"必须先变成 `Proposal[T]`，由人 approve 后才进入稳定层：

- `propose-note` / `propose_knowledge` op：扫 vault note → 写 knowledge proposal
- `harness-redundancy-scan`：扫 memory → 写 duplicate / stale / conflict / low_signal proposal
- `omni-hub worker --lane claude/codex/openhands`：headless agent 成功 artifact →
  自动写 generation proposal（[cli/worker.py:_artifact_to_proposal](../src/omni_hub/cli/worker.py)）
- 任何新增 agent worker lane 必须加入 `_GATED_LANES` 集合；只有 `python` lane（即 OperationRunner-audited
  确定性 op）可以豁免，因为它的写已经过 policy + audit。

## CLI 接口

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli propose-list                          # 看全部
PYTHONPATH=src python3.12 -m omni_hub.cli propose-list --state pending          # 只看待审
PYTHONPATH=src python3.12 -m omni_hub.cli propose-list --kind generation        # 只看 agent 产出
PYTHONPATH=src python3.12 -m omni_hub.cli propose-approve --id <pid> --reason "..."
PYTHONPATH=src python3.12 -m omni_hub.cli propose-reject  --id <pid> --reason "..."
```

`propose-approve` / `propose-reject` 是 LOCAL_WRITE 操作、过 `OperationRunner`，
所以每次决策都会写一条 `.omni/audit/events.jsonl` 审计事件。

## 状态机

```
                ┌─────────────────────────────────────────────┐
                │                  pending                    │
                └───────────┬─────────────────────────┬───────┘
                            │ approve                 │ reject
                            ▼                         ▼
                  ┌──────────────────┐      ┌──────────────────┐
                  │     approved     │      │     rejected     │
                  └──────────────────┘      └──────────────────┘
```

approve 不自动触发任何下游动作——为了避免单点错误连锁。具体后续：

- `knowledge` 类 approved 后由人手动调 `memory-digest-proposal --proposal <id>`
  把 entities/relations 灌进 memory（或写一个未来的"auto-digest approved knowledge" 调度 op）。
- `duplicate`/`stale`/`conflict`/`low_signal` 类 approved 当前只是状态标记，下一阶段
  会接 vault 上的实际合并/归档动作。
- `generation` 类 approved 表示"这段 agent 输出值得入库"，下一阶段会接到 Argilla
  preference store 作为正例。

## 防能力坍缩

`Proposal[T]` 是"防能力坍缩"的工程实现：

- 没有任何代码路径可以让 agent 跳过 propose 直接改 vault / memory / registry
  （[AGENTS.md 工程硬约束](../AGENTS.md) 第 2 条）。
- 拒绝的 proposal 不删——`state=rejected` 留在 SQLite，可以 `propose-list --state rejected` 复盘。
- 高风险 worker（claude / codex / openhands）默认 `permission-mode=plan` + `allowedTools=Read`，
  写操作只能通过 proposal 出现。
