# Discord Channel Integration

**Status**: scaffolded (v0.24).  No SDK code committed yet.

## Broker contract

The omni-hub channel stub at `src/omni_hub/channels/external_stubs.py::DiscordChannel`
expects a `discord` binary on `PATH` (or `OMNI_DISCORD_BIN` env var)
implementing three subcommands:

```bash
discord status --json
# → {"logged_in": true, "bot_id": "...", "guilds": [...]}

discord listen --json [--mention-only] [--guild <id>] [--channel <id>]
# → emits one JSON object per inbound message, newline-delimited:
# {
#   "trace_id":   "discord-<uuid8>",
#   "sender":     "<user_id>",
#   "subject":    "<thread name | empty>",
#   "body":       "<markdown>",
#   "metadata":   {"message_id": "...", "guild_id": "...", "channel_id": "..."}
# }

discord reply --to <user_id> --in-reply-to <message_id> --body <markdown> --trace <trace_id>
# Sends a reply.  --in-reply-to MUST resolve to either a DM thread or
# a channel/message permalink so the bot replies in the right context.
```

## Implementation options (BUILD-vs-USE)

| Option | Maturity | License | Decision |
|--------|---------|---------|----------|
| **discord.py** (Rapptz) | active | MIT | preferred — wrap in CLI shim |
| **disnake** | community fork | MIT | fallback |
| **discord-interactions** (raw REST) | tiny | MIT | for one-off probes only |

Like `feishu`, the shim is ~200 LOC.  Live under
`discord/cli/discord_omni.py` with `pipx`-friendly `pyproject.toml`.
(``discord`` collides with the SDK module name on PATH — rename the
binary to ``discord-omni`` and update the channel stub
``binary`` attribute.)

## Auth

Discord bot tokens go in `~/.config/omni-hub/discord.toml`:

```toml
bot_token = "MTQ4..."
default_guild = "..."
```

**Never** commit.  Token rotation is via the Discord developer portal.

## Intent privileges

Pre-2026 Discord requires explicit "Message Content Intent" approval
for any bot wanting to read non-mention messages.  Default the broker
to `--mention-only` mode to stay within the unprivileged intent set;
operators with approval can flip the flag.

## TODO

1. `git submodule add https://github.com/Rapptz/discord.py.git \
    agent-harness/integrations/discord/discord.py`
2. Implement `discord/cli/discord_omni.py` honouring the broker contract.
3. `pipx install -e agent-harness/integrations/discord/cli`
4. `omni-hub channel-health --name discord` → `ok`
