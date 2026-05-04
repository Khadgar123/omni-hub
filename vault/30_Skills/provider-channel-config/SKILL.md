# Provider Channel Config Skill

## Purpose

Use this skill when Codex, Claude Code, CC, or another local agent needs to add, update, duplicate, test, or document a model API channel for 万象中枢. The goal is to create concrete runnable channel entries, not abstract templates.

The local GUI/API is the source of truth:

```text
http://127.0.0.1:8765
```

Do not write raw API keys into git, docs, tests, issue comments, project bundles, or external client config files.

## Required Inputs

Collect these fields before writing a channel. If a field is unknown, leave it empty and record the uncertainty in `notes`.

- `provider`: official model family such as `openai`, `claude`, `qwen`, `deepseek`, `kimi`, `glm`, or `minimax`.
- `account_id`: stable local id, for example `openai-cursorlink` or `claude-official`.
- `name`: human readable channel name.
- `base_url`: model calling endpoint, usually OpenAI-compatible `/v1` or the vendor native endpoint.
- `api_key` or `secret_ref`: raw key only in the local POST body; otherwise use `env:`, `local:`, `keychain:`, or `runtime:`.
- `model_ids`: newline separated available model names or aliases.
- `default_model`: the preferred first model for this channel.
- `proxy_url`: optional. Empty means unset proxy for this channel.
- `api_format`: `openai_chat`, `openai_responses`, `anthropic`, or vendor-specific value when needed.
- `wire_api`: Codex-facing protocol such as `responses` or `chat`.
- `requires_openai_auth`, `disable_response_storage`, `model_reasoning_effort`: Codex export fields.
- `usage_template`, `usage_base_url`, `usage_endpoint`, `usage_access_token_ref`: balance and quota query configuration.
- `max_concurrency`, `rpm_limit`, `tpm_limit`, `batch_support`: measured or vendor-published runtime limits.
- `cost_multiplier`, `pricing_model_source`: pricing source and correction factor.

## Workflow

1. Read existing state:

```bash
curl -sS http://127.0.0.1:8765/api/state
```

2. If the user provides a vendor page or query page, inspect it for model calling URL, model aliases, balance endpoint, key exchange rules, quota fields, rate limits, batch support, and billing units. Record high-risk finance/admin endpoints, but do not call them without human approval.

3. Create or update a concrete channel entry:

```bash
curl -sS -X POST http://127.0.0.1:8765/api/official-provider-config \
  -H 'Content-Type: application/json' \
  -d '{
    "provider": "openai",
    "account_id": "openai-example",
    "name": "OpenAI 中转 · Example",
    "base_url": "https://api.example.com/v1",
    "api_key": "<raw key only in this local request>",
    "model_ids": "gpt-5.5\ngpt-5.5-high",
    "default_model": "gpt-5.5",
    "usage_template": "generic",
    "usage_endpoint": "/v1/usage",
    "priority": 90
  }'
```

4. Discover models when the provider supports it:

```bash
curl -sS -X POST http://127.0.0.1:8765/api/model-fetch \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"openai-example"}'
```

5. Probe model connectivity and latency. This sends a minimal real request and may cost a tiny amount:

```bash
curl -sS -X POST http://127.0.0.1:8765/api/model-probe \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"openai-example"}'
```

6. Refresh balance or quota:

```bash
curl -sS -X POST http://127.0.0.1:8765/api/balance-check \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"openai-example"}'
```

7. Probe concurrency, request speed, and batch support. This overwrites user-entered guesses for `max_concurrency`, `rps_limit`, and `rpm_limit` with measured values in the 0-10 range:

```bash
curl -sS -X POST http://127.0.0.1:8765/api/channel-capability-probe \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"openai-example","max_concurrency":10,"max_rps":10}'
```

8. Re-read `/api/state` and report the created `account_id`, model list, secret ref type, health status, latency, balance result, concurrency result, and batch support.

## CursorLink Notes

CursorLink should be configured as direct pending entries under OpenAI and Claude, not as a separate template list.

- Model call base URL: `https://apicursor.com/v1`
- OpenAI/Codex aliases: `cx-5.5`, `cx-5.5-high`, `cx-5.5-xhigh`, `cx-5.4`, `cx-5.4-high`, `cx-5.4-xhigh`
- Claude aliases: `op-4.6`, `so-4.6`
- Usage template: `cursorlink`
- Usage base URL: `https://cursorlink.net`
- The short query secret from a URL is not necessarily the model API key. Exchange or verify it only through the local control plane and never store it in git.

## Output Contract

After using this skill, return a short result with:

- changed `account_id`
- provider and channel name
- stored `secret_ref` type, never the raw key
- models and default model
- balance or quota status
- model probe status and latency
- concurrency and batch support status
- files changed, if any docs were updated
