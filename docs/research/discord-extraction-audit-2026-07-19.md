# Discord extraction audit — 2026-07-19

Status: evidence collection is **running, not complete**. The bot token is valid, the corrected collector passed the repository and two live-smoke gates, and all four formal shards started between `2026-07-19T18:19:57Z` and `2026-07-19T18:22:36Z`. Private archived threads remain permission-blocked and final merge/closure validation has not yet run.

## 1. Credential and live-access result

- Token source: `/Users/hzh/.config/dce/bot-token`, owner-only mode `0600`.
- The token was read inside the process, never placed in argv, manifests, request payloads, raw pages, or audit output.
- Live probe succeeded for guild `1427104065959231640` and bot `1528033468045463552`.
- Live inventory returned 447 guild channels and 131 active threads.
- Target channel `1517580102572179597` returned 29 recent messages with readable bodies.
- A fresh 132-target preflight found 131 targets directly in the guild/active-thread graph; the one archived thread absent from that graph (`1488356135282016356`) returned HTTP 200 individually with the pinned type and parent. All 132 static targets were therefore API-readable at preflight time, and all nine explicit thread-parent relationships matched.
- The guild permission bitset exposed for the bot includes `VIEW_CHANNEL` and `READ_MESSAGE_HISTORY`, but not `MANAGE_THREADS` or `ADMINISTRATOR`. This independently explains why authorized messages work while unjoined private-archive enumeration remains blocked.
- A byte scan of the live smoke run and operation audit found zero token-containing files.

The fresh preflight is persisted at `/Users/hzh/discord-exports/v2/preflight/discord-scope-20260719T1629Z.json` (SHA-256 `800d24f71fbf64bef234ae31f3d2e337c77ca9ff6228fc968aca44b53ceb44f1`). It records only IDs, counts, status codes, permission booleans, hashes, and audit identifiers; it contains no token or message body.

The token is therefore usable. Token validity does not imply permission completeness.

## 2. Discord API boundary

- The bot can enumerate guild channels, active threads, public archived threads, joined private archived threads, messages, channel pins, attachments, and embed media permitted to it.
- `GET /channels/{id}/threads/archived/private` returned HTTP 403 on target text channels. The endpoint requires `READ_MESSAGE_HISTORY` and `MANAGE_THREADS`; joined-private enumeration succeeded, so the missing capability is consistent with `MANAGE_THREADS`, not a bad token.
- Without `MANAGE_THREADS`, the defensible result is “authorized public and joined-private scope covered”; it is not “all private archived threads covered.”
- A user's Discord sidebar/Favorites or personal pinned-channel list is not exposed to a bot. The user supplied the priority labels manually.
- Pinned messages inside an authorized channel are API-visible and are collected separately.
- Discord Go Live video/audio frames are not exposed through the Bot REST API. Messages, links, thumbnails, embeds, and uploaded video/audio attachments associated with a live session remain collectable when Discord exposes them.
- Model/bot messages sent into a channel are ordinary visible messages. A model provider's private prompt, chain of thought, or backend state is not API-visible.
- Historical REST completeness means all objects still exposed to this bot within the committed capture interval. Discord cannot return a message that was deleted before collection, and a REST message snapshot contains the latest content plus `edited_timestamp`, not the prior edit revisions. Without a Gateway event recorder already running, no honest audit can claim coverage of every transient/deleted message or every historical edit state.

## 3. Priority target scope

- User labels resolved: 130.
- Unique static targets: 132.
- Current composition: 123 root/parent objects and 9 explicit public threads.
- Pending label resolution: 0.
- Target-ID-set SHA-256: `9a22c9ed110c05a7505dc0292c6ea28a8121f1346c934fcf481204c49026888c`.
- Current canonical snapshot SHA-256: `db788cc3e480351af735dfd56f73fbc339353aa753d4f976d57a1038a5fe50a5`.
- Snapshot file byte SHA-256: `ed25ad1ac85b55fc0e02370c47b7fbc012a7d7b917ea68cf9e2add815e4ae2c3`.
- Snapshot path: `/Users/hzh/discord-exports/v2/targets/cia-erfu-pinned-expanded.json`.

The live graph revalidation repaired parent/type metadata for nine explicit threads. Checking only the ID-set hash would not have detected those metadata corrections, so every run must also pin the canonical snapshot hash.

Dynamic scope is larger than 132 objects. The latest census found about 121 related active/archived threads, 112 of which are not explicit static targets. A complete run must union active, public-archived, joined-private-archived, and message-embedded threads and then deduplicate by thread ID.

## 4. Existing exports — strict completeness result

Primary legacy bot export:

- `/Users/hzh/discord-exports/full/CIA_二服_bot`
- 597 JSON files, about 1.026 GiB.
- 583 numeric raw wrappers plus 14 derived replay files mixed into the same directory.
- 769,981 messages across the 583 numeric raw wrappers; the 14 derived JSON files contain no top-level `messages` records.
- Sixteen files stop at exactly 10,000 messages; nine are in the current 132-target scope.
- The old fetchers paginated newest-to-oldest and stopped at 10,000, skipped existing files, used an incorrect archived-thread cursor, and had no durable coverage manifest.

Legacy DCE export:

- `/Users/hzh/discord-exports/full/CIA_二服`
- 361 files; 19 are not valid whole JSON documents.
- 265,177 messages are strictly parseable.

Thread evidence:

- 419 unique message-embedded thread IDs were observed in the old export.
- Only nine had independent history exports.
- 410 therefore lack independently demonstrated thread-history coverage.

The old files are useful recovery evidence, but they do not prove full history and must not be treated as the new run's completion source.

The earlier AI's rankings and “full replay” claims are not accepted as evidence. Its own session log first acknowledged analysis on about 42% of an estimated scope, later reported changing message/file totals, and explicitly skipped a very large OXSUN discussion thread as “闲聊” even after the user required every message to be considered. Its extraction rules also discarded replies, result messages, and some edited messages before reconstructing trade state. The same session later acknowledged symbol/date/position-size false positives, missed GOLD calls, duplicate edits, and exit confirmations misclassified as entries. These are structural selection and state-linking errors, not small score adjustments.

## 5. Author and timestamp truth

Within the legacy files corresponding to the current static scope, 226,549 messages were available for field audit:

- 226,549/226,549 have message ID, channel ID, author object, author ID, author username, and timestamp.
- 226,549/226,549 timestamps parse as ISO time.
- 226,549/226,549 timestamps exactly match the millisecond creation time derived independently from the Discord Snowflake message ID; maximum observed delta was 0 ms.
- 10,150 messages have a non-null edit timestamp.
- Only 10,103 have a non-null `global_name`; absence is upstream data, not a parser loss.
- No audited message contains a guild `member` object, so guild nickname cannot be reconstructed from this response set alone.

Identity requires two layers:

- 216,341/226,549 messages are webhook deliveries and 216,341/216,341 have `author.id == webhook_id`.
- Those IDs identify the forwarding webhook, not necessarily the original blogger's Discord account.
- The webhook username is an exact snapshot of what Discord displayed at send time, but it is mutable and is not proof of the off-platform human identity.

Downstream records must therefore keep `discord_delivery_identity` (provable author/webhook envelope) separate from `claimed_source_identity` (channel/name/embed-derived attribution with confidence and evidence).

## 6. Media coverage

Legacy 132-target static-file census:

- 72,983 attachments, including 72,276 images, 441 videos, 96 audio files, and 170 other files.
- 2,392 top-level embed images, 8,253 embed thumbnails, and 1,593 embed video references.
- 82,483 unique top-level media URLs.
- Declared attachment size is about 14.819 GiB; no known attachment exceeds the collector's 512 MiB per-file limit (largest declared file: 107,004,951 bytes).
- Legacy local caches contain only 607 images (about 125.8 MiB) and no local video/audio files.

The old data also proves a nested-media gap in the first collector revision:

- 181 attachment URLs occur inside forwarded `message_snapshots`.
- 79 occur inside `referenced_message`.
- 24 occur inside a referenced message's nested snapshot.
- Additional nested snapshot/reference embed images, thumbnails, and videos exist.

Raw Discord JSON preserves these nested objects, but the first asset index only materialized top-level attachments and top-level embed image/thumbnail/video. Nested materialization must be added before the expanded run is accepted.

One-channel unlimited smoke (`smoke-full-20260719T2230`) verified:

- 29/29 unique messages with author ID, username, and parseable timestamp.
- 14/14 JPEG assets downloaded and independently decoded for pixel dimensions with macOS image tooling.
- 0 missing blobs and 0 SHA-256 mismatches after an independent second hash pass.
- 0 token-containing files.

The first smoke on the provenance/streaming revisions (`smoke-drhash-d830b49-20260719T175325Z`, collector HEAD `d830b49`) deliberately failed the launch gate rather than being mislabeled complete:

- Operation `dcdb3b6f-1972-4541-818a-42eb3235d37c`, audit `e2355240-5a21-4c62-80b7-4bc16e24bcd9`.
- 29/29 unique root messages have an author ID and aware timestamp; 29/29 message-evidence rows are complete with zero diagnostics.
- All 29 are webhook/bot deliveries, reinforcing the two-layer identity rule in section 5.
- 16 media records made 32 direct/proxy attempts and all were rejected as `unsafe_media_url`; no binary was claimed captured.
- The candidate hosts resolved locally only into `198.18.0.0/15`, the RFC 2544 benchmark range used by the active Clash Verge Fake-IP DNS/TUN mode. A credential-free HTTPS diagnostic succeeded through that local tunnel, so the failure is an environment/security-policy mismatch rather than an expired bot token or a missing attachment URL.
- Full launch remains `NO-GO` until the exception is explicitly opt-in, restricted to normalized Discord-owned media hosts, preserves TLS/SNI/certificate and redirect validation, remains token-free, is bound into immutable request identity, and a new-run smoke reports zero failed media. Broadly allowing non-public DNS answers is not acceptable.

That gate was subsequently fixed in revisions `dc151f2` and `8247ea4` and passed two new-run live smokes:

- `smoke-drhash-8247ea4-20260719T181312Z`: 29 root messages, 29 evidence nodes, 16 media occurrences, 16/16 binaries complete, zero failed media, zero evidence diagnostics, and only the expected private-archive HTTP 403. Manifest SHA-256: `3d6b6d0c442baac0715577d9890258777c7439c73929f60a6b3106a4f2c4a1c9`.
- `smoke-nested-8247ea4-20260719T181438Z`: 22 root messages plus one nested snapshot node, 22/22 binaries complete, and the known nested message `1527507306109997077` and attachment `1527507305933832212` both have durable evidence/blob coverage. Its sole evidence warning is `snapshot_timestamp_reference_mismatch`: both upstream values are retained and parseable, with no error, partial message, or missing asset. Manifest SHA-256: `2f22f74a9d3651c5d3dca908b26a4dff72ac05a4c39919a3f61dde8bc20bd9f7`.
- Independent rehash, size, image-decode, SQLite integrity, resume/no-op, symlink, and token scans passed for both runs. The media policy is default-off and confined to exact Discord-owned media hosts on port 443; its immutable inputs SHA-256 is `17b89647c19c760f58058291784f0fa55a6b55f7c91c23db738a4221d704e325`.

Strict media states must distinguish “binary unavailable” from “binary captured with upstream metadata mismatch.” In the stopped expanded run, most media failures had a valid hashed blob but Discord-declared MIME/size disagreed with the CDN response. External embed URLs such as YouTube pages are references, not direct video binaries; they must remain reference-only rather than be reported as downloaded video.

## 7. New evidence storage

Root: `/Users/hzh/discord-exports/v2`

- Targets: `targets/*.json`
- Immutable run request: `runs/<run-id>/request.json`
- Legacy-request compatibility proof, when applicable: `runs/<run-id>/request-v2-amendment.json` plus `request-v2-migration-marker.json`; these bind the immutable v1 request hash and previously captured bot-principal evidence without rewriting `request.json`.
- Raw API pages: `runs/<run-id>/pages/<stream>/<page>.json`
- Deterministic per-message evidence, including referenced messages and snapshots: `runs/<run-id>/message-evidence/<stream>/<page>.jsonl`
- Inventory snapshots: `runs/<run-id>/inventory/`
- Resume state: `runs/<run-id>/checkpoint.json`
- Transactional media metadata ledger: `runs/<run-id>/asset-ledger.sqlite3` (SQLite WAL while active; checkpointed before final hashing).
- Per-logical-media evidence: `runs/<run-id>/asset-records/`
- Content-addressed blobs: `runs/<run-id>/assets/sha256/<prefix>/<sha256>.<ext>`
- Coverage roll-up: `runs/<run-id>/manifest.json`
- Endpoint errors: `runs/<run-id>/errors.jsonl`
- Operation audit: `/Users/hzh/discord-exports/v2/.omni/audit/events.jsonl`
- Parent-family weights: `/Users/hzh/discord-exports/v2/targets/cia-erfu-family-weights-20260719.json` (123 families; SHA-256 `ca2796d6f98a1b345dbad20dd17df04ab8e221ff8308e144731baa8bf4fe8775`).
- Formal four-shard plan: `/Users/hzh/discord-exports/v2/plans/full-pinned-20260719T170645Z/plan.json` (SHA-256 `fdc4c3bb1770454091494a6b9bc1a584ad510d80f0d90642d50abda4f930d731`; operation `edd85f81-5344-4226-a9ec-5497e389a47c`, audit `1aa016d7-0f66-49a5-aa7d-b739fb149cfa`). The four shard target/family/estimated-weight tuples are `1/1/292055`, `41/38/75519`, `40/38/75521`, and `50/46/75518`; manifest SHA-256 values are `b39702a0ccdc9c39c6474316cf2c1ce3545b4a4562e2ee40646c429867a35919`, `00806bba450abf98b2723326ad44c05305dab4d668e4c036dcedad2d747ef9e3`, `616c11b1acdde0c43c9a922cf051bb7c49d17e63a2c10fecc0aaf84169735bbe`, and `fd895586c8d00d5cd577783d8ccaf022d155204581b7996a4c41a0c4f050cc0c`. The OXSUN family is intentionally indivisible.

The intentionally stopped stale run remains at `runs/full-pinned-20260719T2245`. Its manifest is correctly `partial`; it contains 7,900 messages and 352 asset records and is retained as audit evidence, not reused as proof of completion.

The four active formal run IDs are `cia-erfu-pinned-v2-8247ea4-s01`, `cia-erfu-pinned-v2-8247ea4-s02`, `cia-erfu-pinned-v2-8247ea4-s03`, and `cia-erfu-pinned-v2-8247ea4-s04`. They were launched from collector code baseline `8247ea458e235f1407c1f2b0f753fdf27c548842`, with owner-only output permissions, the versioned Fake-IP policy explicitly enabled, no page limit, and media downloads enabled. Their current checkpoints are progress evidence, not completion evidence.

## 8. Performance blocker and corrected execution plan

The first collector revision rewrote the whole asset index and a growing global checkpoint on each asset-state change. At about 82,000 media records this was quadratic and could generate roughly 10–40 TiB of metadata writes, plus excessive `fsync` calls. The monolithic run was stopped before meaningful sunk cost. The replacement uses a transactional SQLite asset ledger, per-record immutable evidence, a streamed final index, content-addressed blobs, generation compare-and-swap, directory `fsync`, and resume-time rehashing. Revisions `7cf4df3` and `d830b49` additionally bind message/page provenance and stream stored/fresh pagination with a 1,500-page constant-live-payload regression. Revisions `dc151f2` and `8247ea4` add the confined Clash/TUN media policy and connect-time fail-closed tests. Warning-strict targeted tests passed 122/122 and the final full repository suite passed 1,158/1,158 with zero `ResourceWarning`; both corrected live-media smokes also passed, so the four formal shards were launched.

Required order:

1. Remove per-asset full-index/checkpoint rewrites while retaining crash-safe atomic records and tamper detection.
2. Make unchanged resume a no-op for completed assets and retry only transient failures.
3. Add nested media discovery and author/timestamp validation.
4. Split by indivisible parent families into four disjoint runs. Explicit and dynamically discovered threads must stay with their parent owner.
5. Validate shard static-set disjointness/union, canonical parent snapshot hash, dynamic thread ownership, raw-page hashes, asset logical identities, and blob hashes in a reference-only merged manifest.
6. Use the preflight's committed `captured_at` (`2026-07-19T16:29:06Z`, source SHA-256 `800d24f71fbf64bef234ae31f3d2e337c77ca9ff6228fc968aca44b53ceb44f1`) as the common lower boundary `T_close`. After the shards finish, first freeze a millisecond-aligned exclusive upper boundary `H`, then rerun the thread census and paginate every message-bearing static target plus every owned thread newest-to-oldest with `before` until the lower boundary is crossed. The point-in-time result is the verified union of the initial history and all captured `(T_close, H)` deltas; a non-empty verified delta is coverage, not a failure. A census thread absent from the merge and older than `T_close` remains a blocker unless a separate newest-to-empty full-history acquisition covers it. Forum roots do not expose `/messages` and are excluded from head-message catch-up while their owned threads remain required.

The realistic target after launch is about 8–12 hours, subject to Discord/CDN rate limits and the large indivisible OXSUN parent family. This is an estimate, not a completion guarantee; merge and `(T_close,H)` closure validation follow the raw acquisition and can still find blockers.

## 9. Backtest and market-data truth

Legacy backtest code and outputs:

- Code: `/Users/hzh/discord-exports/replay/full_backtest.py`
- JSON output: `/Users/hzh/discord-exports/replay/full_backtest_results.json`
- Markdown report: `/Users/hzh/Desktop/简历/个人知识库/agent-harness/BITE/obsidian-vault/全历史回测按月统计.md`

The script covers only ten hard-coded channels, not the 132-target scope. It does not retain complete per-trade message provenance, and its huge PnL results are not trustworthy. It also has current-hour-close look-ahead, intrabar ordering ambiguity, duplicate-signal risk, and incomplete symbol/timeframe coverage.

Market bars:

- Root: `/Users/hzh/quant/market`
- Approximate size: 44.02 GiB.
- Ingest ledger: `_ingest_manifest.jsonl` (59,581 lines at audit time).
- Available bar directories: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`.
- No audited `trades`, `quotes`, `orderbook`, or `_reference` stores were present.
- The old backtest uses only `1h` bars.

Paths previously claimed for `agent-harness/quant/quant/kol_replay.py`, `/Users/hzh/quant/market/ledger/kol_calls.jsonl`, and `/tmp/fengge_replay.json` do not exist and must not be cited as completed work.

Backtesting must wait until Discord coverage and source-attribution ledgers pass their gates. Each trade event must cite message ID, channel/thread, author envelope, timestamp, relevant media/blob hashes, extraction rule/model output, and the exact market-bar rows used.
