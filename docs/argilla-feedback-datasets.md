# Argilla 反馈数据集

Argilla 在本项目里是 **人类反馈 UI / 标注数据集层**，不是工作流 source of
truth。权威状态仍在 `.omni/proposals.sqlite3`，优化事实仍在
`.omni/optimizer.sqlite3`。

## 数据流

```text
ProposalStore pending rows
  -> argilla-export-proposals
  -> Argilla 人审（approve / edit / reject / insufficient_context）
  -> argilla-sync-feedback
  -> ProposalStore state + PreferenceStore JSONL
  -> harness-compile / promptfoo / DSPy / GEPA
```

## 数据集契约

当前默认数据集是 `omni_proposal_review_v1`：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli argilla-schema \
  --dataset omni_proposal_review_v1
```

字段：

- `title` / `summary`：Proposal 摘要，方便人快速扫。
- `candidate_text`：真正要审的候选输出。
- `source_paths`：来源路径，换行分隔。
- `payload_json`：完整 payload 派生视图，只用于排查，不作为训练字段。

问题：

- `decision`：`approve` / `edit` / `reject` / `insufficient_context`。
- `faithfulness`、`citation_support`、`information_density`、
  `uncertainty_calibration`：1-5 分。
- `corrected_text`：当 decision 是 `edit` 时写入修订版本。
- `review_reason`：必须写原因；GEPA 这类 optimizer 需要文本反馈。

元数据：

- `proposal_id`、`kind`、`state`、`source_task_id`、`artifact_id`。
- `domain`、`skill_id`、`skill_version`。
- `model`、`tokens_total`、`cost_usd`。
- `schema_version`。

## 导出待审 Proposal

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli argilla-export-proposals \
  --output .omni/argilla/proposals.jsonl \
  --state pending \
  --kind generation \
  --domain research \
  --skill-id qa \
  --skill-version v1
```

这个命令是 `LOCAL_WRITE` 操作，经过 `OperationRunner`，会写 audit event。
输出 JSONL 每行是一个 Argilla-ready record：

```json
{
  "external_id": "proposal-id",
  "fields": {
    "candidate_text": "..."
  },
  "metadata": {
    "proposal_id": "proposal-id",
    "domain": "research",
    "skill_id": "qa",
    "skill_version": "v1"
  },
  "suggestions": [
    {"question_name": "decision", "value": "approve"}
  ]
}
```

## 同步人审反馈

从 Argilla 导出的反馈 JSONL 至少需要保留 `external_id`、`metadata` 和
`responses`：

```json
{
  "external_id": "proposal-id",
  "metadata": {
    "domain": "research",
    "skill_id": "qa",
    "skill_version": "v1"
  },
  "responses": [
    {
      "user_id": "reviewer-1",
      "values": {
        "decision": {"value": "edit"},
        "review_reason": {"value": "补充引用并删除空泛判断"},
        "corrected_text": {"value": "修订后的答案 [1]。"},
        "faithfulness": {"value": 4},
        "citation_support": {"value": 5}
      }
    }
  ]
}
```

同步：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli argilla-sync-feedback \
  --input .omni/argilla/feedback.jsonl \
  --preference-root .omni/preference
```

映射规则：

| Argilla decision | Proposal state | Preference decision |
| --- | --- | --- |
| `approve` / `accepted` | `approved` | `accepted` |
| `edit` / `edited` | `approved` | `edited` |
| `reject` / `rejected` | `rejected` | `rejected` |
| `insufficient_context` | `rejected` | `rejected` |

`PreferenceStore` 会保留 `candidate_text`、`edited_text`、`reason`、reviewer
和评分。后续 `harness-compile` 只读取 accepted/edited 作为正例，并读取 rejected
作为负例。

## 质量规则

- 不要把 pending Proposal 直接当训练样本。
- 不要把模型 suggestion 当 human response。
- `review_reason` 尽量写成可复用的规则，例如“必须保留矛盾信息”、
  “不能推断未给出的指标”，而不是只写“好/不好”。
- rejected 样本不要删除，它们是防止能力坍缩的反例库。
- 每个领域单独设置 `domain` 和 `skill_id`，避免科研、工程、聊天、金融等领域互相污染。
