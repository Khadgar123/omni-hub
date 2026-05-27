# 自进化 Agent Harness

目标不是再做一个聊天应用，而是把工程开发、知识整理、写作和评测都放进同一个可回放的数据飞轮。

## 需求分层

| 层 | 需求 | 落地项目 |
| --- | --- | --- |
| 工程迭代能力 | 从 issue/task 到 patch、测试、回归、提交建议 | SWE-agent fork |
| 评测和 CI | prompt、RAG、agent、领域任务的回归测试和红队测试 | promptfoo fork |
| 人类偏好飞轮 | 保存 accepted/rejected 句子、候选选择、修改理由 | Argilla fork |
| 动态知识记忆 | 来源、时间、关系、冲突、冗余、过期信息识别 | Graphiti fork |
| 观测和实验追踪 | trace、成本、latency、judge 分数、实验 dashboard | Opik 候选，先评估再 fork |

## 输入层规范化

每次任务都必须先变成 `Task Packet`。这避免把“写得更好”“信息量更高”这种愿望留在聊天上下文里。

```yaml
task_type: academic_writing
goal: 写出信息密度高、证据充分的论文段落
audience: top-tier CS reviewer
sources:
  required:
    - paper_a.pdf
    - experiment_table.csv
claims_to_cover:
  - 方法解决的具体瓶颈
  - 与 baseline 的差异来源
constraints:
  citation_required: true
  no_generic_claims: true
  preserve_uncertainty: true
positive_examples:
  - 人类认可的旧段落
negative_examples:
  - 流畅但空泛的旧段落
judge_rubric:
  evidence_coverage: 0.3
  information_density: 0.25
  novelty_vs_source: 0.15
  citation_support: 0.2
  style_fit: 0.1
```

## 输出层规范化

输出不只保存最终答案。每次生成都要保留：

- 检索结果
- N 个候选输出
- 每个候选的 claim/evidence map
- judge 分数
- 人类选择/修改
- accepted sentences
- rejected sentences
- 失败原因
- 新增回归测试
- prompt/program 更新记录

## 防止能力坍缩

冗余识别只给建议，不直接删除。所有“删除”先变成：

```text
duplicate -> merge proposal
stale -> archive proposal
conflict -> review proposal
low-signal -> demote proposal
```

人类确认后再进入归档或合并，并保留可恢复记录。偏见知识也不能变成强约束，只能作为 judge、routing、few-shot 和人工审查的信号。

## Harness 循环

```text
Task Packet
  -> SWE-agent / model attempts
  -> promptfoo evals
  -> Opik candidate traces
  -> Argilla human preference
  -> Graphiti provenance and memory update
  -> regression case
  -> prompt/program update
```

核心资产是数据飞轮，而不是某一个 agent 框架。fork 可以替换，回放数据和评价标准不能丢。
