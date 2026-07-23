# Error Ledger — analysis misses & the fixes that pin them

The framework gets durable not by patching one bug at a time but by turning **every caught miss**
into five things: a **code fix** (L1, deterministic), a **rule** in the skill (L2), a **falsification
habit** (L3), a **regression test** (L4), and a **golden eval case** (`evals/quant_framework.jsonl`,
graded by the CI contract gate `tests/test_framework_contract.py`). This file is the narrative + the
index. New misses append here.

> Discipline that prevents *unknown future* errors (not just the ones below): **state the opposite
> read and its evidence before concluding** (L3). Most misses here would have been caught by asking
> "what would make the opposite true?" once, out loud, against the data.

---

## 2026-06-02 — called ETH "weaker" when its 4h was a double-bottom basing above BTC's downtrend

- **Symptom.** Told the user ETH was the weaker/leading-down leg vs BTC. User: "ETH 的 4h 明显比 BTC
  强,有双底迹象。" They were right.
- **Evidence I ignored.** My own `--full` ① table already said `BTC 4h=down/ADX40/位置0.08`
  (trending down at the lows) vs `ETH 4h=range/ADX23/位置0.52` (basing mid-channel). The 4h swing
  lows confirmed it: ETH 1,965→1,954 (a W, +2.4% off the low, toward neckline ~2,040) vs BTC
  76,056→72,556→72,436→70,038 (descending lower-lows, −4.4% under its neckline).
- **Root causes.**
  1. *Discipline*: I narrated "ETH 更弱" from the 1d label + funding + an **invalid cross-asset raw
     taker-delta comparison** (−14,772 ETH vs −998 BTC, different contract scales) and let it
     override the 4h price structure I had in hand.
  2. *Capability*: `framework.py` had **no swing-structure layer** — it reduced each TF to
     {regime label, Donchian S/R, order-flow} and was structurally blind to double-bottoms. The
     package already had `quant.structure` (swings / BOS / CHoCH / 背驰) — the framework just never
     wired it in.
  3. The Donchian `[破]` flag conflated "broke and kept falling" with "wicked the low and reversed
     (double-bottom retest)."
- **Fix — L1 (code).** Wired `quant.structure` into `framework.py`: `_structure_by_tf` emits per-TF
  **BOS/CHoCH trend + dominant base/neckline + 背驰 + a conservative 双底/双顶 tag**, and
  `_flow_by_tf` emits **per-level taker-delta** (the 1m-bought / 5m-sold "bounce quality" baked in).
  `report()` gained §②b (structure) + a per-level §④; `narrate()` now states the 4h structure so the
  TL;DR is no longer blind.
- **Fix — L2 (rules).** Canonical in `.agents/skills/quant-framework/SKILL.md` §分析纪律 (this miss
  drove rules 1–4: read §①/§②b before any cross-level claim · "range at the lows" ≠ weak · no
  cross-asset raw delta · strength needs per-TF structural evidence).
- **Fix — L4 (test + eval).** `tests/test_framework.py::test_structure_flags_double_bottom_but_not_a_lower_low_downtrend`
  pins the W-vs-descending distinction; golden cases `struct-double-bottom` / `struct-lower-low` in
  `evals/quant_framework.jsonl` re-assert it through the CI contract gate (`tests/test_framework_contract.py`).

## 2026-06-02 — "bounce quality" had to be computed ad-hoc (per-level flow was missing)

- **Symptom.** To answer "1m 反弹什么质量" I had to write a throwaway script each time; the report
  only carried one 15m order-flow line.
- **Root cause.** `framework` computed order-flow on a single operating TF (15m), so the scale-split
  signature (1m bought, 5m sold into = distribution) was invisible in the report.
- **Fix.** `_flow_by_tf` + §④ per-level order-flow (L1); covered by
  `test_read_carries_structure_and_per_level_flow` (L4) + golden case `out-per-level-flow`.

## 2026-06-02 — the narrative didn't carry the disclaimer (caught by the new contract eval)

- **Symptom.** The `out-disclaimer` contract eval FAILED on its first run: `narrate()` returned the
  4-sentence read WITHOUT the disclaimer — it was appended only by `report()` / the CLI, so the
  `narrative` field used standalone (which the skill says to lead with) lacked the required
  "非投资建议" footer.
- **Root cause.** "Always append the disclaimer" was an *agent instruction*, not a *code guarantee*.
- **Fix.** Baked the disclaimer into `narrate()` (L1 — code guarantees it); de-duped `report()`/CLI.
  Pinned by golden case `out-disclaimer` in `evals/quant_framework.jsonl`.
- **Note.** Found by the eval gate itself on its first run — the loop working as designed.
