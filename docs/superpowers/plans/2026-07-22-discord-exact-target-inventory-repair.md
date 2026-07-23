# Discord Exact-Target Inventory Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild a hash-bound, redacted inventory for all 132 explicit Discord targets so explicit public Threads retain their own message counts while parent Forum families retain rollup counts.

**Architecture:** Read only the already verified baseline plus explicit closure message IDs. Build two projections from one canonical deduplicated corpus: `exact_by_channel` keeps every message's real channel, while `family_owner` additionally rolls discovered Threads into their parent. Publish one canonical JSON artifact through `OperationRunner`; never overwrite or edit the flawed historical reports.

**Tech Stack:** Python 3.12 stdlib, existing Discord closure/merge evidence readers, canonical JSON/SHA-256 helpers, `unittest`, existing OperationRunner/CLI registry.

## Global Constraints

- Do not contact Discord or restart collectors; reuse the verified `831915 + 11119 - 609 = 842425` corpus.
- Do not emit message bodies, raw/signed URLs, bot tokens, media logical keys, or individual message IDs.
- Keep explicit Thread rows on exact-channel semantics and parent rows on family-rollup semantics; target-row sums may overlap, while the authorized corpus count must remain unique.
- Validate the 132-target snapshot, merge/closure bindings, discovered Thread parent mapping, and all source hashes before publishing.
- Publish to a new path with no-clobber semantics and mode `0600`; do not modify the old `all-sources-*` evidence.
- Keep `full_private_scope_complete=false` while 123 unjoined private-archive enumerations remain HTTP 403.
- Preserve unrelated changes under `prompts/**` and `.superpowers/`.
- Final verification must have zero failures and zero `ResourceWarning`.

---

### Task 1: Add the dual-view target inventory and safe publisher

**Files:**
- Create: `src/omni_hub/discord_blogger_inventory.py`
- Modify: `src/omni_hub/discord_blogger_corpus.py`
- Modify: `src/omni_hub/cli/discord.py`
- Modify: `src/omni_hub/builtins.py`
- Create: `tests/test_discord_blogger_inventory.py`
- Modify: `tests/test_discord_cli.py`

**Interfaces:**
- Consumes: `BloggerMessage`, the validated target snapshot, merge `discovered_threads`, and the closure-bound authorized message corpus.
- Produces: `build_blogger_target_inventory(...) -> dict[str, object]`, `publish_blogger_target_inventory(...) -> dict[str, object]`, and CLI command `discord-blogger-inventory-build`.

- [x] **Step 1: Write the failing projection tests**

Create a parent Forum, an explicit public Thread, and a dynamic public Thread. Feed one exact Thread message and one dynamic Thread message, then assert:

```python
self.assertEqual(rows[forum_id]["message_count"], 2)
self.assertEqual(rows[explicit_thread_id]["message_count"], 1)
self.assertEqual(result["unique_authorized_message_count"], 2)
self.assertEqual(result["per_target_message_sum"], 3)
self.assertEqual(rows[explicit_thread_id]["count_semantics"], "exact_thread")
```

Also assert no output contains message content, URL values, `logical_key`, or a list of message IDs.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v tests.test_discord_blogger_inventory
```

Expected: import failure because `discord_blogger_inventory` does not exist.

- [x] **Step 3: Implement the minimal dual-view reducer**

Implement these public functions:

```python
def build_blogger_target_inventory(
    *,
    messages: Iterable[BloggerMessage],
    target_snapshot: Mapping[str, object],
    discovered_threads: Sequence[Mapping[str, object]],
    provenance: Mapping[str, object],
) -> dict[str, object]: ...

def publish_blogger_target_inventory(
    *, workspace: Path, output_path: Path, inventory: Mapping[str, object]
) -> dict[str, object]: ...
```

Use `exact_by_channel[channel_id]` for explicit Thread rows and `family_owner[channel_id]` only for parent family rollups. Derive the unique corpus count and commitment from the canonical message union, not the sum of target rows. Use an exclusive atomic file publish and force mode `0600`.

- [x] **Step 4: Add a verified authorized-scope corpus mode**

Extend the corpus reader with a mode that accepts exactly `merge.required_head_catchup_target_ids`. Validate that this set equals the message-bearing static targets plus all discovered Thread IDs before reading pages. Preserve the current static-target default for existing callers.

- [x] **Step 5: Add CLI and OperationRunner plumbing**

Register `discord_blogger_inventory_build` in `builtins.py` and expose:

```bash
discord-blogger-inventory-build \
  --export-root discord-exports/v2 \
  --closure-audit discord-exports/v2/closure/full-pinned-cdd1ad0-s01retry-20260721T0009Z/capture/closure-audit.json \
  --targets discord-exports/v2/targets/cia-erfu-pinned-expanded.json \
  --output discord-exports/v2/derivatives/blogger-results/exact-target-inventory-<timestamp>.json
```

All paths remain contained beneath the selected workspace and the operation risk is `LOCAL_WRITE`.

- [x] **Step 6: Run focused and related tests**

Run:

```bash
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -m unittest -v \
  tests.test_discord_blogger_inventory \
  tests.test_discord_blogger_corpus \
  tests.test_discord_cli
```

Expected: all tests pass with no warnings.

- [x] **Step 7: Build and verify the new artifact**

Run the command from `/Users/hzh`, verify the result has 132 exact target rows, zero rows incorrectly marked inaccessible, unique corpus count 842425, and the nine explicit public Thread counts:

```text
2911, 1586, 183, 50373, 1, 294, 13, 8820, 1043
```

Verify the file mode is `0600`, the old reports are unchanged, and a token/signed-URL/message-body/logical-key scan returns zero findings.

- [x] **Step 8: Run full verification and independent review**

Run:

```bash
PYTHONWARNINGS=error::ResourceWarning make test
```

Expected: zero failures and zero `ResourceWarning`. Request an independent review of the task diff and fix every Critical/Important finding before reporting completion.
