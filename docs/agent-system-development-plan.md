# Agent 系统与知识库开发设计

## 目标

万象中枢要做的是一个可迭代的 agent/知识库 harness，而不是一次性问答工具。核心目标是：

- 把模型能力、模型偏见、领域知识、人工偏好、评测结果和失败案例都变成可回放资产。
- 让下一次生成不再只靠抽奖，而是从已验证的输入包、检索策略、few-shot、judge rubric 和人类选择中继承改进。
- 识别冗余和低质量信息，但不让模型直接删除重要信息，避免知识和能力坍缩。

## 需求清单

| 编号 | 需求 | 开发含义 | 对应项目 |
| --- | --- | --- | --- |
| R1 | 最强工程开发和迭代能力 | issue/task -> patch -> test -> review -> regression | SWE-agent fork |
| R2 | 模型基础能力画像 | 记录模型编码、推理、写作、检索、长上下文、工具调用、成本和稳定性 | promptfoo + lm-evaluation-harness/HELM 参考 |
| R3 | 模型偏见知识 | 记录训练数据/训练方案带来的表达偏见、知识盲区、过度安全约束和低信息密度倾向 | HELM / TrustLLM / DecodingTrust / BBQ 参考 |
| R4 | benchmark + 数据飞轮 | 每次任务保存输入、候选、judge、人类选择、修改、失败原因和回归用例 | promptfoo + Argilla |
| R5 | 领域评测体系 | 每个领域有独立 task packet、rubric、judge 和反例库 | promptfoo + domain profiles |
| R6 | LLM-as-judge | 多 judge、可校准、可抽检，不能把单一 judge 当真理 | promptfoo + Opik 候选 |
| R7 | 知识问答 | 基于来源、时间、关系、置信度回答，不把聊天记忆当事实 | Graphiti + 本地 memory |
| R8 | 冗余识别 | duplicate/stale/conflict/low-signal 只生成 proposal，不直接删除 | Graphiti + proposal queue |
| R9 | 领域方案提出 | 在领域基础知识和约束下提出方案，而不是通用鸡汤 | domain profiles |
| R10 | skill/agent/harness 执行一致性 | skill 负责确定性动作，agent 负责计划执行，harness 负责评测和飞轮 | Omni Hub 主仓库 |
| R11 | 日常关系和消息整理 | 转发总结、关系上下文、日报/周报/月报、待办和情绪线索 | chat_relationships profile + capture/memory |
| R12 | 科研写作质量迭代 | 强制检索和引用只是输入条件，真正要保存 accepted/rejected 句子和 claim/evidence map | research profile + Argilla |

## GitHub 项目分层

> 2026-05-28 重新校准。原版决策表保留为历史，新决策见下。`manifest.json` 与 `agent-harness/README.md` 已同步。

### 现役 fork（4 个）

| 项目 | 决策 | 原因（2026 校准） |
| --- | --- | --- |
| SWE-agent | 已 fork（保留） | 退化为"最小 CI harness"角色：mini-SWE-agent ~100 行，做 model-vs-model 基准对比时仍最干净 |
| promptfoo | 已 fork | 仍是 eval CI 主流；将内嵌 RAGAS（faithfulness ≥ 0.9 作为硬 gate） |
| Argilla | 已 fork | 偏好飞轮主流；accepted/rejected → DSPy compile 的输入端 |
| Graphiti | 已 fork | LongMemEval 2026 实测 71.2%（GPT-4o），仍领先 Mem0 (49%)；商业 OMEGA 95.4% 闭源不可选 |

### 升格为 fork（3 个 pending-personal-clone）

| 项目 | 决策 | 原因 |
| --- | --- | --- |
| **DSPy** | 升格：依赖 → fork-pending | **无权重自进化的工程答案**。BootstrapFewShot + MIPROv2 把 Argilla 数据编译成下一版 prompt；没有它，accepted/rejected 永远只是数据，模型下一次还是写不好 |
| **OpenHands** | 新增：fork-pending | SWE-bench Verified 66–77%、72k★、enterprise-ready，是 2026 工程 agent 的"产品侧"主力；与 SWE-agent 错位（minimal CI vs 产品工作） |
| **Opik** | 升格：candidate → fork-pending | 2026 trace+cost+eval dashboard 已成熟，优于 Langfuse/Phoenix |

落地方式：`scripts/add_pending_harness_forks.sh` 在你 GitHub 上 fork 完上游后一键转 submodule；现在 `manifest.json` 已经把它们登记到 `pending_forks` 字段，`make harness-status` 可查。

### 持续依赖/参考（不 fork）

| 项目 | 角色 |
| --- | --- |
| LangGraph | agent workflow runtime；Claude Code Agent Teams + Codex CLI 已替代大部分编排，仅作参考 |
| lm-evaluation-harness / HELM / Lighteval | 模型基础能力画像（R2/R3），按需依赖 |
| Mem0 | 轻量记忆 fallback（chat_relationships 域），pip 接入即可，不 fork |
| RAGAS | 在 promptfoo 内当 evaluator 库使用 |
| FairJudge / BiasScope（论文） | 自实现为 promptfoo 的自定义 evaluator |
| Label Studio / DVC / MLflow | 规模化标注/数据/实验追踪，后续视需要接入 |

## 总体架构

```text
Omni Hub
├── Task Packet Layer
│   ├── domain profile
│   ├── sources and retrieval policy
│   ├── constraints and rubrics
│   └── positive/negative examples
├── Execution Layer
│   ├── skills: deterministic local operations
│   ├── agents: planning and tool use
│   └── SWE-agent: engineering patch loop
├── Evaluation Layer
│   ├── promptfoo eval suites
│   ├── LLM-as-judge ensemble
│   ├── domain rubrics
│   └── regression cases
├── Preference Layer
│   ├── Argilla datasets
│   ├── accepted/rejected sentences
│   ├── human edits
│   └── preference reasons
├── Memory Layer
│   ├── local SQLite memory
│   ├── Graphiti temporal graph
│   ├── provenance
│   └── redundancy proposals
└── Observability Candidate
    └── Opik traces / cost / latency / eval dashboard
```

## 输入层规范化

输入层的目标是把“我要更好”变成可执行条件。

统一 `Task Packet`：

```yaml
task_id: uuid
task_type: academic_writing | engineering | finance | policy | chat_relationships
goal: 这次任务要达成的具体结果
audience: 谁会使用或评价输出
domain_profile: research
sources:
  required: []
  optional: []
retrieval_policy:
  must_search: true
  min_sources: 3
  freshness_required: false
claims_to_cover: []
constraints:
  no_generic_claims: true
  citation_required: true
  preserve_uncertainty: true
positive_examples: []
negative_examples: []
judge_rubric:
  evidence_coverage: 0.3
  information_density: 0.25
  citation_support: 0.2
  style_fit: 0.1
  uncertainty_calibration: 0.15
human_review_required: true
```

关键原则：

- 检索不是目的，检索结果必须进入 claim/evidence map。
- 领域约束不是死规则，而是评价维度和生成上下文。
- 人类认可的句子必须成为正例，人类否定的句子必须成为反例。

## 输出层规范化

输出层的目标是把一次生成转化成可学习资产。

统一 `Generation Record`：

```yaml
task_id: uuid
model: deepseek-v4-pro
prompt_version: research_v3
retrieval_snapshot: []
candidates:
  - candidate_id: c1
    text: ...
    claim_evidence_map: []
    judge_scores: {}
    failure_tags: []
human_feedback:
  selected_candidate: c1
  accepted_spans: []
  rejected_spans: []
  edit_diff: ...
  preference_reason: ...
regression_case:
  should_keep: []
  should_avoid: []
  eval_thresholds: {}
```

关键原则：

- 生成 N 个候选，避免单样本随机性。
- judge 先排序，人类再选择，选择结果进入 Argilla。
- 低分输出不丢弃，要作为 negative examples。
- 人类修改后的版本是 gold output，但不自动泛化为所有场景规则。

## 领域知识 profile

机器可读 profile 已放在 `agent-harness/domain-profiles.json`。第一批领域：

- `engineering`
- `research`
- `photography`
- `fashion`
- `chat_relationships`
- `finance`
- `policy`
- `international_relations`

每个 profile 定义：

- `goal`
- `required_context`
- `proposal_rules`
- `judge_dimensions`

开发时所有 skill/agent/harness 先读取领域 profile，再决定检索、生成、评测和人工确认策略。

## Skill、Agent、Harness 的边界

| 层 | 做什么 | 不做什么 |
| --- | --- | --- |
| Skill | 确定性、本地、可重复动作，如 status、capture、memory-search | 不做长链自主决策 |
| Agent | 计划、调用工具、生成候选、修复代码、整理资料 | 不直接改写长期记忆，不直接删除信息 |
| Harness | 跑 eval、记录候选、收集偏好、生成回归用例、更新 prompt/program | 不把单次 judge 结果当绝对真理 |

执行顺序：

```text
Task Packet
  -> domain profile
  -> retrieval/context bundle
  -> agent/skill execution
  -> candidate outputs
  -> judge reports
  -> human preference
  -> memory/proposal update
  -> regression test
```

## 冗余识别与防坍缩策略

冗余识别分四类：

```text
duplicate: 内容重复，可合并
stale: 时间过期，可归档
conflict: 与其他知识冲突，需人工审查
low_signal: 空泛/低证据/低复用价值，可降权
```

禁止直接删除。只能产生 proposal：

```text
merge proposal
archive proposal
conflict review proposal
demote proposal
```

防能力坍缩规则：

- 保留反例和少数派选择，不让单一风格吞掉多样性。
- judge 至少区分事实、风格、信息密度和领域适配。
- 人类偏好是上下文相关偏好，不是全局硬规则。
- 每次 prompt/program 更新前跑回归集，防止科研写作变流畅但空泛。

## 日常消息和关系整理

第一版功能：

- 转发内容总结：提取来源、主题、立场、证据、待办。
- 日报/周报/月报：按项目、人物、平台、情绪线索、待办聚合。
- 关系上下文：保存互动事实，不做过度心理诊断。
- 基于知识问答：回答时给来源、日期、置信度和不确定点。

对应 profile：`chat_relationships`。

## 科研写作问题的通用化

问题不是“强制检索”不够，而是检索之后缺少闭环：

```text
source -> claim -> sentence -> judge -> human choice -> positive/negative examples -> regression
```

要让下一次写得更好，必须保存：

- 人类认可的句子为什么好。
- 人类拒绝的句子为什么空泛。
- 每句话对应哪些证据。
- 哪些 prompt 版本改善了信息密度。
- 哪些改善牺牲了准确性或可读性。

因此科研写作第一版不要追求自动完美，而要做“人类监督下的数据飞轮”。

## 开发路线

### Phase 1: Contract

- 新增 `Task Packet` 和 `Generation Record` 数据模型。
- 读取 `agent-harness/domain-profiles.json`。
- CLI 支持创建/验证 task packet。

### Phase 2: Evaluation

- 接入 promptfoo，建立 research/engineering/chat 三个最小 eval suite。
- 支持 LLM-as-judge 输出结构化分数。
- 每次输出产生 regression candidate。

### Phase 3: Preference

- 接入 Argilla，保存 accepted/rejected spans。
- 支持人工选择候选、编辑 diff、偏好原因。
- 从偏好数据生成 positive/negative examples。

### Phase 4: Memory

- 接入 Graphiti，写入 entity/relation/time/provenance。
- 冗余识别只生成 proposal。
- 问答返回 source/date/confidence。

### Phase 5: Engineering Loop

- 接入 SWE-agent，跑 issue-to-patch。
- patch 必须跑测试和 promptfoo regression。
- 失败案例回写 harness。

### Phase 6: Observability

- 本地 pilot Opik。
- 如果 trace/eval/cost dashboard 明显优于轻量自建，再 fork。

## 当前已落地的主仓库模块

已落地（2026-05-28）：

```text
src/omni_hub/harness/models.py          # TaskPacket / GenerationRecord / Candidate / JudgeScore / HumanFeedback
src/omni_hub/harness/ensemble.py        # 多模型 fanout via ccLoad
src/omni_hub/proposals.py               # 统一 Proposal[T] + SQLite ProposalStore
src/omni_hub/optimizer/                 # SkillVersion / OptimizationRun / EvalGate
src/omni_hub/queue.py                   # AgentJob Queue + visibility timeout + lease fencing
src/omni_hub/workers/                   # Artifact / WorkerAdapter / builtin / claude / codex
src/omni_hub/reports/                   # 日/周/月报构建
scripts/launchd/                        # macOS 后台调度模板
```

CLI 子命令已注册：

- `harness-task-validate --file packet.json`：验证 Task Packet 字段+权重和
- `harness-ensemble --prompt "..." --model A --model B [--dry-run]`：N 路 candidates，落 GenerationRecord JSON
- `task-enqueue` / `task-list` / `worker --lane <lane>`：后台队列和 worker pool
- `propose-list` / `propose-approve` / `propose-reject`：统一人工审批出口
- `optimizer-skill-*` / `optimizer-run-*`：记录 DSPy/GEPA-ready skill 版本和优化运行
- `schedule-tick --period daily|weekly|monthly`：入队例行扫描和报表任务

Makefile 目标已加：`make harness-status`、`make harness-add-pending dspy openhands opik`、`make schedule-install-dry`、`make worker-python`。

下一步（按 12 周路线）：

```text
src/omni_hub/harness/judge_ensemble.py  # 多 judge + BiasScope 五维偏差自检
src/omni_hub/harness/grounding.py       # atomic claim + per-claim citation 强制
src/omni_hub/harness/dspy_compile.py    # 接真实 DSPy / GEPA / MIPRO optimizer
src/omni_hub/workers/openhands.py       # OpenHands adapter，lane=openhands
src/omni_hub/mcp_server.py              # 将 task/proposal/memory/harness 暴露给 MCP client
```

设计原则不变：先做主仓库 contract，再接外部服务。外部 fork 替换不动摇 `TaskPacket` / `GenerationRecord` 这两个核心契约。
