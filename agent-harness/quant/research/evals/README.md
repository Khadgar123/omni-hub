# quant-framework evals — "the skill is the contract"

Grade the **contract**, not the answer. Three axes (2026 SOTA):

| Axis | What it checks | Where it lives | Gate |
|---|---|---|---|
| **Dispatch** | the skill description COVERS crypto-state queries + steers equities away | main repo `tests/test_crypto_read_dispatch.py` | pytest (CI) |
| **Output / Trajectory** | the engine output honours the discipline (disclaimer baked in · narrative is prose · §②b structure BEFORE §③ · per-level §④ flow · double-bottom ≠ lower-low) | `tests/test_framework_contract.py` ← `quant_framework.jsonl` | pytest (CI) |
| **Subjective** (advisory) | is the narrative readable + well-calibrated (no prediction) | `omni-hub judge-evaluate` (below) | not CI (LLM optional) |

**The deterministic tests ARE the gate** — they're plain pytest, so the existing CI run enforces them
(pass/fail = the threshold). No separate harness.

## Golden set — `quant_framework.jsonl`

One JSON object per line: `{id, kind: structure|output, ...spec, expect}`. **Append one row per miss**
(the loop: a miss in `../error-ledger.md` → a golden case here → a rule in the skill → a regression
test). `structure` rows carry a `path` (ramp segments → synthetic bars); `output` rows assert on the
live read's `narrative` / `report`. The runner (`tests/test_framework_contract.py`) is generic — add a
row, no test-code change.

## Run

```bash
# the gate (deterministic, no network):
python -m pytest tests/test_framework_contract.py tests/test_framework.py        # in the quant venv
PYTHONPATH=src python3.12 -m pytest tests/test_crypto_read_dispatch.py            # in the omni-hub repo
```

## Optional subjective layer — `omni-hub judge-evaluate` / `ab-test`

A live narrative can be spot-checked by the Judge LLM framework. **Caveat (measured):** the default
domain rubrics target *cited research* (evidence-coverage / citation-support), so a mechanical
state-read scores low for the wrong reason. **Reweight to the axes that matter** for a state-read —
style-fit + uncertainty-calibration — and treat the score as advisory, not a gate:

```bash
python -m quant.framework --symbol BTCUSDT > /tmp/nar.txt        # quant venv
PYTHONPATH=src python3.12 -m omni_hub.cli judge-evaluate --domain finance --judge heuristic \
  --rubric-citation-support 0 --rubric-evidence-coverage 0.1 \
  --rubric-style-fit 0.45 --rubric-uncertainty-calibration 0.45 \
  --candidate file:///tmp/nar.txt
# A/B two report/narrative versions with the same reweighting:  omni-hub ab-test --domain finance ...
```

`--judge llm` needs ccLoad or `ANTHROPIC_API_KEY`; `heuristic` always works. A crypto-specific rubric
(or a 20th "crypto" judge domain) would make this a real gate — deferred; the deterministic contract
tests cover the must-haves today.
