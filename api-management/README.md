# API Management

This directory is the local API management and gateway path.

It is intentionally split into two maintained forks:

- `metapi`: upstream account, balance, model discovery, check-in, token, and cost-aware routing management for New API / One API / OneHub / Sub2API style relays.
- `ccLoad`: local runtime gateway for Claude Code, Codex, Gemini, and OpenAI-compatible clients, with protocol transforms, failover, token limits, cost limits, RPM limits, and monitoring.

The old local Python provider router, GUI, and agent planning layer have been removed from the main repository. Keep API gateway changes inside these two forks.

## Engineering Model

This repository uses a product-orchestrator plus pinned service forks model:

- `omni-hub` owns product configuration, docs, compose files, status checks, tests, and release pointers.
- `metapi` and `ccLoad` stay as independent forked services with their own upstreams, history, tests, and build systems.
- The main repository pins exact service commits through gitlinks, so every checkout is reproducible.
- Contributors should use the root `Makefile` instead of remembering submodule commands.

Bootstrap after clone:

```bash
make setup
```

Update the maintained forks from upstream:

```bash
make api-update
make test
make compose-config
git commit -m "Update API management forks"
```

If an upstream merge is not fast-forward, resolve it inside the fork repository, push the fork branch, then commit the bumped gitlink in `omni-hub`.

## Forks

```text
api-management/metapi  -> https://github.com/Khadgar123/metapi
api-management/ccLoad  -> https://github.com/Khadgar123/ccLoad
```

`make setup` adds each fork's `upstream` remote automatically. Manual equivalent:

```bash
cd api-management/metapi
git fetch upstream
git merge upstream/main

cd ../ccLoad
git fetch upstream
git merge upstream/master
```

## Local Run

Use the image-based compose file for first validation:

```bash
cp api-management/env.example .env
docker compose -f api-management/compose.yml up -d
```

Services:

```text
Metapi admin/proxy: http://127.0.0.1:4000
ccLoad admin:        http://127.0.0.1:8080/web/
ccLoad proxy:        http://127.0.0.1:8080
```

Use the build override when testing changes made inside the forks:

```bash
docker compose -f api-management/compose.yml -f api-management/compose.build.yml up -d --build
```

Runtime data is stored under `.omni/api-management/`, which is gitignored.

## Current Default

All projects currently default to DeepSeek through `api-management/defaults.json`:

```text
provider:   deepseek
model:      deepseek-v4-pro
base_url:   https://api.deepseek.com
anthropic:  https://api.deepseek.com/anthropic
secret_ref: local:omni-hub/api/deepseek/default
```

Store the real key only in the local secret backend:

```bash
PYTHONPATH=src python3.12 - <<'PY'
from omni_hub.secrets import store_api_key
print(store_api_key("api/deepseek/default", input().strip()))
PY
```

Then paste the key at the prompt. Do not put it in `.env`, docs, tests, or compose files.

For Claude Code direct integration, use the Anthropic-compatible endpoint and models from `defaults.json`:

```bash
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-v4-pro[1m]
ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-pro[1m]
ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-pro[1m]
ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-flash
CLAUDE_CODE_SUBAGENT_MODEL=deepseek-v4-flash
```

## Runtime Topology

```text
Claude Code / Codex / Gemini / OpenAI clients
  -> ccLoad on 127.0.0.1:8080
  -> Metapi on 127.0.0.1:4000 when the selected ccLoad channel points to Metapi
  -> New API / One API / OneHub / Sub2API / official compatible upstreams
```

Recommended first setup:

1. Add upstream accounts and sites in Metapi.
2. Confirm Metapi exposes the merged model list and proxy endpoint.
3. Add Metapi as one ccLoad channel, using `METAPI_PROXY_TOKEN` as the upstream bearer token.
4. Point Claude Code, Codex, Gemini, or OpenAI-compatible clients to ccLoad.

## Ownership Boundary

Metapi should own:

- upstream site/account/token registration
- upstream model discovery
- balance refresh and low-balance alerts
- cost and usage dashboards
- upstream route selection by cost, balance, and usage

ccLoad should own:

- Claude Code, Codex, Gemini, and OpenAI protocol endpoints
- protocol conversion
- channel/key failover and cooldown
- per-token model allowlists
- per-token cost ceilings
- per-channel RPM and daily cost limits
- request logs and live monitoring

Main `omni-hub` should own only:

- this compose entrypoint
- fork pointers and update notes
- `api-management-status`
- tests that validate the local layout without storing real credentials
