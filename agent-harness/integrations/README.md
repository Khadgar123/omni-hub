# agent-harness/integrations/

Interface-Plane channel adapters (v0.24+).  Each integration is a
**broker** that exposes one of two pluggable surfaces:

1. **CLI on PATH** — e.g. `feishu listen --json` / `feishu reply ...`,
   invoked via subprocess from the channel stub in
   `src/omni_hub/channels/external_stubs.py`.
2. **Self-hosted HTTP service** — RSS / REST endpoint omni-hub talks to
   via stdlib `urllib`, no SDK linked into the main repo.

Either way, the main repo stays 100% Python stdlib.  Real SDKs
(`lark-oapi`, `discord.py`, `slack-bolt`, etc.) live here as pinned
forks under `agent-harness/integrations/<channel>/`.

| Integration | Status     | Broker contract                                 |
|-------------|-----------|-------------------------------------------------|
| `feishu`    | scaffolded | `feishu listen|reply|status --json` CLI         |
| `discord`   | scaffolded | `discord listen|reply|status --json` CLI        |
| `email`     | n/a        | stdlib imaplib/smtplib — no broker needed       |
| `zhihu`     | scaffolded | `zhihu search --json` CLI                       |
| `weibo`     | scaffolded | `weibo search --json` CLI                       |
| `linkedin`  | scaffolded | `linkedin search --json` CLI                    |

The CLI contract is intentionally narrow so we can swap implementations
(direct SDK vs. third-party broker) without touching the main repo's
Channel Protocol.  See `src/omni_hub/channels/external_stubs.py` for
the consuming side.
