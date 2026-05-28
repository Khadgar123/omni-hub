---
name: api-management-status
description: |
  Inspect the local API management stack of the omni-hub repository.
  Use this skill whenever the user asks about: ccLoad health, Metapi balance,
  channel cooldown, DeepSeek default routing, why a model request failed,
  whether 4000 / 8080 ports are up, or wants to see the merged status report
  for both metapi and ccLoad. Trigger when the user mentions "网关"、"渠道"、
  "余额"、"cooldown"、"metapi"、"ccLoad"、"api-management"、"DeepSeek key".
  Do NOT trigger for generic Python / Go / TypeScript debugging.
license: MIT
---

# API Management Status

This is a **project-level skill** owned by `omni-hub`. It tells the agent how to
read the current state of the local API management stack without guessing.

## When to use

Trigger when the user asks any of:

- "ccLoad 还好吗 / 还活着吗 / 起来了没"
- "metapi 余额"、"上游账号余额"、"低余额告警"
- "渠道 X 是不是 cooldown 了 / 还要等多久"
- "DeepSeek 默认 key 配好了吗 / 哪里找"
- "为什么 Codex / Claude Code / Cursor 调用 4000 / 8080 失败"
- 对 `api-management-status` CLI 的任何提及

## What to do

1. Always run the canonical status command **first** before reading any source:

   ```bash
   PYTHONPATH=src python3.12 -m omni_hub.cli api-management-status
   ```

2. If that fails, decide which side is broken and read **only** the relevant
   README before suggesting fixes:

   - Metapi side → `api-management/metapi/README.md` + `metapi/AGENTS.md`
   - ccLoad side → `api-management/ccLoad/README.md`
   - Compose / orchestration → `api-management/README.md`

3. For real failures, surface the **exact** docker compose command from
   `api-management/README.md` rather than inventing one. The canonical
   image-based form is:

   ```bash
   docker compose --env-file api-management/env.example \
     -f api-management/compose.yml up -d
   ```

4. **Never** suggest writing API keys into `.env`, README, tests, or compose
   files. The only allowed location is the local secret backend:
   `local:omni-hub/api/deepseek/default`.

## Hard rules

- Do not recreate the deleted Provider Router / GUI in `src/omni_hub/`.
  Gateway changes belong to `api-management/metapi` or `api-management/ccLoad`.
- Do not modify `api-management/defaults.json` to change default provider
  without an explicit user instruction — it is the project's single source of
  truth for the default model.
- Read-only by default. Only run write commands (compose up, key store) when
  the user has explicitly confirmed in chat.

## Expected output format

A short status line per service:

```
metapi (4000): UP   | upstream sites: 3  | low-balance alerts: 0
ccLoad (8080): UP   | active channels: 5 | cooldown: 0  | RPM headroom: ok
defaults:       deepseek / deepseek-v4-pro  (secret_ref present)
```

Then a one-sentence next action if anything is RED.

## Eval cases (for skill-creator)

1. Everything healthy → returns the status block, no fix suggestions.
2. ccLoad up, metapi down → suggests reading `api-management/metapi/README.md`
   and running compose with the build override.
3. DeepSeek `secret_ref` missing → outputs the exact key-store snippet from
   `api-management/README.md` (does NOT print the key itself).
4. All channels cooldown → explains exponential backoff (2→4→8→30 min) from
   ccLoad README and waits, does not force-reset.
5. User asks to add a new upstream → routes the change to `metapi`, never to
   the main repo.
