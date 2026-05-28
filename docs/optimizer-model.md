# Optimizer 模型

`optimizer` 层是 DSPy / GEPA / MIPRO / BootstrapFewShot 的本地控制契约。
它不直接依赖这些包，而是先记录可审计、可回放、可比较的优化事实：

```text
SkillVersion
  <- OptimizationRun
       <- DatasetSplit
       <- EvalGate
       <- holdout metrics
```

## 为什么单独成层

GEPA 这类 Pareto-based optimizer 需要大量样本、清晰 eval gate 和 holdout
验证。它不应该实时插进每次 QA 或写作请求里，而应该作为离线 compiler：

```text
Proposal / Argilla feedback / trace
  -> dataset split
  -> DSPy/GEPA optimize
  -> candidate SkillVersion
  -> promptfoo regression + holdout gate
  -> approve 后升为默认 skill
```

因此主仓库先固化四个对象：

- `SkillVersion`：一个 prompt / skill / module 的版本。
- `OptimizationRun`：一次 manual / dspy / gepa / mipro 优化尝试。
- `DatasetSplit`：train / dev / holdout 数量，防止只看训练集。
- `EvalGate`：holdout 最小样本数和指标阈值。

## 本地存储

权威存储是 `.omni/optimizer.sqlite3`：

```bash
PYTHONPATH=src python3.12 -m omni_hub.cli optimizer-skill-register \
  --skill-id qa --version v1 --domain research \
  --prompt-path prompts/qa/v1/system_prompt.md

PYTHONPATH=src python3.12 -m omni_hub.cli optimizer-run-record \
  --skill-id qa --optimizer gepa \
  --from-version v1 --to-version v2 \
  --train-count 120 --dev-count 40 --holdout-count 30 \
  --metric faithfulness=0.93 \
  --threshold faithfulness=0.90 \
  --min-holdout-count 20 \
  --pareto-candidates 6
```

`harness-compile` 现在会自动登记一个 `OptimizationRun` 和一个
`SkillVersion`。因为当前 fallback 没有 holdout gate，它的结果默认是
`needs_review`，不能自动升为默认 skill。

## QA skill 的推荐闭环

第一条真实优化链路应从 QA skill 开始：

```text
question + context + gold answer + evidence requirements
  -> promptfoo eval case
  -> Argilla accepted/rejected/edit spans
  -> DSPy dataset
  -> GEPA optimize
  -> qa:v2 candidate
  -> holdout gate
```

只有当 `EvalGate` 通过，且 citation / faithfulness / refusal 行为不退化，
下一版 skill 才能从 `candidate` 升级为默认版本。
