# Discord Media Resolution Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correctly distinguish DNS resolution failures from media-security refusals, recover only strictly eligible legacy Discord CDN/proxy failures with a durable three-sequence budget, and publish independently verifiable recovery evidence that blocks false closure.

**Architecture:** Keep connection-policy classification in the stdlib Discord transport. Put request-bound recovery state validation and eligibility in a new pure `discord_media_recovery` module, and deterministic row/count construction in a separate pure `discord_media_audit` module. The collector remains the only writer; sharding independently rebuilds the audit from the bound request, SQLite ledger, asset records, and asset index before closure can trust it.

**Tech Stack:** Python 3.12 stdlib (`enum`, `errno`, `socket`, `ipaddress`, `urllib.parse`, `hashlib`, `json`, `dataclasses`), existing SQLite asset ledger and atomic writers, `unittest`, existing injected JSON/byte transports.

## Global Constraints

- The binding design is `docs/superpowers/specs/2026-07-20-discord-media-resolution-recovery-design.md`; implement every invariant in sections 4–9, including typed `in_progress` with null reason/detail and typed `interrupted` with reason `interrupted`, null detail, and audit disposition `resolution_retry_pending`.
- Do not hot-update, signal, restart, or otherwise disturb the four currently running collector processes. New code is used only after all four reach a terminal state and request/plan/policy integrity is reverified.
- Never print or persist the Discord bot token, raw exception text, Authorization, or a raw/signed media URL in the recovery audit. `candidate_url_sha256` hashes the exact URL bytes, including query, but the URL itself is absent.
- Do not widen the SSRF boundary: credential-free HTTPS, effective port 443, exact existing host allowlist, all-public DNS answers, connect-time revalidation, DNS pinning, TLS/SNI, redirect revalidation, and the exact RFC 2544 policy remain mandatory.
- Transient resolution is only `EAI_AGAIN`, `TimeoutError`/`socket.timeout`, and resolver `OSError.errno == ETIMEDOUT`. Name-not-found, no-data, empty answer, unclassified `OSError`, malformed answer, private answer, and mixed answer are not transient.
- Legacy recovery requires current record **and** exact candidate's latest attempt to be `failed + unsafe_media_url`, zero bytes/no HTTP metadata/no SHA/no blob, no covered binary history, exact official HTTPS:443 host, exact request-bound policy, one marker, and proxy-only handling for external direct/proxy observations.
- The durable budget key is `(logical_key, exact candidate_url, request_sha256)`. At most three committed typed logical sequences exist; an already committed `in_progress`/`interrupted` sequence is reused after a crash and may repeat physical I/O without allocating a fourth sequence.
- Preserve every historical attempt byte-for-byte. Recovery is append-only except that an already committed in-progress typed attempt may be finalized in place.
- Keep asset-record schema version 3, SQLite asset-ledger version 1, request version 2, and manifest version 1. Existing schema-v3 records with none of the new optional attempt fields remain valid; do not bulk-migrate them.
- Preserve existing HTTP, MIME, declared-size, content-length, candidate fallback, YouTube reference-only, proxy-binary stale-reference cleanup, attachment, thumbnail, and 400/404/415 semantics.
- `reference_only` is never binary. Current failed records and verified audit blockers prevent completion; a covered failed candidate remains an attempt-level compensation row without making the final record failed.
- New modules require focused tests. Each task follows RED → GREEN, produces pristine output, commits only its scoped files, and receives a task-scoped review before the next task.
- Preserve unrelated user changes in `prompts/engineering/v1/*`, `prompts/research/v1/*`, and unrelated `.superpowers/` files. Do not stage them.
- Final verification is `PYTHONWARNINGS=error::ResourceWarning make test` with zero failures and zero `ResourceWarning`.

## File Structure

- `src/omni_hub/connectors/discord.py`: stable resolver exception taxonomy while retaining the network-security gate.
- `src/omni_hub/discord_media_recovery.py`: immutable request context, exact legacy eligibility, retry-sequence selection, and load-time attempt-history validation.
- `src/omni_hub/discord_media_audit.py`: canonical audit bytes, deterministic rows, fixed counts, dispositions, and top-level artifact construction.
- `src/omni_hub/discord_collector.py`: durable marker allocation/reuse, typed outcome mapping, atomic audit publication, and manifest binding.
- `src/omni_hub/discord_sharding.py`: independent artifact reconstruction, hash/path/count validation, media blocker propagation, and schema-v2/v3 compatibility checks.
- `tests/test_discord_api.py`: transport taxonomy and SSRF boundary.
- `tests/test_discord_media_recovery.py`: pure request/eligibility/sequence/history invariants.
- `tests/test_discord_media_audit.py`: pure row/count/canonicalization contract.
- `tests/test_discord_collector.py`: state-machine, crash-replay, artifact publication, and media-semantics integration.
- `tests/test_discord_sharding.py`: hostile artifact tampering, independent reconstruction, and closure gates.

---

### Task 1: Add Stable Resolver Failure Taxonomy Without Widening SSRF

**Files:**
- Modify: `src/omni_hub/connectors/discord.py:115-289`
- Modify: `tests/test_discord_api.py:123-573`

**Interfaces:**
- Consumes: existing `_resolved_public_addresses(host, port, resolver, path, allow_rfc2544_fake_ip)` and `DiscordAPIError`.
- Produces: `DiscordMediaResolutionReason`, `DiscordMediaResolutionError.reason_code`, and `DiscordMediaResolutionInvalidAnswer.reason_code` for Task 4. Existing callers and function signatures remain compatible.

- [ ] **Step 1: Write the failing taxonomy tests**

Import the three new public types and add five tests to `DiscordHTTPTransportTests`:

```python
def test_media_resolution_transient_errors_are_typed_without_opening(self) -> None:
    cases = (
        (socket.gaierror(socket.EAI_AGAIN, "again"), "resolver_eai_again"),
        (TimeoutError(), "resolver_timeout"),
        (OSError(errno.ETIMEDOUT, "timeout"), "resolver_timeout"),
    )
    for resolver_error, expected in cases:
        opener = Mock()
        transport = DiscordHTTPTransport(
            self.token,
            opener=opener,
            resolver=Mock(side_effect=resolver_error),
        )
        with self.subTest(expected=expected):
            with self.assertRaises(DiscordMediaResolutionError) as caught:
                transport.open_byte_stream("https://cdn.example/file")
            self.assertEqual(caught.exception.reason_code, expected)
            opener.assert_not_called()

def test_media_resolution_invalid_answer_prevents_socket_creation(self) -> None:
    socket_factory = Mock()
    with self.assertRaises(DiscordMediaResolutionInvalidAnswer) as caught:
        discord_api_module._create_public_connection(
            ("cdn.example", 443),
            resolver=Mock(return_value=[("bad",)]),
            socket_factory=socket_factory,
        )
    self.assertEqual(caught.exception.reason_code, "resolver_invalid_answer")
    socket_factory.assert_not_called()
```

The other three tests must cover `EAI_NONAME`, platform-distinct `EAI_NODATA`, empty list/tuple, unclassified `OSError`, non-list output, malformed five-tuples/addresses, and connect-time `EAI_AGAIN`. Strengthen the existing private/mixed-answer assertions to require `DiscordMediaSecurityError` and zero opener/socket calls.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v tests.test_discord_api.DiscordHTTPTransportTests
```

Expected: import or assertion failures because the typed resolver classes and `reason_code` values do not exist; existing transport tests remain collected.

- [ ] **Step 3: Implement the typed exceptions and errno mapping**

Add `errno` and `enum` imports, then add this exact enum and credential-safe constructor shape:

```python
class DiscordMediaResolutionReason(enum.StrEnum):
    EAI_AGAIN = "resolver_eai_again"
    TIMEOUT = "resolver_timeout"
    NAME_NOT_FOUND = "resolver_name_not_found"
    NO_DATA = "resolver_no_data"
    EMPTY_ANSWER = "resolver_empty_answer"
    OS_ERROR_UNCLASSIFIED = "resolver_os_error_unclassified"
    INVALID_ANSWER = "resolver_invalid_answer"


class DiscordMediaResolutionError(DiscordAPIError):
    __slots__ = ("reason_code",)

    def __init__(
        self,
        reason_code: DiscordMediaResolutionReason,
        *,
        path: str | None = None,
    ) -> None:
        super().__init__("Discord media host resolution failed", path=path)
        self.reason_code = str(reason_code)


class DiscordMediaResolutionInvalidAnswer(DiscordAPIError):
    __slots__ = ("reason_code",)

    def __init__(self, *, path: str | None = None) -> None:
        super().__init__("Discord media resolver returned an invalid answer", path=path)
        self.reason_code = str(DiscordMediaResolutionReason.INVALID_ANSWER)
```

Refactor only `_resolved_public_addresses`: classify `socket.gaierror` by numeric errno; check distinct platform `EAI_NODATA` only when present; classify timeouts; map all other resolver `OSError` to `OS_ERROR_UNCLASSIFIED`; map empty list/tuple to `EMPTY_ANSWER`; map malformed containers/tuples/families/socket addresses/IP text to `DiscordMediaResolutionInvalidAnswer`. Leave literal/private/mixed/RFC2544 branches as `DiscordMediaSecurityError`.

- [ ] **Step 4: Run focused and file-level GREEN tests**

Run:

```bash
PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v tests.test_discord_api
```

Expected: all `tests.test_discord_api` tests pass, with the existing fake-IP, port, lookalike-host, redirect, and DNS-rebinding tests still green and no warnings.

- [ ] **Step 5: Commit the transport task**

```bash
git add src/omni_hub/connectors/discord.py tests/test_discord_api.py
git commit -m "fix(discord): classify media resolution failures"
```

### Task 2: Build Request-Bound Recovery Policy and Load-Time Validation

**Files:**
- Create: `src/omni_hub/discord_media_recovery.py`
- Create: `tests/test_discord_media_recovery.py`

**Interfaces:**
- Consumes: `RFC2544_FAKE_IP_MEDIA_HOSTS`, `RFC2544_FAKE_IP_MEDIA_PORT`, and `rfc2544_fake_ip_media_policy_descriptor()` from Task 1's transport module.
- Produces:

```python
LEGACY_RETRY_TRIGGER: str
RESOLUTION_RETRY_TRIGGER: str
MAX_RESOLUTION_RETRY_SEQUENCES: int

@dataclass(frozen=True, slots=True)
class MediaResolutionContext:
    request_sha256: str
    allow_rfc2544_fake_ip: bool
    policy_inputs_sha256: str | None
    policy_descriptor: Mapping[str, object] | None

def media_resolution_context(
    request_identity: Mapping[str, Any],
    request_sha256: str,
) -> MediaResolutionContext

def validate_resolution_attempt_history(
    record: Mapping[str, Any],
    *,
    context: MediaResolutionContext,
) -> None

def legacy_recovery_retry_of(
    record: Mapping[str, Any],
    candidate_url: str,
    *,
    context: MediaResolutionContext,
) -> int | None

def reusable_resolution_attempt_number(
    record: Mapping[str, Any],
    candidate_url: str,
) -> int | None

def next_resolution_retry_metadata(
    record: Mapping[str, Any],
    candidate_url: str,
    *,
    context: MediaResolutionContext,
) -> dict[str, object] | None

def is_discord_external_proxy_url(value: object) -> bool
```

- [ ] **Step 1: Write RED tests for request binding and exact eligibility**

Create `MediaResolutionContextTests` and `LegacyMediaRecoveryEligibilityTests`. Use synthetic URLs only. Assert the opt-in descriptor produces its existing `inputs_sha256`, non-opt-in produces `None`, and contradictory/mismatched policy fields raise `ValueError`. Parameterize every eligibility rejection: current record not unsafe, latest exact candidate not unsafe, bytes/metadata/blob present, covered binary history, credentials, non-HTTPS, non-443, lookalike/unlisted host, wrong request policy, duplicate marker, and direct candidate when an external proxy observation exists.

The positive fixture must return the exact 1-based unsafe attempt number and preserve the exact candidate URL including its query:

```python
retry_of = legacy_recovery_retry_of(
    record,
    "https://media.discordapp.net/external/item?sig=synthetic",
    context=context,
)
self.assertEqual(retry_of, 1)
```

- [ ] **Step 2: Write RED tests for history validation and retry selection**

Add `MediaResolutionHistoryValidationTests`. Cover valid legacy sequence 1; transient sequences 1→2→3; third-sequence exhaustion; reusable `in_progress` and `interrupted`; bool/zero/forward/cross-candidate `retry_of_attempt_number`; duplicate marker; duplicate/skipped/>3 sequence; opt-in hash mismatch; non-opt-in non-null hash; illegal `failure_detail`/terminal-reason pairs; terminal resolution outcome without sequence/detail; and a schema-v3 history with none of the new fields.

Assert `next_resolution_retry_metadata` returns these exact dictionaries:

```python
{
    "retry_trigger": "legacy_resolver_security_conflation_v1",
    "retry_of_attempt_number": 1,
    "policy_inputs_sha256": context.policy_inputs_sha256,
    "resolution_retry_sequence": 1,
}
```

and, after a sequence-1 transient:

```python
{
    "retry_trigger": "media_resolution_retry_v1",
    "retry_of_attempt_number": 2,
    "policy_inputs_sha256": context.policy_inputs_sha256,
    "resolution_retry_sequence": 2,
}
```

- [ ] **Step 3: Run the new suite and verify RED**

Run:

```bash
PYTHONPATH=src:tests /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v test_discord_media_recovery
```

Expected: import failure because `discord_media_recovery` does not exist.

- [ ] **Step 4: Implement the pure recovery contract**

Define exact constants:

```python
LEGACY_RETRY_TRIGGER = "legacy_resolver_security_conflation_v1"
RESOLUTION_RETRY_TRIGGER = "media_resolution_retry_v1"
MAX_RESOLUTION_RETRY_SEQUENCES = 3
TRANSIENT_RESOLUTION_DETAILS = frozenset(
    {"resolver_eai_again", "resolver_timeout"}
)
UNRESOLVED_RESOLUTION_DETAILS = frozenset(
    {
        "resolver_name_not_found",
        "resolver_no_data",
        "resolver_empty_answer",
        "resolver_os_error_unclassified",
    }
)
```

Implement context validation from the complete request `options`, exact URL parsing/host canonicalization, proxy-only eligibility, zero-byte/no-metadata checks, per-candidate typed sequence scans, and cross-attempt validation. Validation must first collect all facts, then reject corruption before returning; it must never perform network or filesystem I/O. Keep exact candidate URL strings as keys and use `request_sha256` only through the immutable context. Typed `in_progress` uses null terminal reason/detail; typed `interrupted` uses terminal reason `interrupted` and null detail.

- [ ] **Step 5: Run GREEN and commit**

Run:

```bash
PYTHONPATH=src:tests /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v test_discord_media_recovery
```

Expected: all recovery-policy tests pass with pristine output.

```bash
git add src/omni_hub/discord_media_recovery.py tests/test_discord_media_recovery.py
git commit -m "feat(discord): validate bounded media recovery"
```

### Task 3: Build the Deterministic Media Recovery Audit Contract

**Files:**
- Create: `src/omni_hub/discord_media_audit.py`
- Create: `tests/test_discord_media_audit.py`

**Interfaces:**
- Consumes: recovery triggers and terminal-reason sets from `discord_media_recovery`.
- Produces:

```python
MEDIA_RECOVERY_AUDIT_VERSION = 1
MEDIA_RECOVERY_AUDIT_KIND = "discord_media_resolution_recovery_audit"
MEDIA_RECOVERY_AUDIT_FILENAME = "media-recovery-audit.json"

def canonical_media_recovery_audit_bytes(
    value: object,
    *,
    newline: bool = True,
) -> bytes

def rebuild_media_recovery_rows(
    records: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]

def rebuild_media_recovery_counts(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, int]

def build_media_recovery_audit(
    *,
    run_id: str,
    request_sha256: str,
    policy_inputs_sha256: str | None,
    asset_index_sha256: str,
    records: Mapping[str, Mapping[str, object]],
) -> dict[str, object]
```

- [ ] **Step 1: Write the audit RED tests**

Create `MediaRecoveryAuditTests` covering the union-without-duplication attempt selection, record-row selection, 1-based attempt identity, UTF-8 byte sorting, no-newline row-id hashing, exact signed URL hashing, lowercase/single-trailing-dot host, malformed host `None`, fixed field set, all dispositions, and all 19 count keys/invariants.

Include these critical assertions:

```python
content = canonical_media_recovery_audit_bytes(audit)
self.assertNotIn(b"sig=synthetic", content)
self.assertEqual(
    attempt_row["candidate_url_sha256"],
    hashlib.sha256(exact_url.encode("utf-8")).hexdigest(),
)
self.assertFalse(reference_row["binary_captured"])
self.assertEqual(interrupted_typed_row["disposition"], "resolution_retry_pending")
self.assertEqual(audit["counts"]["unresolved_blockers"], 1)
```

Assert a failed 404 attempt followed by a valid final binary is `candidate_failed_record_covered`, while a current failed 404 record is `http_compensation_blocker` and contributes one unresolved blocker.

- [ ] **Step 2: Run the audit suite and verify RED**

Run:

```bash
PYTHONPATH=src:tests /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v test_discord_media_audit
```

Expected: import failure because `discord_media_audit` does not exist.

- [ ] **Step 3: Implement canonical rows, dispositions, and fixed counts**

Canonical bytes must be UTF-8 JSON with `ensure_ascii=False`, `sort_keys=True`, compact separators, and an optional single trailing newline. Row identity payloads are exactly:

```python
{"item_kind": "attempt", "logical_key": logical_key, "attempt_number": attempt_number}
{"item_kind": "record", "logical_key": logical_key}
```

Sort rows by:

```python
(
    logical_key.encode("utf-8"),
    0 if item_kind == "attempt" else 1,
    attempt_number or 0,
)
```

Initialize exactly the 19 design count keys at zero, derive them from rows only, enforce unique `row_id`, enforce the two row-count equalities and eight mutually exclusive failed-record buckets, and raise `ValueError` for malformed records/attempts instead of emitting ambiguous rows. Treat typed `in_progress`/`interrupted` attempts with no terminal reason as `resolution_retry_pending` but do not generate a record row for a current in-progress record.

- [ ] **Step 4: Run GREEN and commit**

Run:

```bash
PYTHONPATH=src:tests /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v test_discord_media_audit
```

Expected: all audit tests pass with stable repeated bytes.

```bash
git add src/omni_hub/discord_media_audit.py tests/test_discord_media_audit.py
git commit -m "feat(discord): build media recovery audit"
```

### Task 4: Integrate Atomic Recovery and Three-Sequence Replay Into the Collector

**Files:**
- Modify: `src/omni_hub/discord_collector.py:20-105,694-780,1210-1522,3178-3470,4668-4699`
- Modify: `tests/test_discord_collector.py`

**Interfaces:**
- Consumes: Task 1 exception types and Task 2 `MediaResolutionContext`, validator, eligibility, reuse, retry metadata, and proxy helper.
- Produces: request-bound `self._resolution_context`; validated append-only attempt fields; `_download_asset(record, resume_attempt_number=None, retry_metadata=None)`; exact typed terminal outcomes for Task 5's audit publisher.

- [ ] **Step 1: Write RED tests for typed outcomes and policy-null behavior**

Add `DiscordMediaResolutionRecoveryTests` using the existing fake collector transports and isolated run fixtures. For each stable `reason_code`, assert the exact record/attempt `terminal_reason` and `failure_detail`; assert the collector reads the property even when exception text is misleading. For a non-opt-in request, assert sequence 1 and `policy_inputs_sha256 is None`; for opt-in, assert the descriptor's fixed inputs SHA.

- [ ] **Step 2: Write RED tests for legacy AND eligibility and bounded resumes**

Seed schema-v3 asset records through the real asset ledger. Cover record/latest-attempt AND, official proxy-only recovery, old-attempt byte-for-byte preservation, sequence 1→2→3, third transient exhaustion, fourth resume zero byte-transport calls, unresolved/invalid/new-security terminal no-op, covered binary/current 404/nonofficial/wrong-policy/non-443/credential/bytes/blob rejection, and non-opt-in no legacy override.

- [ ] **Step 3: Write RED tests for corruption and crash replay**

Before every expected failure, attach a byte-transport mock and assert zero calls. Tamper `retry_of`, candidate URL, marker uniqueness, policy hash, sequence continuity/uniqueness/maximum, and detail/reason pairing. Inject failure at marker pre-commit, marker post-commit/pre-resolver, blob post-promote/pre-final-record, and terminal post-commit. Assert at most one legacy marker and at most three committed sequences; post-marker replay reuses the same attempt number.

- [ ] **Step 4: Run focused collector tests and verify RED**

Run:

```bash
PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v tests.test_discord_collector.DiscordMediaResolutionRecoveryTests
```

Expected: failures because context binding, marker fields, typed outcomes, and replay selection are absent.

- [ ] **Step 5: Bind context and validate histories before network I/O**

Immediately after `_bind_request_identity(request_identity)`, create:

```python
self._resolution_context = media_resolution_context(
    request_identity,
    self._checkpoint["request_sha256"],
)
```

At the end of `_validate_asset_record`, call `validate_resolution_attempt_history(record, context=self._resolution_context)`. Preserve schema version 3 and all existing blob/reference checks. Replace the collector's local external-proxy implementation with a compatibility wrapper that delegates to Task 2's pure helper so existing private call sites/tests retain their name.

- [ ] **Step 6: Implement marker allocation, attempt reuse, and exact exception mapping**

Change the signature to:

```python
def _download_asset(
    self,
    record: dict[str, Any],
    *,
    resume_attempt_number: int | None = None,
    retry_metadata: Mapping[str, object] | None = None,
) -> None:
```

When resuming, select the existing 1-based attempt and append nothing. When allocating, create the existing attempt payload plus `retry_metadata`. In both cases set record/attempt to `in_progress` and `_commit_asset_record(record)` before `tempfile.mkstemp`, resolver, socket, or byte-stream I/O. `_download_asset_candidates` must choose in this order: reusable typed in-progress/interrupted; typed transient sequence <3; strict legacy metadata; then existing complete/reference/fallback logic. `_attempted_asset_urls` still prevents more than one new sequence for an exact key in one invocation.

Catch in this exact order:

```python
except DiscordMediaResolutionInvalidAnswer as exc:
    record["status"] = "failed"
    record["terminal_reason"] = "media_resolution_invalid_answer"
    attempt["failure_detail"] = exc.reason_code
except DiscordMediaResolutionError as exc:
    record["status"] = "failed"
    attempt["failure_detail"] = exc.reason_code
    if exc.reason_code in TRANSIENT_RESOLUTION_DETAILS:
        record["terminal_reason"] = (
            "media_resolution_retry_exhausted"
            if attempt["resolution_retry_sequence"] == 3
            else "media_resolution_failed_transient"
        )
    else:
        record["terminal_reason"] = "media_resolution_unresolved"
except DiscordMediaSecurityError:
    record["status"] = "failed"
    record["terminal_reason"] = "unsafe_media_url"
```

If a generic attempt first encounters a resolution exception, assign its typed sequence 1 and request-appropriate policy hash before final commit; never parse `str(exc)`. `_finish_asset_attempt` must preserve retry fields and copy only outcome fields. A third transient is terminal; future resume must not call the resolver.

- [ ] **Step 7: Run collector GREEN and media-regression tests**

Run:

```bash
PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v tests.test_discord_collector.DiscordMediaResolutionRecoveryTests
PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest discover -s tests -p 'test_discord_collector.py' -v
```

Expected: recovery tests and all pre-existing HTTP/MIME/size/content-length/candidate/YouTube/blob/ledger tests pass with no warnings.

- [ ] **Step 8: Commit the collector recovery state machine**

```bash
git add src/omni_hub/discord_collector.py tests/test_discord_collector.py
git commit -m "feat(discord): recover eligible media resolution failures"
```

### Task 5: Publish the Hash-Bound Recovery Audit in Every Collector Finalization

**Files:**
- Modify: `src/omni_hub/discord_collector.py:3637-3930`
- Modify: `tests/test_discord_collector.py`

**Interfaces:**
- Consumes: Task 3 `build_media_recovery_audit`, canonical bytes, constants; Task 4 request context and records.
- Produces: `_write_asset_index() -> str`, `_write_media_recovery_audit(asset_index_sha256: str) -> dict[str, Any]`, `runs/<run-id>/media-recovery-audit.json`, and `manifest.media_recovery_audit`.

- [ ] **Step 1: Write RED artifact and manifest tests**

Add `DiscordMediaRecoveryAuditPublicationTests`. Assert opt-in and null top-level policy SHA, exact actual asset-index SHA, fixed path/version, final-file SHA, descriptor counts equality, raw/signed URL absence, deterministic unchanged resume bytes, legitimate hash update after recovery, and generation during `interrupted=True` finalization. Exercise both final failed HTTP 404 and a 404 candidate covered by later proxy binary.

- [ ] **Step 2: Run the publication class and verify RED**

Run:

```bash
PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v tests.test_discord_collector.DiscordMediaRecoveryAuditPublicationTests
```

Expected: failures because the artifact and manifest descriptor are absent.

- [ ] **Step 3: Return the committed asset-index SHA and atomically publish the audit**

Make `_write_asset_index` return the SHA accepted by `mark_index`; when no rewrite is needed, read the ledger's bound index SHA and verify it against the regular `asset-index.jsonl` file before returning it. Add:

```python
def _write_media_recovery_audit(
    self,
    *,
    asset_index_sha256: str,
) -> dict[str, Any]:
    audit = build_media_recovery_audit(
        run_id=self._checkpoint["run_id"],
        request_sha256=self._resolution_context.request_sha256,
        policy_inputs_sha256=self._resolution_context.policy_inputs_sha256,
        asset_index_sha256=asset_index_sha256,
        records=self._asset_records,
    )
    content = canonical_media_recovery_audit_bytes(audit)
    _atomic_write_bytes(self._run_root / MEDIA_RECOVERY_AUDIT_FILENAME, content)
    return {"audit": audit, "sha256": _sha256_bytes(content)}
```

The exact `_write_derived_outputs` order is: reconcile pending ledger; save checkpoint; write/index SHA; checkpoint SQLite/WAL; build and atomically write audit; write errors/stream summaries; write manifest. Add this descriptor without changing the existing `media` object:

```python
"media_recovery_audit": {
    "version": MEDIA_RECOVERY_AUDIT_VERSION,
    "path": MEDIA_RECOVERY_AUDIT_FILENAME,
    "sha256": audit_result["sha256"],
    "counts": deepcopy(audit_result["audit"]["counts"]),
},
```

- [ ] **Step 4: Run GREEN plus collector regressions**

Run:

```bash
PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v tests.test_discord_collector.DiscordMediaRecoveryAuditPublicationTests
PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest discover -s tests -p 'test_discord_collector.py' -v
```

Expected: artifact tests and all collector tests pass; YouTube no-binary reference and proxy stale-reference cleanup remain green.

- [ ] **Step 5: Commit publication**

```bash
git add src/omni_hub/discord_collector.py tests/test_discord_collector.py
git commit -m "feat(discord): publish media recovery evidence"
```

### Task 6: Independently Rebuild Recovery Evidence Before Merge and Closure

**Files:**
- Modify: `src/omni_hub/discord_sharding.py:851-970,3250-4350`
- Modify: `tests/test_discord_sharding.py:305-450,764-2728`

**Interfaces:**
- Consumes: Task 2 `media_resolution_context`; Task 3 audit builder/canonical bytes/constants; actual request bytes/SHA, SQLite committed rows, asset-record bytes, and asset-index bytes.
- Produces: transitive evidence field `media_recovery_audit = {verified, sha256, counts}` and a fail-closed media/closure verdict.

- [ ] **Step 1: Upgrade synthetic shard fixtures and write RED hostile-tamper tests**

Update `_write_asset_ledger_fixture` to write schema-v3 records with candidate URLs, return its committed records and actual asset-index SHA, build the genuine audit, and add the manifest descriptor. Keep an explicit schema-v2 fixture for compatibility. Correct fixture expectations so reference-only does not increment binary count and the narrow YouTube reference may omit a blob.

Add tests where an attacker changes each of: counts, one row, row order, row ID, URL hash, asset-index binding, descriptor path, descriptor counts, and artifact bytes; then also updates the artifact SHA, manifest SHA, and merge-request manifest SHA. Each must still fail because the validator independently rebuilds the expected bytes. Add symlink/non-regular artifact rejection.

- [ ] **Step 2: Write RED blocker and compatibility tests**

Assert verified `unresolved_blockers > 0` yields merge partial and closure incomplete; `candidate_failed_record_covered` does not create a current failed record; schema v3 is accepted; historical schema v2 remains accepted; reference-only is not binary; complete/captured warning blobs validate; and only the exact YouTube no-blob reference exception is accepted.

- [ ] **Step 3: Run focused sharding tests and verify RED**

Run:

```bash
PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v tests.test_discord_sharding.MergeAndClosureAuditTests
```

Expected: missing-descriptor/reconstruction failures and current schema/reference/binary expectation failures.

- [ ] **Step 4: Thread request evidence into the asset validator and reconstruct independently**

Extend `_audit_transitive_run_evidence` and `_audit_asset_evidence` to receive the already-read request object and actual request SHA. Retain a `logical_key -> committed record` mapping; calculate the actual asset-index SHA; read the fixed audit path as a non-symlink regular file; validate descriptor key set/version/path/SHA/counts; derive `MediaResolutionContext` from the actual bound request; call `build_media_recovery_audit`; compare canonical expected bytes to the exact artifact bytes; and return:

```python
"media_recovery_audit": {
    "verified": not recovery_validation_errors,
    "sha256": actual_audit_sha256,
    "counts": expected_audit["counts"],
}
```

Put every mismatch into the existing `validation_errors`; never trust artifact-reported rows or counts.

- [ ] **Step 5: Repair schema/reference/binary drift and enforce the closure gate**

Accept record schema versions 2 and 3, with v3 `candidate_urls` validation. Count binary only when status is `complete` or `captured_with_warning` with a valid positive-byte SHA/blob. Permit a no-blob `reference_only` record only for the existing exact YouTube player reference/provenance rule. `_validate_media_state` must require verified audit evidence and equality among `audit.unresolved_blockers`, `manifest.media.failed`, and asset failed status count. Nonzero blockers remain a consistent partial state but cannot enter `media_complete_shards` or authorized closure.

- [ ] **Step 6: Run GREEN, file regressions, and commit**

Run:

```bash
PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v tests.test_discord_sharding.MergeAndClosureAuditTests
PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest discover -s tests -p 'test_discord_sharding.py' -v
```

Expected: all hostile tampering is rejected, verified blocker-free fixtures can close, blocker fixtures cannot, and every sharding regression passes without warnings.

```bash
git add src/omni_hub/discord_sharding.py tests/test_discord_sharding.py
git commit -m "fix(discord): verify media recovery before closure"
```

### Task 7: Run Cross-Layer Verification and Prepare the Frozen-Run Resume Gate

**Files:**
- Modify only if a test exposes a root-cause defect: files already listed in Tasks 1–6 and their tests
- Generated outside Git after live collectors terminate: `/Users/hzh/discord-exports/v2/runs/<run-id>/media-recovery-audit.json`

**Interfaces:**
- Consumes: all Tasks 1–6 and the existing formal plan/request/policy hashes.
- Produces: a verified implementation branch and an operational go/no-go record; it does not itself authorize touching a live collector.

- [ ] **Step 1: Run all Discord suites with ResourceWarning fatal**

Run:

```bash
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v \
  tests.test_discord_api \
  test_discord_media_recovery \
  test_discord_media_audit \
  tests.test_discord_collector \
  tests.test_discord_sharding
```

Expected: all Discord tests pass, zero warnings, zero real network calls.

- [ ] **Step 2: Run the whole repository hard gate**

Run:

```bash
PYTHONWARNINGS=error::ResourceWarning make test
```

Expected: zero failures and zero `ResourceWarning`. Restore but do not stage any prompt compile fixtures changed only by test side effects.

- [ ] **Step 3: Perform the final whole-branch review**

Generate a branch review package from `96730a0` to HEAD. Review exact design coverage, URL/token redaction, SSRF negative tests, logical sequence cap, crash reuse, old-attempt preservation, deterministic artifact, hostile closure reconstruction, and schema compatibility. Fix every Critical/Important finding under RED→GREEN and rerun the affected suite plus the full hard gate.

- [ ] **Step 4: Apply the operational resume gate only after all old processes are terminal**

Before invoking new code, confirm all four exact old Python PIDs are gone with terminal operation/checkpoint evidence; validate plan SHA `fdc4c3bb1770454091494a6b9bc1a584ad510d80f0d90642d50abda4f930d731`, preflight SHA `800d24f71fbf64bef234ae31f3d2e337c77ca9ff6228fc968aca44b53ceb44f1`, and policy inputs SHA `17b89647c19c760f58058291784f0fa55a6b55f7c91c23db738a4221d704e325`; require SQLite `quick_check=ok`, zero unreconciled pending rows, at least 50 GiB free, and zero token leakage. If any value differs, stop before resolver/socket I/O.

Resume each original run-id with its original shard/request. Require the legacy unsafe attempt to remain, every new typed attempt to have valid sequence/policy/retry provenance, all audit descriptors/hashes/counts to verify, 400/404/415 to remain compensation evidence, and reference-only to remain non-binary. This operational step may still end partial when real media blockers remain and must never be described as complete by the code implementation alone.

- [ ] **Step 5: Commit only genuine verification fixes**

If Step 1–3 required a code correction, commit the scoped tested fix with a concrete subject such as `fix(discord): preserve recovery audit invariants`. If no code changed, create no empty commit. Live evidence remains outside Git.
