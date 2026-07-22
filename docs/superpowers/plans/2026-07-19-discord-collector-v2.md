# Discord Collector v2 Implementation Plan

> **For Codex:** Execute this plan continuously with `superpowers:subagent-driven-development`, one implementation task at a time, with task-scoped review after each task.

**Goal:** Replace the capped, thread-incomplete Discord scripts with a Bot API collector that creates immutable raw pages, media hashes, resumable checkpoints, and truthful completeness manifests for the recovered 32+1 audit baseline and the user's expanded 132-object pinned target set.

**Architecture:** A stdlib-only REST transport and pagination layer lives in `src/omni_hub/connectors/discord.py`. `src/omni_hub/discord_collector.py` orchestrates inventory, thread discovery, messages, pins, assets and the evidence archive. Two CLI operations are registered through `OperationRunner`; credentials are read only inside the handler and never enter the payload or outputs.

**Tech Stack:** Python 3.12 stdlib (`urllib`, `json`, `hashlib`, `pathlib`, `dataclasses`), existing `OperationRunner`, `argparse`, `unittest` with injected fake transports.

## Global Constraints

- Never print, persist, pass in argv, or place the Discord bot token in an `OperationSpec` payload. The default credential file is `~/.config/dce/bot-token` and must pass owner/mode/symlink checks.
- No Discord SDK dependency in the main package; this collector is Acquisition, not the `Channel.listen/reply` adapter.
- Raw Discord JSON and unknown fields remain intact. Derived indexes may normalize, but raw page evidence is immutable.
- Default collection has no page cap. Any explicit `max_pages` is a test/smoke limit and must produce `truncated_by_limit`, never `complete`.
- Public/private archived thread cursors are ISO8601 `archive_timestamp`; joined-private archived cursor is thread ID; message cursor is last message ID; pins cursor is `pinned_at`.
- A 403/404/invalid cursor is an explicit stream status and cannot be treated as an empty complete stream.
- Local writes must be invoked through a registered `OperationRunner` operation, and all output paths must stay under its workspace.
- New modules have focused tests. Tests use scripted transports and must never fall through to the real network.
- Preserve unrelated user changes. Do not modify `/Users/hzh/discord-exports/replay/` or old raw exports.
- Before completion, `make test` must report 0 failures and 0 `ResourceWarning`; restore any tracked prompt fixtures changed by test side effects without committing those side effects.

### Task 1: Restore the clean test baseline

**Files:**
- Modify: `tests/test_market_store.py`

- [ ] **Step 1: Reproduce the existing failure**

Run `make test` or `python -m unittest tests.test_market_store -v` with the repository's Python 3.12 runtime. Confirm `FileNotFoundError` points at `agent-harness/quant/market_store.py`.

- [ ] **Step 2: Confirm the refactor source**

Verify the tracked module is `agent-harness/quant/quant/market_store.py` and that commit history moved the implementation while the resurrected test retained the legacy path.

- [ ] **Step 3: Apply the single root-cause fix**

Change only `_MODULE_PATH` to include the package's second `quant` component. Do not touch quant implementation behavior.

- [ ] **Step 4: Verify and commit**

Run `python -m unittest tests.test_market_store -v`; expected: 4 tests pass. Commit only this test-path repair.

### Task 2: Implement credential-safe Discord REST pagination

**Files:**
- Create: `src/omni_hub/connectors/discord.py`
- Create: `tests/test_discord_api.py`
- Modify: `src/omni_hub/connectors/__init__.py` only if an export is required

- [ ] **Step 1: Write failing credential and transport tests**

Cover: `0600` owner-only regular token file succeeds; symlink/group/other-readable/empty token fails; Authorization never appears in exception strings; ISO8601 query cursors encode `+` as `%2B`; injected sleep follows JSON `retry_after` for 429; bounded 5xx retry stops deterministically.

- [ ] **Step 2: Run the tests and observe the missing-module failure**

Run `python -m unittest tests.test_discord_api -v`; expected: import failure before implementation.

- [ ] **Step 3: Implement the minimum transport**

Implement `read_bot_token`, `DiscordHTTPTransport`, typed `DiscordAPIError`, JSON decoding, rate-limit retry and byte streaming/download hooks. Keep token only in the Authorization header and redact errors.

- [ ] **Step 4: Write failing paginator tests**

Use a strict scripted transport that rejects unexpected calls. Cover messages, public/private archived threads, joined-private archived threads, pins, `has_more=true` with an empty/non-advancing page, and explicit `max_pages` status.

- [ ] **Step 5: Implement endpoint-specific paginators**

Expose page objects containing raw payload, request path/params, next cursor, item count and terminal status. Preserve Discord response objects unmodified.

- [ ] **Step 6: Verify and commit**

Run `python -m unittest tests.test_discord_api -v`; all tests pass with zero real network calls. Commit API module and tests.

### Task 3: Build the immutable evidence collector

**Files:**
- Create: `src/omni_hub/discord_collector.py`
- Create: `tests/test_discord_collector.py`

- [ ] **Step 1: Write failing archive and discovery tests**

Cover: target JSON validation; output path containment; exact guild inventory; union/dedup of active, message-embedded, public/private/joined archived and explicit threads; private archive 403 does not prevent other sources; forum parent handling; raw unknown fields survive.

- [ ] **Step 2: Run tests and observe the missing-module failure**

Run `python -m unittest tests.test_discord_collector -v`; expected: import failure.

- [ ] **Step 3: Implement run archive and thread discovery**

Create the run layout from the design. Write JSON atomically, use exclusive page identities, record per-stream checkpoint/status, and refuse a same-path content mismatch. Discover only allowlisted targets and their threads; store the full guild graph for audit.

- [ ] **Step 4: Write failing message, pins and media tests**

Cover exact page counts and terminal reasons; same message/attachment seen through messages and pins downloads once; two attachment IDs with identical bytes create two index rows and one blob; embed image/thumbnail/video URLs are included; filename traversal cannot affect paths; byte count/MIME/download failures yield partial media status.

- [ ] **Step 5: Implement collection and media hashing**

Collect message-bearing targets and discovered threads, then pins. Store page payloads unchanged. Stream media to an owned temporary file, hash SHA-256, atomically promote content-addressed blobs, append an index row without token data, and continue after item-level media errors.

- [ ] **Step 6: Write failing resume and offline E2E tests**

Interrupt after a page, resume the same run from checkpoint, assert no page overwrite/duplicate asset, and assert the complete synthetic fixture has no unused or unexpected scripted responses. A limited fixture must end `partial` with `truncated_by_limit`.

- [ ] **Step 7: Implement checkpoint resume and manifest roll-up**

Only mark a run complete when every required stream and required asset is complete. Include `not_api_exposed` limitations for Go Live and personal Favorites.

- [ ] **Step 8: Verify and commit**

Run `python -m unittest tests.test_discord_collector -v`; all tests pass. Commit collector and tests.

### Task 4: Register audited CLI operations

**Files:**
- Create: `src/omni_hub/cli/discord.py`
- Modify: `src/omni_hub/cli/__init__.py`
- Modify: `src/omni_hub/builtins.py`
- Create: `tests/test_discord_cli.py`

- [ ] **Step 1: Write failing registry and CLI tests**

Cover `discord-probe` as READ_ONLY, `discord-collect` as LOCAL_WRITE, parser/dispatch, mock transport collection, path escape rejection, no token in audit JSONL, and outputs containing counts/artifact paths rather than message bodies.

- [ ] **Step 2: Run tests and observe missing commands**

Run `python -m unittest tests.test_discord_cli -v`; expected: parser or registry failure.

- [ ] **Step 3: Implement operations and commands**

Add a dedicated CLI area because there are two commands. `discord-probe` accepts guild ID and token-file path. `discord-collect` accepts guild ID, target JSON, relative output directory, optional run ID, optional smoke-only `max_pages`, and asset toggle. Resolve credentials only inside handlers.

- [ ] **Step 4: Verify and commit**

Run `python -m unittest tests.test_discord_api tests.test_discord_collector tests.test_discord_cli -v`; all tests pass. Commit CLI/registry integration.

### Task 5: Materialize the audited 32+1 target snapshot and run live smoke

**Files:**
- Create outside Git: `/Users/hzh/discord-exports/v2/targets/cia-erfu-old-32-plus-1.json`
- Create outside Git: `/Users/hzh/discord-exports/v2/targets/cia-erfu-pinned-expanded.json`
- Generated outside Git: `/Users/hzh/discord-exports/v2/runs/<run-id>/...`
- Update: `agent-harness/integrations/discord/README.md`

- [ ] **Step 1: Write the recovered target snapshot**

Include the 32 unique channel links plus explicit thread `1441054113512292393`, with known names/types and `guild_id=1427104065959231640`. Record source as the prior session audit and include a SHA-256 of the canonical target list. Do not include credentials.

Also validate the manually supplied pinned list snapshot: 130 source labels resolve to 132 unique objects because `颜驰` retains two adjacent channels and `周大侠-合约策略` resolves to two distinct threads. Record label normalization and live metadata evidence; no unresolved label may be silently omitted.

- [ ] **Step 2: Update operator documentation**

Document the safe token-file command, probe/collect commands, exact raw output location, capability boundaries, resume semantics and how to interpret `partial` versus `complete`. Remove any implication that a Bot can read personal Favorites or Go Live frames.

- [ ] **Step 3: Run a read-only live probe**

Use the real token file without printing it. Verify identity, guild access, channel graph, one message body's visibility and pins response shape. Capture only boolean/count diagnostics.

- [ ] **Step 4: Run one-target live smoke**

Create a one-target snapshot for low-volume channel `1517580102572179597`. Run once with `max_pages=1` and assert manifest says partial/truncated; then run a fresh unbounded run with assets and assert stream termination, downloaded asset hashes and no secret leakage.

- [ ] **Step 5: Verify whole repository and commit docs**

Run the three Discord suites, then `make test`. Require 0 failures and 0 `ResourceWarning`. Restore tracked prompt fixtures changed solely by test side effects. Commit documentation only; external raw evidence remains outside Git.

### Task 6: Start the full expanded evidence run

**Generated outside Git:**
- `/Users/hzh/discord-exports/v2/runs/<full-run-id>/...`

- [ ] **Step 1: Preflight disk and target coverage**

Report free disk, 132 static target objects, dynamically discovered threads, the 4 known 10k-capped legacy files, and expected request/media scale. Abort only if output space or credentials fail.

- [ ] **Step 2: Start the unbounded collection with assets**

Run from `/Users/hzh/discord-exports/v2` so all writes remain inside the OperationRunner workspace. Use no `max_pages`. Keep the execution session attached for monitoring and resume the same run ID after recoverable interruption.

- [ ] **Step 3: Audit completion before downstream analysis**

Compare new per-target message counts against legacy counts, list every blocked/failed/partial stream and missing asset, and explicitly reconcile the four prior 10k files and all discovered threads. Do not begin backtest ingestion while required raw streams remain partial.

### Task 7: Add evidence-grounded periodic summaries

**Files:**
- Create: `src/omni_hub/discord_review.py`
- Modify: `src/omni_hub/discord_collector.py`
- Modify: `src/omni_hub/cli/discord.py`
- Modify: `src/omni_hub/builtins.py`
- Create: `tests/test_discord_review.py`
- Create or modify the applicable `scripts/launchd/` template only after matching the repository scheduler pattern

- [ ] **Step 1: Write failing incremental and coverage-ledger tests**

Cover `after`-cursor collection, cursor advance only after atomic persistence, exact duplicate folding with all backlinks retained, every non-duplicate message assigned to a batch, every unique media SHA assigned to a visual/ASR queue, and arbitrary `[start,end)` interval selection.

- [ ] **Step 2: Implement incremental raw capture**

Add an incremental mode that reads per-channel newest IDs from completed manifests. It must retain the same raw/archive guarantees as backfill and may not let summary relevance filter collection.

- [ ] **Step 3: Write failing trade-state and citation tests**

Use fixtures containing limit pending/cancel, reply-based fill confirmation, add/reduce, moved stop, partial TP, image-only levels and contradictory follow-ups. Assert events retain raw message/media IDs; a limit order without fill evidence remains unconfirmed; self-report and K-line grade are separate fields.

- [ ] **Step 4: Implement model-work packet generation**

Create deterministic coverage batches and enqueue them to a headless codex/claude lane through `TaskQueue`; Application Plane code must not call an LLM directly. The worker contract reads all batch messages plus linked media, writes channel evidence cards/trader timelines, and reports consumed IDs and unresolved IDs.

- [ ] **Step 5: Add independent review and output gates**

An interval can be `complete` only when raw streams are complete, all non-duplicate message IDs and unique media hashes are accounted for, every summary claim has evidence references, and a second reviewer records contradictions/low-confidence cases. Otherwise publish an explicitly partial report.

- [ ] **Step 6: Wire hourly/daily/weekly automation through TaskQueue**

Launchd may enqueue hourly incremental collection plus daily and weekly review packets; workers perform the operations. Add dry-run/status commands and tests proving schedules cannot bypass policy/audit or leak credentials.
