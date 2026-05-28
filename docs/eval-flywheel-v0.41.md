# Eval Flywheel v0.41 — Data Loop Architecture

**写于 2026-05-28** (单 session 第四轮架构审视)。基于 2026 Q2 SOTA brief
(Anthropic Demystifying Evals 2026-01, DSPy 3 GEPA, OpenAI evals, Argilla
preference-graduation, Iceberg time-travel, SWE-bench Verified +
LongMemEval / LegalBench / FinanceBench, UC Berkeley 反 reward-hacking
2026-04, Notion + Braintrust 飞轮 case study)。

## 1. 当前现状盘点 (v0.40 后,实事求是)

✅ 已有:
- `harness/models.py::JudgeRubric` — 5 维度评分 + extras
- `harness/domain_profiles.py::_DOMAIN_RUBRIC_OVERRIDES` — per-domain rubric weights
- `harness/preference.py::PreferenceStore` — accepted/rejected spans (空,无真实数据)
- `judge/` — HeuristicJudge + LLMJudge (打分基础)
- `ab/` — A/B test + win-rate aggregation (跑实验基础)
- `harness/dspy_compile.py` — PreferenceStore → SKILL.md body 编译

❌ 缺:
- **没有 benchmark dataset 层** — 没有 `vault/evals/<domain>/v0.X/seed.jsonl`
- **没有 EvalCase / EvalPack / EvalRun 一等公民数据类型**
- **没有版本固定 (version pinning)** — 重新跑相同 skill 在同一 case 上无法保证可复现
- **没有 promotion 机制** — PreferenceStore 高分 span 自动变 regression case 的路径不存在
- **没有 holdout 私有集** — 没区分公开 seed / 私有 holdout (Anthropic 必须的两套)
- **没有 contamination 防护** — 没监测 benchmark 是否被泄露进训练数据

简言之: 评分管道有,**测试集没有**。这是审核要求的"基础 benchmark"那一层。

## 2. SOTA 不变量 (从 brief 摘要)

1. **Anthropic 三类 eval**: capability (低分起步,有提升空间) / regression (graduated capability, 保持~100%) / calibration (rubric-based, 两专家独立打分一致才算合格)
2. **JSONL 是 lingua franca** (OpenAI evals + LangSmith + Braintrust 共识)
3. **20-50 cases 起步是 Anthropic 明确的初始规模,不是 1000+**
4. **版本固定 + atomic pointer swap** (Iceberg 模式) — 跑同一 case 同一 prompt 必须可复现
5. **三类 grader**: code-based (快+脆) / model-based (灵活+不确定) / human (gold) — 组合用
6. **80% public + 20% private holdout** (LLMEval-Logic 标准) — public 一旦被脏,holdout 顶上去 + 轮换
7. **Graduation rule** (Anthropic): capability 题通过率高 → 移入 regression
8. **Goodhart-on-rubric 反模式**: 不要让 LLM 写出"看起来满分但实际灌水"的答案
9. **Rolling vs Frozen**: frozen 是 SWE-bench Verified 模式 (commit-pin), rolling 是 LiveCodeBench / MIRAI 模式 (cutoff 之后才源源不断加题)
10. **PreferenceStore → benchmark graduation 飞轮** (NVIDIA Data Flywheel Blueprint 弃用但模式留下): 接收用户反馈 → 过滤 → 候选 → 人审 → 提升进 regression

## 3. omni-hub Eval Flywheel 架构

```
┌──────────────────────────────────────────────────────────────────────┐
│ Evaluation Plane (v0.41 新)                                           │
│                                                                       │
│ vault/evals/                                                          │
│   ├── _schema.md                          # global eval-pack schema    │
│   ├── <domain>/                                                       │
│   │   ├── v0.1/                                                       │
│   │   │   ├── seed.jsonl                  # public N 题                │
│   │   │   ├── holdout-private.jsonl       # gitignored,N/4 题          │
│   │   │   └── manifest.yaml               # rubric ref, grader recipe,│
│   │   │                                   # seed source SHA, version   │
│   │   └── v0.2/                           # 飞轮后的下一版              │
│   └── functional/<skill>/v0.1/...         # functional skill 同结构    │
│                                                                       │
│ .omni/eval_runs.sqlite3 — 每次 EvalRun 一行 (run_id, pack_id, score,  │
│                          per_case_pass, judge_name, trace_id, ts)     │
│                                                                       │
│ Promotion path:                                                       │
│   PreferenceStore[domain].accepted ≥ N_threshold                      │
│     → emit Proposal(kind=eval_pack_upgrade)                           │
│     → 人审 → v0.2 写出                                                 │
│     → 旧 v0.1 retain (历史可回放)                                       │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.1 EvalCase + EvalPack 数据契约

```python
@dataclass(slots=True)
class EvalCase:
    case_id: str                            # "eval_<8hex>"
    domain: str                             # 域 slug (research / finance / ...) 或 "functional:<skill>"
    eval_class: Literal["capability", "regression", "calibration"]
    question: str                           # 用户输入
    expected: str = ""                      # 期望答案 (capability/regression 用)
    expected_traits: list[str] = []         # rubric trait 必须出现 (calibration 用)
    rubric_weights: dict[str, float] = {}   # 覆盖 domain 默认 rubric
    metadata: dict[str, Any] = {}           # 来源,难度,tags,...
    graduated_from: str = ""                # PreferenceRecord id (graduated 才有)
    created_at: str = ...                   # ISO 8601 UTC

@dataclass(slots=True)
class EvalPack:
    pack_id: str                            # "research/v0.1"
    domain: str
    version: str                            # "v0.1"
    eval_classes: dict[str, int]            # {"capability": 20, "regression": 5, "calibration": 5}
    source: str                             # "PaperQA2-eval@<sha>", "hand-curated", "graduated-v0.0"
    rubric_ref: str                         # path to rubric or "domain_profiles.<domain>"
    holdout_path: str                       # gitignored
    created_at: str
    superseded_by: str = ""                 # next-version pack_id (bitemporal)
```

### 3.2 EvalRun + 评分

```python
@dataclass(slots=True)
class EvalRun:
    run_id: str
    pack_id: str
    judge_name: str                         # "heuristic" | "llm"
    skill_version: str                      # 哪个 SKILL.md 版本被测
    composite_score: float                  # 0..1 (跨 case 平均)
    per_case_results: list[CaseResult]
    started_at: str
    finished_at: str
    trace_id: str
```

每条 EvalCase 跑出 1 个 `CaseResult` (`pass: bool, score: float, judge_verdict: JudgeVerdict`)。

### 3.3 Promotion 飞轮规则

阈值:
- `PreferenceStore[domain].accepted_count >= 100` AND `days_since_last_bench_refresh >= 30` → 触发 candidate
- top 25 accepted spans (按 acceptance frequency) → EvalCase candidate
- 5 rejected spans (典型 failure mode) → EvalCase candidate (with `expected="hallucination" / "uncited" / "stale"`)
- emit `Proposal(kind=eval_pack_upgrade)` 携带候选 30 题 → 人审通过 → v0.X+1 写出

回归约束:
- v0.X+1 在 v0.X 的 retained-cases 上必须 ≥ 95% pass-rate (确保不退化)
- 不通过则 reject + 报告哪条新题让回归掉点

## 4. 初始化数据范围 + 来源 + 存储

### 4.1 19 个 domain 的 seed 来源 (SOTA brief 推荐)

| Domain | 来源 | 初始 N | 类型分布 |
|---|---|---|---|
| **research** | PaperQA2-eval 子集 + LongMemEval_S | 40 | capability 30 / regression 5 / calibration 5 |
| **engineering** | SWE-bench Verified Lite (50 picks) + LiveCodeBench fresh window | 50 | cap 35 / reg 10 / cal 5 |
| **finance** | FinanceBench 公开 + ConvFinQA 25 题 | 35 | cap 25 / reg 5 / cal 5 |
| **us-policy** | LegalBench (5 tasks × 5 cases) | 25 | cap 20 / reg 3 / cal 2 |
| **cn-policy** | 手工策展 gov.cn / 央行 | 25 | 全 capability |
| **international-relations** | MIRAI 子集 (GDELT-derived,model-cutoff 后产生,反污染) | 30 | cap 25 / reg 3 / cal 2 |
| **meta** | omni-hub commits + BUILD/USE/PIN 决策 (hand-curated) | 20 | 全 capability |
| **photography** | hand-curated 街拍/构图/曝光场景 | 25 | 全 calibration (主观) |
| **fashion** | hand-curated outfit 场景 | 25 | 全 calibration |
| **cooking** | hand-curated 菜谱 Q&A | 25 | cap 20 / cal 5 |
| **travel** | TravelBench / TripScore 样本 | 30 | cap 25 / cal 5 |
| **chat-relationships** | MT-Bench multi-turn 子集 + hand-curated 场景 | 30 | 全 calibration (MT-Bench 长度bias-aware judging) |
| **ai-progress** | 从近期 arxiv/openalex retrieval 策展 | 25 | cap 20 / cal 5 |
| **agent-systems** | SkillsBench-style BUILD/USE 决策 | 25 | cap 20 / cal 5 |
| **fitness-wellness** | PubMedQA 子集 + 手工 advisory 场景 | 25 | cap 15 / cal 10 |
| **marketing** | hand-curated 文案/增长 case | 25 | cap 15 / cal 10 |
| **enterprise** | hand-curated SOP / 公司 Q&A | 25 | 全 capability |
| **social-en** / **social-zh** | hand-curated 平台特定 reply | 20 each | 全 calibration |

总计 **~560 个 domain case**。

### 4.2 11 个 functional skill 的 seed 来源

| Skill | 来源 | N | grader 类型 |
|---|---|---|---|
| `retrieve` | query → expected-top-5 (从现有 cascade traces) | 30 | model-based |
| `context-pack` | query → expected-tier (minimal/standard/expanded) | 20 | code-based |
| `pptx-build` | SkillsBench 10 prompts × 6-axis rubric (Felo) | 10 | model + human |
| `chat-route` | query → expected-domain | 30 | code-based (exact match) |
| `inbox-route` | message → expected-action | 25 | code-based |
| `schedule-plan` | constraints → expected-schedule | 15 | code (hard constraints) + model (quality) |
| `order-propose` | intent → expected-RiskCheckResult | 20 | code (risk thresholds) |
| `app-report-build` | period → expected JSON shape | 10 | code |
| `wiki-ingest` | run_id → expected-Proposal shape | 15 | code |
| `propose-approve/reject` | proposal → expected-decision | 20 | hand-labeled |
| `harness-compile-skill` | domain → expected SKILL.md sections | 10 | code |

总计 **~205 个 functional case**。

### 4.3 存储 layout (落地)

```
vault/evals/
├── _schema.md                                # 由 init_evals() 自动写,bump 版本会重写
├── manifest.json                             # {"domain/v0.1": {meta...}, ...} 所有 pack 索引
├── research/
│   ├── v0.1/
│   │   ├── seed.jsonl                       # 30 cap + 5 reg + 5 cal = 40 题
│   │   ├── manifest.yaml
│   │   └── notes.md                         # 来源说明,curation 时间
│   └── v0.2/ (graduated)                    # 飞轮后写出
├── engineering/v0.1/{seed.jsonl, manifest.yaml, notes.md}
├── ... (剩余 17 个 domain)
└── functional/
    ├── chat-route/v0.1/...
    ├── pptx-build/v0.1/...
    └── ... (剩余 9 个 functional skill)

.gitignore:
vault/evals/*/holdout-private.jsonl
vault/evals/*/v*/holdout-private.jsonl
```

存储格式: **JSONL** (一行一个 EvalCase),与 OpenAI evals + LangSmith + Braintrust 兼容。Parquet/Iceberg 留作 trigger (~10k case 之后再切)。

### 4.4 v0.41 此 commit 实际落地的 (现实主义)

不一口气写 760 个 case。本轮交付:
- `src/omni_hub/evals/` 包: EvalCase / EvalPack / EvalStore / EvalRunner / promote_from_preference
- `eval-list` / `eval-show` / `eval-run` / `eval-promote` 4 个 CLI
- **5 个域的 minimal seed pack** (research / engineering / finance / meta / chat-relationships) — 每个 3-5 题作 schema sanity 测试
- 飞轮 promotion 函数完整 (PreferenceStore → 候选),只是缺 PreferenceStore 真实数据驱动
- 测试覆盖 store + runner + promote 逻辑
- 完整的 19 + 11 seed 留作 v0.42 dogfood 阶段填充

## 5. 升级 + 迭代机制 (回答审核第 3 问)

### 5.1 cadence (从 SOTA brief)

| 频率 | 操作 | 触发 |
|---|---|---|
| 每次 generation | preference span 累积 (现有) | 自动 |
| 每周 | weekly eval-run on current packs | launchd `make schedule-weekly` |
| 每月 | rotate calibration rubric + 重算 rubric weight | manual + Proposal |
| 每 release (skill 版本 bump) | full eval-run 全部 pack | manual |
| 当 PreferenceStore[domain].accepted ≥ 100 | promotion candidate | trigger detect |
| 当 retro-holdout score 显著 < public score | 怀疑污染 → rotate holdout | manual review |

### 5.2 graduation 决策树

```
PreferenceStore[domain].accepted_count ≥ 100
      │
      ├── compute top 25 accepted spans by frequency
      ├── compute 5 representative rejected spans (failure modes)
      ├── form candidate set (30 items)
      │
      ├── run candidate vs current v0.X on retained cases:
      │       v0.X+1 score on v0.X retained cases ≥ 95%?
      │       │
      │       ├── YES → emit Proposal(kind=eval_pack_upgrade)
      │       └── NO  → reject + log which new case caused regression
      │
      └── 人审 Proposal → 通过 → 写出 vault/evals/<domain>/v0.X+1/
```

### 5.3 反 Goodhart + 反污染

1. **私有 holdout** 永远不进 git;每月用一次,burned 之后强制轮换
2. **Judge family diversity**: HeuristicJudge + LLMJudge (Anthropic) + 偶尔人审 — 三类 grader 同跑
3. **Rubric rotation**: 每月微调 rubric weight (e.g. citation_support 0.25 → 0.20 / style_fit ↑) 防止 LLM 学会 "刷分模板"
4. **Retro-holdout** (Benchmark Inflation 论文):每季度从 PreferenceStore 抽 20 个早期高分 case 做 retro-test;如果分数显著低于 public bench 分,认为被污染
5. **Reward hacking 检测**: UC Berkeley 2026-04 工具 (BARM-style) 检查 case 是否被"作弊"刷分 (空答案/复制 expected/...)

### 5.4 SOTA 不抄

- **不引入 Apache Iceberg**: 单用户 < 10k case 用 JSONL + dirname version 够用;trigger 在 100k case
- **不引入 LangSmith**: 远程服务,与 local-first 冲突;LangSmith dataset 版本语义本地学
- **不引入 DSPy GEPA 全套**: GEPA 自动 mutate prompt 已经接进 harness-compile-skill,不重复
- **不抄 Notion/Braintrust 整套 ML-ops**: 那是团队规模,单用户 70 个 engineer 用 Braintrust 性价比错位

## 6. 工程不变量 (新增 HR #11)

- **HR #11 — Eval pack 不可手编辑 v0.X**: 一旦 freeze (写入 vault/evals/<domain>/v0.X/),只能新建 v0.X+1。审核失误的 case → 在 v0.X+1 修;原 v0.X 保留作历史回归。
- **HR #12 — Holdout 不进 git**: `vault/evals/*/holdout-private.jsonl` 加 .gitignore。Burn 之后强制 rotate (不可重用同一 holdout)。
- **HR #13 — graduation 必经 Proposal**: PreferenceStore → eval_pack_upgrade Proposal → 人审 → 写 v0.X+1。不允许自动 promotion。
- **HR #14 — capability vs regression vs calibration 分类必须显式**: 每个 EvalCase 必填 `eval_class`。混类 = 反模式。

## 7. v0.41 此 commit 实际交付

代码:
1. `src/omni_hub/evals/__init__.py` + `store.py` + `run.py` + `promote.py` (~600 LOC)
2. `src/omni_hub/cli/evals.py` (4 个 subcommand)
3. `src/omni_hub/builtins.py` 加 4 个 op
4. 5 domain × 3-5 seed case = ~20 JSONL 行 (放 `vault/evals/<domain>/v0.1/`)
5. `tests/test_v041_eval_flywheel.py` (~250 LOC)
6. CLAUDE.md / AGENTS.md HR #11-14 同步

不交付 (留 v0.42+):
- 完整 760 case seed (需要 dogfood 数据驱动)
- 自动 weekly launchd run (需要稳定的 skill 实现先)
- retro-holdout 自动监测 (需要历史 PreferenceStore 数据)
- LLMJudge real wiring (需要 ANTHROPIC_API_KEY 配置)

## 9. v0.42 增量 (审核闭环)

v0.41 暴露的四个 P1/P2 问题在 v0.42 全部闭:

1. **case_id 改 sha256** (HR #15) — `promote.py::_stable_case_id(domain, eval_class, span)` 用 `hashlib.sha256(...)[:12]` 派生,删掉 PYTHONHASHSEED-随机化的 `hash()`。同 span 在不同进程 / 不同机器都生成一致 case_id,符合飞轮的 idempotency 不变量。
2. **EvalRunner 真 SkillAdapter** (HR #16) — `evals/run.py` 加 `SkillAdapter = Callable[[EvalCase], str]` Protocol,`builtin_skill_adapters(workspace)` 自动注册 19 domain wiki + 11 functional skill 的 read-only adapter。Write-class (`calendar-add` / `order-propose` / 等) 走 describe-only,绝不副作用执行。`pick_adapter(case)` 三规则查表 (explicit `metadata.skill_id` → `functional:<name>` → `<domain>-wiki`)。原 echo-as-candidate 退到 `--echo-only` debug flag。
3. **全覆盖 seed** — `scripts/seed_eval_packs_v042.py` 写 14 + 11 = 25 个新 pack (5 case 每个 = 3 capability + 1 regression + 1 calibration)。合上 v0.41 的 5 个共 **30 pack / 150 case**,触达 19 domain × 5 + 11 functional × 5 的 smoke 底线。
4. **Holdout 双因子** (HR #12 加强) — `EvalStore.list_cases(include_holdout=True)` 必须先设 `OMNI_EVAL_HOLDOUT=1` 环境变量,否则 raise `HoldoutAccessDenied`。CLI typo 不会再意外把 private case stdout 泄露。`OMNI_EVAL_HOLDOUT` 是单 session 一次性的 opt-in,burn 后还是要 rotate。

v0.42 测试: `tests/test_v042_eval_adapter_holdout.py` (13 case,覆盖 sha256 跨进程稳定 / pick_adapter 三规则 / 30-pack smoke / env-gate)。

## 8. 参考 SOTA 来源 (从 brief)

- [Anthropic — Demystifying evals for AI agents (2026-01)](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [DSPy GEPA Optimizer](https://dspy.ai/api/optimizers/GEPA/overview/)
- [OpenAI evals — Working with evals (2026 docs)](https://developers.openai.com/api/docs/guides/evals)
- [Braintrust datasets](https://www.braintrust.dev/docs/core/datasets)
- [SWE-bench Verified leaderboard](https://benchlm.ai/coding)
- [LongMemEval-S](https://github.com/xiaowu0162/longmemeval)
- [FinanceBench, ConvFinQA, LegalBench](https://kili-technology.com/blog/domain-specific-llm-benchmarks-guide)
- [MIRAI — model-cutoff resistant benchmark](https://arxiv.org/pdf/2407.01231)
- [Anthropic Constitutional AI — preference-pair pattern](https://arxiv.org/pdf/2212.08073)
- [UC Berkeley CRDI — 8 major agent benchmarks broken via reward hacking (2026-04-12)](https://rdi.berkeley.edu/blog/trustworthy-benchmarks-cont/)
- [Iceberg time-travel — dataset versioning model](https://lakefs.io/blog/iceberg-time-travel/)
- [Benchmark Inflation: Retro-Holdouts (arXiv 2410.09247)](https://arxiv.org/html/2410.09247v1)
- [NVIDIA Data Flywheel (pattern lives, blueprint deprecated 2026-04)](https://github.com/NVIDIA-AI-Blueprints/data-flywheel)

---

_本文档为 v0.41 实施真源。具体实现 + 测试 + commit 见同版本提交记录。_
