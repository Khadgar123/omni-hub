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
- `usage_timeout_secs`, `usage_max_retries`: balance query timeout and retry knobs. Use these when the usage domain is slower than the model call domain.
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

7. Probe concurrency, request speed, and batch support. This overwrites user-entered guesses for `max_concurrency`, `rps_limit`, and `rpm_limit` with measured values in the 0-10 range. A full probe tests 1..10 concurrent requests plus 1..10 paced RPS levels, so it can send up to 110 minimal model requests:

```bash
curl -sS -X POST http://127.0.0.1:8765/api/channel-capability-probe \
  -H 'Content-Type: application/json' \
  -d '{"account_id":"openai-example","max_concurrency":10,"max_rps":10}'
```

Batch probing is non-mutating: OpenAI-compatible channels check `/v1/batches?limit=1`, Anthropic-native channels check `/v1/messages/batches`. Treat 2xx as supported, 404/405/501 as unsupported, and 401/403/429 as not confirmed.

8. Re-read `/api/state` and report the created `account_id`, model list, secret ref type, health status, latency, balance result, concurrency result, and batch support.

## Project Model Orders

When the user asks to distribute model configuration to a project, do not copy raw channel credentials into that project. Save model names by ability slot and let 万象中枢 resolve the concrete channel from the global model configuration order.

Example:

```bash
curl -sS -X POST http://127.0.0.1:8765/api/project-model-orders \
  -H 'Content-Type: application/json' \
  -d '{
    "project_id": "auto-driving-research",
    "orders": [
      {"slot": "default", "model_ids": ["deepseek-chat", "gpt-5.5-mini"]},
      {"slot": "reasoning", "model_ids": ["gpt-5.5-xhigh", "claude-opus"]},
      {"slot": "code", "model_ids": ["gpt-5.5", "deepseek-chat"]}
    ]
  }'
```

Resolve one slot before handing the bundle to a runtime:

```bash
curl -sS -X POST http://127.0.0.1:8765/api/project-resolve \
  -H 'Content-Type: application/json' \
  -d '{"project_id":"auto-driving-research","slot":"reasoning"}'
```

Resolution order is project model order first, then global channel priority for the same model name. Channels marked down, limited, disabled, or quota-exhausted are skipped. The response may include `base_url`, `secret_ref`, and proxy/rate-limit metadata, but never raw API keys.

## CursorLink Notes

CursorLink should be configured as direct pending entries under OpenAI and Claude, not as a separate template list.

- Model call base URL: `https://apicursor.com/v1`
- OpenAI/Codex aliases: `cx-5.5`, `cx-5.5-high`, `cx-5.5-xhigh`, `cx-5.4`, `cx-5.4-high`, `cx-5.4-xhigh`
- Claude aliases: `op-4.6`, `so-4.6`
- Usage template: `cursorlink`
- Usage base URL: `https://cursorlink.net`
- The short query secret from a URL is not necessarily the model API key. Exchange or verify it only through the local control plane and never store it in git.
- CursorLink model calls use `https://apicursor.com/v1`, but quota calls use `https://cursorlink.net`. If quota lookup times out during TLS handshake, configure the channel `proxy_url` and optionally increase `usage_timeout_secs`.

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
