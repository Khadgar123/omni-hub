# Discord evidence collector

**Status:** evidence-first collector implemented and under live verification. This is
not a production-readiness claim. Code-path/scaffolding completion and real-data
closed-loop completion are separate: coverage of the 132 requested targets is true
only when the corresponding run's `manifest.json` says what was completed and what
was blocked, truncated, or failed.

The collector uses the Discord Bot REST API. It preserves returned message pages as
raw evidence, discovers relevant active and archived threads, captures channel pins,
and can download message attachments plus embed image/thumbnail/video URLs. It does
not send Discord messages or place trades.

## Broker contract

The read-only evidence collector is separate from the Interface Plane's Discord
channel broker. `src/omni_hub/channels/external_stubs.py::DiscordChannel` still
defines the `listen` / `reply` / `health_check` / `shutdown` boundary for a future
`discord.py` adapter. This REST collector implements only `discord-probe` and
`discord-collect`; it does not satisfy that channel contract or send replies. Until
an adapter is wired under this integration, channel health remains not configured
and `listen` / `reply` remain unavailable.

## Credential handling

The default token file is:

```text
/Users/hzh/.config/dce/bot-token
```

It must be a regular file owned by the current user with no group or other
permissions (`0600`). The collector refuses symlinks, empty files, foreign-owned
files, and files readable by another user. Never put the token in a command-line
argument, environment variable, repository file, target manifest, log, or pasted
terminal output. Never inspect it with `cat`, `head`, shell tracing, or a command that
prints the value.

Write or rotate it without echoing the secret:

```bash
/usr/bin/python3 -c 'import getpass,os,pathlib,tempfile; p=pathlib.Path("/Users/hzh/.config/dce/bot-token"); p.parent.mkdir(parents=True,exist_ok=True); t=getpass.getpass("New Discord Bot Token: ").strip(); assert t,"Token 不能为空"; fd,tmp=tempfile.mkstemp(prefix=".bot-token.",dir=str(p.parent)); os.fchmod(fd,0o600); os.write(fd,t.encode()); os.close(fd); os.replace(tmp,p); print("已安全写入",p,"权限",oct(p.stat().st_mode & 0o777))'
```

Verify only the file mode and owner, never its contents:

```bash
stat -f '%Su %Lp %N' /Users/hzh/.config/dce/bot-token
# Expected current user, mode 600, and the path above.
```

Rotate/revoke a disclosed token in the Discord Developer Portal before using this
collector again. The bot must also have access to the guild/channels and the Discord
privileged intents required to read message content; a valid token does not grant
permissions the bot role does not have.

## Operator setup

Run the CLI with Python 3.12+ and this checkout's `src` directory on `PYTHONPATH`.
Collection paths are intentionally confined to the current working directory, so
run from the evidence workspace and pass target/output paths relative to it:

```bash
OMNI_HUB_CHECKOUT=/absolute/path/to/omni-hub
OMNI_HUB_PYTHON=/absolute/path/to/python3.12
cd /Users/hzh/discord-exports/v2
```

The reviewed 132-target snapshot is stored at:

```text
/Users/hzh/discord-exports/v2/targets/cia-erfu-pinned-expanded.json
```

The command therefore uses
`targets/cia-erfu-pinned-expanded.json`, not an external absolute path. The collector
rejects target and output paths that escape the working directory or traverse a
symlink.

## Probe bot visibility

`discord-probe` verifies the token, guild inventory, active-thread inventory, and,
when a channel is supplied, one recent message page plus the pins response shape. It
prints only IDs, booleans, and counts; it does not print message bodies or the token.

```bash
DISCORD_GUILD_ID=1427104065959231640
DISCORD_CHANNEL_ID=1517580102572179597
PYTHONPATH="$OMNI_HUB_CHECKOUT/src" "$OMNI_HUB_PYTHON" -m omni_hub.cli \
  discord-probe \
  --guild-id "$DISCORD_GUILD_ID" \
  --channel-id "$DISCORD_CHANNEL_ID"
```

Interpret `message_body_visible: true` as evidence that at least one message body in
that one-page probe was visible. It is not proof that every channel, old thread, or
message is accessible.

## Collect evidence

For the full requested scope, use an explicit, unique run ID, omit `--max-pages`, and
leave asset downloading enabled (that is, do not add `--no-assets`):

```bash
DISCORD_RUN_ID=full-pinned-20260719T230000Z
PYTHONPATH="$OMNI_HUB_CHECKOUT/src" "$OMNI_HUB_PYTHON" -m omni_hub.cli \
  discord-collect \
  --targets targets/cia-erfu-pinned-expanded.json \
  --output-dir . \
  --run-id "$DISCORD_RUN_ID"
```

Useful diagnostic options are:

- `--max-pages N`: cap each paginated stream. This deliberately creates
  `truncated_by_limit` streams and therefore a partial run when more pages exist.
- `--no-assets`: index media references but do not download binaries. The media
  status becomes `not_requested`; this must not be described as visual completeness.
- `--max-asset-bytes N`: per-object download ceiling (default 512 MiB).
- `--asset-chunk-size N`: streaming chunk size (default 64 KiB).
- `--token-file PATH`: override the default token file when needed; the same security
  checks apply.

The collection walks the requested channel graph, active threads, public archived
threads, private archived threads, joined private archived threads, message history,
channel pins, and threads embedded in returned messages. Raw Discord message objects
are retained without summarization, so the exact `message.id`, `author.id`,
`timestamp`, `edited_timestamp`, attachments, embeds, and other fields are available
when Discord returned them. Downstream identity and chronology should use the stable
IDs and API timestamps, not display names alone.

## Run layout and audit trail

With `--output-dir . --run-id "$DISCORD_RUN_ID"`, the run is written under
`/Users/hzh/discord-exports/v2/runs/$DISCORD_RUN_ID/`:

```text
runs/<run-id>/
├── request.json
├── checkpoint.json
├── manifest.json
├── errors.jsonl
├── inventory/
│   ├── bot.json
│   ├── guild.json
│   ├── channels.json
│   ├── active-threads.json
│   └── targets.json
├── pages/<stream-key>/000001.json
├── asset-records/<sha256-of-logical-key>.json
├── asset-index.jsonl
└── assets/sha256/<first-two-hex>/<content-sha256>.<extension>
```

- `request.json` binds the run ID to the canonical target snapshot and collection
  options.
- `checkpoint.json` is the durable stream/page/asset ledger used for recovery.
- `pages/` contains immutable, hash-checked API page envelopes: request path/params,
  raw payload, and pagination evidence.
- `asset-records/` retains source message/channel/stream identity, observed URLs,
  metadata, every attempt, terminal reason, byte count, MIME observations, SHA-256,
  and blob path. `asset-index.jsonl` is a derived consolidated view.
- `assets/sha256/` holds content-addressed binaries. Equal content is addressed by the
  same digest within the run; the collector verifies size and hash before trusting a
  resumed record.
- `errors.jsonl` records endpoint-level failures without storing the bot token.
- `manifest.json` is the final truth for stream and media status. Do not infer
  completeness from directory size, message count, or a successful process exit.

Operations also pass through `OperationRunner`. When the evidence workspace is the
current working directory, probe/collect audit events are appended to:

```text
/Users/hzh/discord-exports/v2/.omni/audit/events.jsonl
```

The audit record contains operation metadata and the bounded result summary. Raw
message bodies remain in the run evidence pages, not in CLI output.

## Resume and immutability semantics

For an interrupted run, rerun the **same command** with the **same explicit run ID,
target snapshot, and collection options**. The collector verifies all previously
landed page hashes and asset-record/blob integrity, replays a landed-but-unprocessed
page, continues from durable cursors, and retries eligible incomplete asset attempts.
Completed streams and verified blobs are not fetched again.

Do not reuse a run ID for a different target snapshot or different request options;
the immutable `request.json` check rejects that identity change. A run intentionally
ended by `--max-pages` remains `truncated_by_limit`; start a new run ID without the
limit to collect the broader history. If `--run-id` is omitted, the CLI creates a new
ID, so that invocation cannot intentionally resume an earlier run.

## Reading `complete` and `partial`

`manifest.json::status` is `complete` only when every tracked stream is `complete`,
all requested media records are `complete`, and the collector was not interrupted.
Any blocked, missing, malformed, failed, interrupted, or truncated stream—or any
requested media download that does not complete—keeps the run `partial`.

Check the status and all exceptions explicitly:

```bash
jq '{status, errors, media, not_api_exposed}' \
  "runs/$DISCORD_RUN_ID/manifest.json"
jq -r '.streams | to_entries[] | select(.value.status != "complete") | \
  [.key, .value.status, (.value.terminal_reason // "")] | @tsv' \
  "runs/$DISCORD_RUN_ID/manifest.json"
jq -r '[.stream, .status, (.status_code // ""), (.path // "")] | @tsv' \
  "runs/$DISCORD_RUN_ID/errors.jsonl"
```

In particular, Discord may return HTTP 403 for private-archived or
joined-private-archived thread endpoints even when the bot can read the parent
channel. The collector records the stream as `blocked` with `http_403`, writes the
error evidence, and keeps the run `partial`. A 403 is an honest permission/API
boundary, not proof that no archived private threads exist. Fix Discord permissions
or membership and use a new run when the accessible scope changes; never relabel the
blocked stream as complete.

## Media and client-only boundaries

The Bot REST API can expose and this collector can attempt to download:

- files attached to messages, including images, videos, and audio attachments;
- embed `image`, `thumbnail`, and `video` URLs;
- the same media references returned inside pinned messages.

Each download can still fail because a URL expired, the remote host rejected it, the
object exceeded the limit, or observed MIME/size did not match Discord metadata. Such
records remain explicit failures in `asset-records/` and keep an asset-enabled run
partial.

The Bot REST API does **not** expose:

- a user's Discord client **Personal Favorites / sidebar pinned channels**. These are
  different from messages pinned inside a channel. The user's manually supplied list
  is therefore the source for the 132-target snapshot; the bot cannot discover or
  verify that client-side list by itself;
- raw **Go Live / voice-channel screen-share video frames or audio samples**. REST can
  retain chat messages and posted media around a live session, but it cannot fetch
  the live media stream itself;
- a bot/model's private backend reasoning. Messages authored by a bot/model are
  readable only when they were actually posted into an accessible Discord channel.

These non-REST surfaces are also declared in `manifest.json::not_api_exposed`. If
they are required as evidence, they need a separately authorized capture source and
must not be silently presented as Bot API coverage.
