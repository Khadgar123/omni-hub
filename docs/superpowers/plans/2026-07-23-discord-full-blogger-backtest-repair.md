# Discord Full Blogger Backtest Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan task-by-task,
> and `superpowers:test-driven-development` for every production change.

**Goal:** Produce a reproducible, evidence-bound result for all 132 explicit
Discord targets: current calls, complete per-target funnels, lifecycle-derived
curation, and conservative point-in-time backtests for every trade whose
identity, media, instrument and market data are sufficient.

**Architecture:** Preserve the frozen 842,425-message corpus and old pilot
artifacts. Build a new v2 namespace beside them. A hash-bound run contract feeds
an append-only SQLite ledger. Deterministic reducers materialize identity,
message decisions, media occurrences, events, lifecycles, market coverage,
trades and 132 target reports. Heavy OCR/video/model work stays under
`agent-harness`; all durable writes run through `OperationRunner`, and queued
semantic work commits with `TaskQueue` lease fencing.

**Tech Stack:** Python 3.12 stdlib in `src/omni_hub`, SQLite WAL/FULL,
`unittest`, existing Discord evidence/asset readers, local OCR/video subprocess
seams in `agent-harness`, existing quant environment and 1m market store.

## Frozen Inputs and Global Constraints

- Exact inventory:
  `discord-exports/v2/derivatives/blogger-results/exact-target-inventory-20260722-v6.json`,
  SHA-256
  `21065f955a6600cde196357a292f33e3d854ab66437a8d061c4641ceb069e691`.
- Historical corpus: 842,425 unique messages, commitment
  `9baac0174f4a411171a4f8d37ab5b4d3e2ed59fd20f57b90cbd9d95092e8365c`.
- Historical union is exactly `831915 + 11119 - 609`; never ingest all 23,978
  raw boundary rows.
- Historical upper bound `H=2026-07-21T00:57:18.979Z`.
- Report target IDs must equal the exact frozen 132-ID set, not merely have 132
  rows.
- Preserve the 123 private-archive enumeration blockers as
  `known_scope_only`; do not alter Discord permissions.
- Do not expose the bot token, signed URLs, message bodies, media logical keys,
  private holdout labels or raw model prompts in reports/logs/git.
- Do not overwrite old four-profile artifacts. New output root:
  `discord-exports/v2/derivatives/blogger-results/full-v2-<run-id>/`.
- Directories are `0700`; files are `0600`; publication is atomic and
  no-clobber.
- `make test` and quant tests must finish with zero failures and zero
  `ResourceWarning`.

---

### Task 0: Verify repository and skill-runtime preconditions

**Files:** none.

- [x] Run the mandated runtime audit before production code:

```bash
PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python \
  -m omni_hub.cli skill-list | jq '.output.skills | length'
```

Observed on 2026-07-23: `60`. This is not below the repository's runtime
claim, so `skill-sync --apply` is not required.

- [ ] Re-run the audit before the formal run. If the count is then below the
  generated registry/schema claim, run `skill-sync --apply` through its
  registered write operation before continuing.

---

### Task 1: Freeze the full-v2 run contract and append-only ledger

**Files:**

- Create: `src/omni_hub/discord_blogger_contract.py`
- Create: `src/omni_hub/discord_blogger_ledger.py`
- Create: `src/omni_hub/operation_receipts.py`
- Create: `tests/test_discord_blogger_contract.py`
- Create: `tests/test_discord_blogger_ledger.py`
- Create: `tests/test_operation_receipts.py`
- Modify: `src/omni_hub/runner.py`
- Modify: `tests/test_runner.py`
- Modify: `src/omni_hub/workers/builtin.py`
- Modify: `tests/test_workers.py`
- Modify: `src/omni_hub/queue.py`
- Modify: `tests/test_queue.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class BaselineRunContract:
    run_id: str
    design_sha256: str
    plan_sha256: str
    code_sha256: str
    target_snapshot_sha256: str
    closure_audit_sha256: str
    inventory_path: str
    inventory_sha256: str
    corpus_commitment: str
    baseline_upper_bound: str
    target_ids: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class DerivationRunContract:
    baseline_contract_sha256: str
    identity_registry_sha256: str
    classifier_schema_sha256: str
    classifier_evaluation_sha256: str
    model_attempt_profile_sha256: str
    media_input_manifest_sha256: str
    instrument_registry_sha256: str
    adapter_policy_sha256: str
    market_manifest_sha256: str
    h2_delta_sha256: str | None
    h2_union_sha256: str | None
    family_census_sha256: str | None
    source_revalidation_sha256: str | None

def load_baseline_run_contract(...) -> BaselineRunContract: ...
def finalize_derivation_contract(...) -> DerivationRunContract: ...
def deterministic_entity_id(kind: str, *canonical_parts: object) -> str: ...

class BloggerLedger:
    def begin_attempt(
        ..., task_id: str, claimed_by: str, lease_epoch: int
    ) -> str: ...
    def commit_message_revision(
        ..., expected_revision: str | None,
        task_id: str, claimed_by: str, lease_epoch: int
    ) -> str: ...
    def commit_event_revision(
        ..., expected_revision: str | None,
        task_id: str, claimed_by: str, lease_epoch: int
    ) -> str: ...
    def replace_lifecycle_revision(
        ..., expected_revision: str | None,
        task_id: str, claimed_by: str, lease_epoch: int
    ) -> str: ...
    def current_rows(entity_kind: str) -> Iterator[dict[str, object]]: ...
```

- [ ] Write failing tests for inventory/corpus/path SHA drift, exact target-set
  mismatch, deterministic IDs, duplicate commit no-op, current-revision UNIQUE,
  predecessor CAS, stale lease rejection and crash replay.
- [ ] Implement strict canonical JSON/hash validation and create the domain
  tables inside the same private SQLite database used by `TaskQueue`; do not
  split queue lease state and domain revisions across database files. Keep WAL,
  `synchronous=FULL`, foreign keys, single current revision per entity and
  append-only attempts.
- [ ] Reuse `Task.fencing_suffix()` and require
  `(task_id, claimed_by, lease_epoch)` on each worker commit. The ledger verifies
  the authoritative queue holder and epoch immediately inside the commit
  transaction. Use the same connection with `BEGIN IMMEDIATE` to lock the queue
  row, verify `claimed_by/lease_epoch/status/lease_deadline`, append the revision
  and CAS the current row before commit. An expired/replaced lease raises
  `LeaseLost`.
- [ ] Propagate `Task.trace_id`, idempotency key, task ID, worker ID and lease
  epoch into a reserved worker execution context; user payload must not be able
  to forge these values.
- [ ] Make idempotent enqueue fail closed when an existing key has different
  canonical packet bytes instead of silently returning the unrelated task.
- [ ] Add a private durable OperationRunner receipt store keyed by operation
  name, idempotency key and canonical spec hash. Same key/same spec replays the
  committed result without calling the handler; same key/different spec fails
  closed. `EXTERNAL_SEND` records send-attempt and committed result separately
  so retry cannot silently issue a duplicate send.
- [ ] Add a two-connection race test in which worker A pauses after reading its
  lease, worker B reclaims it, and A then attempts the domain commit. The single
  database transaction must reject A and permit exactly one B revision.
- [ ] Verify focused tests:

```bash
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src \
  /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v \
  tests.test_discord_blogger_contract tests.test_discord_blogger_ledger \
  tests.test_operation_receipts tests.test_runner tests.test_workers \
  tests.test_queue
```

---

### Task 2: Build the versioned target/author identity projection

**Files:**

- Create: `src/omni_hub/discord_blogger_identity.py`
- Create: `src/omni_hub/discord_blogger_evidence.py`
- Create: `src/omni_hub/discord_blogger_identity_review.py`
- Create: `tests/test_discord_blogger_identity.py`
- Create: `tests/test_discord_blogger_evidence.py`
- Create: `tests/test_discord_blogger_identity_review.py`
- Modify: `src/omni_hub/discord_blogger_inventory.py`
- Modify: `src/omni_hub/discord_blogger_corpus.py`
- Modify: `src/omni_hub/builtins.py`
- Modify: `src/omni_hub/cli/discord.py`
- Modify: `tests/test_discord_cli.py`

**Interfaces:**

```python
def build_target_identity_registry(
    *, messages: Iterable[BloggerMessage],
    inventory: Mapping[str, object],
    reviewed_overrides: Sequence[Mapping[str, object]],
) -> dict[str, object]: ...

def resolve_message_owner(
    *, message: BloggerMessage, registry: Mapping[str, object]
) -> IdentityResolution: ...

def build_identity_review_pack(...) -> Mapping[str, object]: ...
def freeze_identity_review_pack(
    *, candidate_pack: Path, reviewed_labels: Path,
    output_path: Path,
) -> Mapping[str, object]: ...

def iter_verified_blogger_evidence(
    *, export_root: Path, closure_audit: Path,
    target_ids: Sequence[str],
) -> Iterator[VerifiedMessageEnvelope]: ...
```

- [ ] Write failing tests for all six target types, author validity windows,
  verified owner/team/proxy/community/unknown/conflict, exact child-thread
  ownership, parent rollup and one `performance_owner_id` maximum.
- [ ] Generate conservative evidence-derived defaults: single author may only
  become verified when a reviewed ID binding exists; otherwise analyst/team/
  signal targets remain `unknown_author` and stay author-eligible.
- [ ] Build a private, non-git author census/review pack from exact author,
  webhook/application ID, target, validity windows and delivery evidence.
  Perform the review locally and freeze every accepted, rejected or conflicting
  row with reviewer/time/evidence commitments. Bind the reviewed pack SHA into
  the registry; no target may rely on a missing review row.
- [ ] Register `discord_blogger_identity_review_freeze` as a `LOCAL_WRITE`
  OperationRunner action and expose `discord-blogger-identity-review-freeze`.
  Test audit/idempotency, `0600` no-clobber output and hash drift.
- [ ] Stream rich snapshots through `extract_message_evidence()` so root and
  nested delivery attribution, message type, embed/components/attachments and
  closure media are retained. Closure current snapshots supersede baseline,
  while both snapshot provenances remain append-only.
- [ ] Do not materialize the full corpus twice in memory; batch into the ledger
  and use SQLite uniqueness for global conservation.
- [ ] Ensure the four formerly zero explicit threads keep their exact counts
  while parent Forum family counts remain projections.
- [ ] Persist no message body, URL or display-name-only verification.
- [ ] Run identity/inventory/corpus focused tests warning-strict.

---

### Task 3: Create one processing row and one current decision per message

**Files:**

- Create: `src/omni_hub/discord_blogger_classifier.py`
- Create: `src/omni_hub/discord_blogger_classifier_eval.py`
- Create: `src/omni_hub/discord_blogger_text_jobs.py`
- Create: `tests/test_discord_blogger_classifier.py`
- Create: `tests/test_discord_blogger_classifier_eval.py`
- Create: `tests/test_discord_blogger_text_jobs.py`
- Modify: `src/omni_hub/discord_trade_events.py`
- Modify: `src/omni_hub/builtins.py`
- Modify: `src/omni_hub/cli/discord.py`
- Modify: `tests/test_discord_cli.py`

**Interfaces:**

```python
def deterministic_evidence_card(
    message: VerifiedMessageEnvelope, identity: IdentityResolution
) -> EvidenceCard: ...

def classify_evidence_card(
    card: EvidenceCard, *, schema_version: str
) -> DecisionAttempt: ...

def reduce_current_decisions(
    attempts: Iterable[DecisionAttempt],
) -> tuple[MessageDecisionV2, ...]: ...

def build_classifier_evaluation(
    *, predictions: Iterable[MessageDecisionV2],
    gold: Iterable[GoldLabel],
) -> dict[str, object]: ...

def build_private_stratified_label_pack(...) -> Mapping[str, object]: ...
def freeze_private_gold_holdout(
    *, sample_pack: Path, reviewed_labels: Path,
    output_dir: Path,
) -> Mapping[str, object]: ...

def iter_text_semantic_jobs(
    cards: Iterable[EvidenceCard], *,
    processor_profile_sha256: str,
) -> Iterator[Mapping[str, object]]: ...
```

- [ ] Write failing tests for all processing states and primary decisions,
  append-only supersession, one-current-decision invariant, 0..N events,
  author-eligible media forcing `media_dependent`, hard negatives and exact
  duplicate occurrence-level decisions.
- [ ] Add an end-to-end rich-envelope test proving nested referenced-message
  delivery identity, embed fields, components, attachments and closure-current
  snapshot fields survive into the evidence card, text/media jobs and emitted
  event field provenance.
- [ ] Implement a high-recall deterministic first pass that never makes missing
  keywords a terminal `non_signal`; uncertain content becomes a queued semantic
  attempt or an explicit blocker.
- [ ] Add owner-only gold/holdout loaders that store only controlled evidence
  references and commitments. Enforce macro candidate recall >=95%,
  OPEN/AMEND/CLOSE recall >=97%, precision >=90%, and material-stratum recall
  >=90%; otherwise disable ranking.
- [ ] Deterministically stratify a private sample across all 132 targets,
  target types, languages, early/middle/late periods, text/media, event types
  and hard negatives. Label it locally, freeze disjoint gold/holdout
  commitments with minimum-positive counts, and rotate the holdout whenever a
  revealed pack is used for tuning. Private content/labels stay outside git.
- [ ] Register `discord_blogger_classifier_pack_freeze` as a `LOCAL_WRITE`
  OperationRunner action and expose `discord-blogger-classifier-pack-freeze`.
  The freeze is atomic/no-clobber `0600`, audited, idempotent and rejects
  overlapping gold/holdout IDs or an already revealed holdout version.
- [ ] Enqueue every author-eligible ordinary-text evidence card that the
  deterministic pass cannot commit. Job packets contain only package
  references and hashes; result ingestion commits a schema-bound decision with
  holder+epoch fencing. Text and media jobs use the same isolation contract but
  distinct schemas and attempt-profile hashes.
- [ ] Retain old four-profile parser only as `pilot_invalid_baseline`; no v2
  caller may use `PROFILE_CHANNELS` or fixed profile counts.
- [ ] Run classifier, trade-event regression and corpus tests warning-strict.

---

### Task 4: Build the all-author-eligible media occurrence manifest

**Files:**

- Create: `src/omni_hub/discord_blogger_media.py`
- Create: `tests/test_discord_blogger_media.py`
- Modify: `src/omni_hub/discord_candidate_media.py`

**Interfaces:**

```python
def build_author_eligible_media_manifest(
    *, messages: Iterable[BloggerMessage],
    identity_registry: Mapping[str, object],
    baseline_asset_indexes: Sequence[Path],
    recovery_audits: Sequence[Path],
    closure_occurrences: Path,
    h2_occurrences: Path | None,
) -> dict[str, object]: ...

def iter_media_semantic_jobs(
    manifest: Mapping[str, object],
) -> Iterator[dict[str, object]]: ...
```

- [ ] Write failing tests for the 146,017 baseline-record binding, 1,849 closure
  pending binding, missing/extra/duplicate/tampered sources, nested-node author
  attribution, snapshot-unattributed context, same blob across multiple
  occurrences and content-cache reuse without event deduplication.
- [ ] Generalize the existing safe asset readers. Preserve SSRF, MIME, size,
  YouTube, 400/404/415 and `reference_only != binary` semantics.
- [ ] Require every author-eligible occurrence to have one acquisition and one
  semantic disposition; terminal/reference-only media keeps the message
  blocked unless a reviewed `non_material_to_event` record exists.
- [ ] Prohibit raw URLs, message bodies and logical keys from the manifest.
- [ ] Run candidate-media, media-audit, media-recovery, sharding and new media
  tests warning-strict.

---

### Task 5: Add isolated local OCR/video semantics and queued result ingestion

**Files:**

- Create: `agent-harness/integrations/discord/media_semantics.py`
- Create: `agent-harness/integrations/discord/test_media_semantics.py`
- Create: `agent-harness/integrations/discord/text_semantics.py`
- Create: `agent-harness/integrations/discord/test_text_semantics.py`
- Create: `src/omni_hub/discord_blogger_semantic_jobs.py`
- Create: `tests/test_discord_blogger_semantic_jobs.py`
- Modify: `src/omni_hub/builtins.py`

**Interfaces:**

```python
def build_attempt_spec(job: Mapping[str, object]) -> Mapping[str, object]: ...
def classify_text_evidence(package: Mapping[str, object]) -> Mapping[str, object]: ...
def extract_image_semantics(path: Path, *, mime: str) -> Mapping[str, object]: ...
def extract_video_semantics(path: Path, *, mime: str) -> Mapping[str, object]: ...
def ingest_semantic_result(
    ..., task_id: str, claimed_by: str, lease_epoch: int
) -> str: ...
```

- [ ] Write failing tests for tool-free/no-URL evidence packages, minimal
  environment, attempt-spec hash idempotency, schema rejection, timeout,
  deterministic video frame timecodes, OCR bounding boxes and stale lease
  commit rejection.
- [ ] Use local macOS Vision/Tesseract/ffmpeg subprocess seams when installed.
  Missing local capability must produce `processor_unavailable`, never guessed
  text.
- [ ] Image results include OCR regions/coordinates plus candidate `symbol`,
  `direction`, `entry`, `stop`, `targets`, `timeframe` and per-field
  uncertainty/evidence regions. Video results include deterministic frame
  timecodes and time-located audio-transcription spans; OCR-only output is
  insufficient to mark chart/video semantics complete.
- [ ] The text worker returns the approved decision/event-hint schema for every
  queued evidence card, declares `tools_used=[]`, rejects URLs/tool calls and
  retains only evidence references and field-level provenance.
- [ ] If an external model path is configured, register it as `EXTERNAL_SEND`
  through `OperationRunner`, require approved provider/retention/redaction/
  request hash and expose no tools or filesystem path.
- [ ] Enqueue one idempotent TaskQueue job per content processor key; commit
  occurrence conclusions separately after the ledger transaction and only then
  acknowledge the task.
- [ ] Run integration and queue/runner tests warning-strict.

---

### Task 6: Normalize instruments without a BTC/ETH-only gate

**Files:**

- Create: `src/omni_hub/discord_instruments.py`
- Create: `tests/test_discord_instruments.py`
- Create: `agent-harness/quant/quant/instrument_registry.py`
- Create: `agent-harness/quant/tests/test_instrument_registry.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class InstrumentIdentity: ...

def resolve_instrument(
    *, evidence: Mapping[str, object],
    target_profile: Mapping[str, object],
    registry: Mapping[str, object],
) -> InstrumentIdentity: ...
```

- [ ] Write failing tests for crypto spot/perpetual/futures, XAU/gold, FX,
  equities, index/commodity futures, venue/session/expiry and ambiguous symbols
  such as `ES`, `GOLD` and short equity tickers.
- [ ] Build a versioned registry from explicit aliases and target context.
  Multi-match remains `ambiguous_instrument`; no implicit BTCUSDT fallback.
- [ ] Keep instrument resolution independent of outcome and future replies.
- [ ] Run main and quant registry tests warning-strict.

---

### Task 7: Extract the complete v2 event schema with field-level known time

**Files:**

- Create: `src/omni_hub/discord_trade_events_v2.py`
- Create: `tests/test_discord_trade_events_v2.py`

**Interfaces:**

```python
def extract_trade_events_v2(
    *, decision: MessageDecisionV2,
    evidence: EvidenceCard,
    media_semantics: Sequence[Mapping[str, object]],
    instrument_registry: Mapping[str, object],
) -> tuple[TradeEventV2, ...]: ...
```

- [ ] Write failing tests for every event type in the approved design,
  field-level `known_at`, `effective_at=max(known_at)`, edits, corrections,
  retracts, deleted source, reply time cutoff, author-reported versus
  market-simulated layers and 0..N events per message.
- [ ] Parse text, embeds, structured components and media semantics together.
  Do not let later replies fill earlier OPEN parameters.
- [ ] Preserve uncertain fields and confidence; schema/timestamp/evidence
  mismatch yields an invalid attempt rather than a fabricated event.
- [ ] Prove there is no fixed profile list, fixed sample count or supported
  two-symbol set in the v2 module.
- [ ] Run v2 tests plus old parser regression tests warning-strict.

---

### Task 8: Reconcile concurrent trade lifecycles and derive curation

**Files:**

- Create: `src/omni_hub/discord_trade_lifecycle.py`
- Create: `tests/test_discord_trade_lifecycle.py`

**Interfaces:**

```python
def reconcile_lifecycles(
    events: Iterable[TradeEventV2], *, linking_policy: Mapping[str, object]
) -> LifecycleReconciliation: ...

def derive_curation(
    lifecycles: Iterable[TradeLifecycleV2], *,
    eligibility_policy: Mapping[str, object],
) -> tuple[CurationRow, ...]: ...
```

- [ ] Write failing tests for order-ID, compatible reply, unique active
  author/instrument/direction/fingerprint, unique active compatible fallback,
  parallel orders, link conflicts, partial fills/closes, cancel/expire,
  correction/retract/edit/delete replay and unresolved close isolation.
- [ ] Give each resolved event at most one lifecycle. Keep forwards/reposts as
  separate occurrences unless order/reference/delivery evidence proves one
  intent.
- [ ] Give every OPEN exactly one lifecycle, eligibility status and execution
  disposition. Eligibility must not inspect later outcome.
- [ ] Missing SL/size or partial allocation produces N/A/range scenarios, not
  silent exclusion or fake equal allocation.
- [ ] Run lifecycle and ledger crash/replay tests warning-strict.

---

### Task 9: Build point-in-time market manifests and extend simulation

**Files:**

- Create: `src/omni_hub/discord_market_coverage.py`
- Create: `tests/test_discord_market_coverage.py`
- Create: `agent-harness/quant/quant/blogger_market_adapters.py`
- Create: `agent-harness/quant/tests/test_blogger_market_adapters.py`
- Create: `agent-harness/integrations/finance/blogger_market_data.py`
- Create: `agent-harness/integrations/finance/test_blogger_market_data.py`
- Modify: `agent-harness/quant/quant/discord_backtest.py`
- Modify: `agent-harness/quant/tests/test_discord_backtest.py`
- Modify: `src/omni_hub/builtins.py`
- Modify: `src/omni_hub/cli/discord.py`
- Modify: `tests/test_discord_cli.py`

**Interfaces:**

```python
def build_market_coverage(
    *, lifecycles: Iterable[TradeLifecycleV2],
    market_root: Path,
    adapter_registry: Mapping[str, object],
) -> dict[str, object]: ...
```

- [ ] Write failing tests for per-instrument start/end/horizon, file hashes,
  gaps, sessions, funding/fees/slippage/multipliers, market-data blockers and
  partial-allocation range output.
- [ ] Retain existing next-full-bar, no forced-market fallback, stop-first and
  adverse same-bar behavior. Apply every OPEN/AMEND/CANCEL/CLOSE only from the
  next full bar after its own `effective_at`.
- [ ] Add adapter dispatch for available crypto/gold/FX/equity/futures data.
  Missing material cost/session/corporate-action data blocks comparable net
  metrics instead of inventing defaults.
- [ ] Implement explicit `Crypto24x7Adapter`, `PerpetualAdapter`,
  `FxMetalsAdapter`, `EquitySessionAdapter` and `FuturesSessionAdapter`. Each
  freezes calendar/session, expiry/roll, multiplier, fee/slippage, funding and
  corporate-action requirements before outcomes are simulated.
- [ ] After the lifecycle instrument census, run a real-data inventory against
  `/Users/hzh/quant/market`. Fetch only missing authorized public market data
  through the finance integration, publish source/download/range/hash/gap
  manifests, and run one real-data smoke lifecycle for every adapter with
  sufficient data. An adapter without auditable data remains explicitly
  blocked.
- [ ] Register `discord_blogger_market_data_acquire` as an `EXTERNAL_SEND`
  OperationRunner action because it performs outbound fetches. The CLI enqueues
  one idempotent TaskQueue job per source/instrument/window; workers publish
  only hash-bound private market manifests before fenced acknowledgement.
  Tests cover approval, audit, retry receipts, packet collision and `0600`.
- [ ] Produce exactly one simulation disposition for every evaluable lifecycle.
- [ ] Run all quant Discord tests warning-strict.

---

### Task 10: Perform H2 catch-up and open-source revalidation

**Files:**

- Create: `src/omni_hub/discord_blogger_head_catchup.py`
- Create: `tests/test_discord_blogger_head_catchup.py`
- Modify: `src/omni_hub/discord_blogger_corpus.py`
- Modify: `src/omni_hub/builtins.py`

**Interfaces:**

```python
def build_head_catchup_request(...) -> Mapping[str, object]: ...
def validate_head_catchup_union(
    *, baseline: Mapping[str, object],
    delta: Mapping[str, object],
    family_census: Mapping[str, object],
    source_revalidation: Mapping[str, object],
) -> Mapping[str, object]: ...
```

- [ ] Write failing tests for a common millisecond `H2`, `(H,H2]` exclusivity,
  exact delta/union commitments, new public/joined child threads, private 403
  preservation, edits/deletes and partial-current labeling.
- [ ] Reuse the audited collector request semantics and token file without
  exposing it. This is a read-only Discord operation; never send a message or
  change permissions.
- [ ] Persist delta separately and merge only explicit validated delta message
  IDs. Re-run decisions/events/lifecycles for delta and affected open sources.
- [ ] Publish only catch-up facts: common H2, family census, explicit delta,
  union and edit/delete revalidation commitments. This module cannot declare
  `as_of=H2`; the final report gate must also verify all downstream delta
  derivations.
- [ ] Run catch-up, corpus, API and collector tests warning-strict.

---

### Task 11: Publish complete 132-target reports through OperationRunner

**Files:**

- Create: `src/omni_hub/discord_blogger_full_results.py`
- Create: `tests/test_discord_blogger_full_results.py`
- Modify: `src/omni_hub/cli/discord.py`
- Modify: `src/omni_hub/builtins.py`
- Modify: `tests/test_discord_cli.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ArtifactBinding:
    artifact_kind: str
    path: str
    expected_sha256: str
    source_message_ids_sha256: str
    entity_ids_sha256: str
    row_count: int

def build_full_blogger_results(...) -> Mapping[str, bytes]: ...
def publish_full_blogger_results(
    *, workspace: Path, output_dir: Path,
    artifacts: Mapping[str, bytes],
) -> Mapping[str, object]: ...

def validate_current_state_closure(
    *, catchup_manifest: Mapping[str, object],
    delta_identity: ArtifactBinding,
    delta_decisions: ArtifactBinding,
    delta_media: ArtifactBinding,
    delta_events: ArtifactBinding,
    delta_lifecycles: ArtifactBinding,
    delta_curation: ArtifactBinding,
    delta_backtest: ArtifactBinding,
) -> Mapping[str, object]: ...
```

- [ ] Write failing tests for all required output files, exact 132-ID equality,
  funnel conservation, separate coverage dimensions, current-call `as_of`,
  every OPEN/accounting disposition, one result/evaluable lifecycle,
  `small_sample_not_ranked`, no ranking under failed classifier gates and
  blockers for all unresolved paths.
- [ ] Register `discord_blogger_full_run` as a `LOCAL_WRITE` operation and add
  `discord-blogger-full-run` to `src/omni_hub/cli/discord.py`.
- [ ] Render per-target current calls, lifecycle counts, closed/unfilled/
  cancelled/expired/censored/data-blocked counts, win rate and intervals,
  average/cumulative R only when defined, drawdown, holding time and coverage.
- [ ] Set `as_of=H2` only when `validate_current_state_closure()` proves the
  delta/union, family census, source revalidation and all delta identity/media/
  decision/event/lifecycle/curation/backtest commitments conserve. Otherwise
  retain `as_of=H` and disclose `new_message_catchup_as_of=H2`.
- [ ] The closure gate must open and hash every typed artifact, verify its
  declared kind, recompute exact delta message/entity ID sets, reject
  missing/extra/duplicate rows, and verify cross-layer message→decision→event→
  lifecycle→eligibility/disposition/result relationships. Tests tamper bytes,
  swap kind labels, reuse a valid hash for the wrong artifact and alter one
  exact ID; all must fail closed.
- [ ] Publish the required JSON/JSONL/Markdown files with canonical hashes,
  `supersedes_for_interpretation`, source/code/schema/model/market commitments,
  atomic no-clobber and private modes.
- [ ] Scan staged artifacts for token shapes, signed-query parameters, raw
  message bodies, filesystem secret paths and logical keys before rename.
- [ ] Run result, CLI, builtin, policy and publication tests warning-strict.

---

### Task 12: Execute the formal run, audit, review and PR update

**Files:**

- Modify only if a test/review demonstrates a defect in Tasks 1–11.
- Create outside git:
  `discord-exports/v2/derivatives/blogger-results/full-v2-<run-id>/`.

- [ ] Revalidate the frozen inventory/corpus/plan/closure hashes, 132 exact
  target IDs, current disk >=50 GiB, SQLite `quick_check`, no live duplicate
  full-run worker and token-leak scan zero.
- [ ] Run H2 catch-up. If Discord/network is retryable, resume the identical
  request; do not restart historical collectors or discard prior evidence.
- [ ] Enqueue/execute all text-decision and media semantic jobs. Re-run
  retryable jobs idempotently; retain terminal text/media blockers.
- [ ] Materialize events, lifecycles, curation and instrument census. Acquire or
  validate market data only for resolved instruments, then simulate all
  evaluable lifecycles.
- [ ] Verify all conservation equations, classifier gates, target exact set,
  media occurrence set, H2 downstream derivation commitments, current-call
  closure and report commitments.
- [ ] Run focused suites, quant suites and:

```bash
PYTHONWARNINGS=error::ResourceWarning make test
```

- [ ] Request independent specification and code reviews. Fix every
  Critical/Important finding with a reproducing test; rerun full verification.
- [ ] Commit, push `codex/discord-collector-v2-final`, update PR #1, and merge
  only after the branch is clean and all checks pass.
- [ ] Deliver actual per-target/result counts and artifact links. Explicitly
  distinguish complete results, small samples and blockers; never claim the
  private 403 scope or failed media is complete.

## Plan Acceptance Checklist

- [ ] No fixed four-profile, 324/216 or BTC/ETH-only gate exists in v2.
- [ ] All 842,425 historical messages plus validated H2 delta are accounted.
- [ ] Every author-eligible rich-media occurrence is processed or blocked.
- [ ] Every OPEN, event, lifecycle, curation row and evaluable result conserves.
- [ ] All 132 targets are present even when performance is N/A.
- [ ] No future information, same-message-bar history, forced fill or
  outcome-conditioned selection is used.
- [ ] Identity, media, lifecycle, market and private-scope coverage remain
  independent.
- [ ] Tests/reviews/security scans pass before any completion claim.
