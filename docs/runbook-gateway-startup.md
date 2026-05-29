# Gateway startup runbook (api-management)

Runtime is **Colima** (pure-CLI Docker on macOS — no Docker Desktop, no GUI,
Apache-2.0, no license tier). The `docker` / `docker compose` CLIs are
unchanged; only the daemon/VM behind them differs.

## Status (verified 2026-05-29 — full LLM path live)

| Item | State |
|------|-------|
| Runtime | ✅ Colima (2 CPU / 4 GB / 20 GB), docker context `colima` |
| `docker compose` | ✅ v5.1.4 (brew plugin, symlinked into `~/.docker/cli-plugins/`) |
| `.env` (root) — `CCLOAD_PASS`, `METAPI_AUTH_TOKEN`, `METAPI_PROXY_TOKEN`, `CCLOAD_API_TOKENS` | ✅ set (gitignored) |
| `omni-ccload` (`:8080`) / `omni-metapi` (`:4000`) | ✅ running, healthy |
| `api-management-status` → `all_services_reachable` | ✅ True |
| ccLoad channel `deepseek-direct` → api.deepseek.com (id 1, enabled, key valid) | ✅ created |
| ccLoad proxy auth-token `omni-hub` (id 1, enabled) | ✅ created |
| **End-to-end LLM**: `POST :8080/v1/chat/completions` (deepseek-chat) | ✅ HTTP 200, replied "OK" |

## Daily operations (all CLI, no GUI)

```bash
colima start                       # first boot ~1-2 min; later ~10s
cd ~/Desktop/简历/个人知识库
docker compose --env-file ./.env -f api-management/compose.yml up -d
docker compose --env-file ./.env -f api-management/compose.yml ps
~/opt/anaconda3/envs/omni-hub/bin/python -m omni_hub.cli api-management-status
# teardown:
docker compose --env-file ./.env -f api-management/compose.yml down
colima stop
```

> ⚠️ **Use `--env-file ./.env`, NOT `api-management/env.example`.** env.example
> holds `change-me-...` placeholders; the real secrets are in the root `.env`.
> Starting with env.example gives ccLoad the placeholder admin password and
> login fails.

Endpoints: ccLoad `http://127.0.0.1:8080` (admin `/web/`), metapi `http://127.0.0.1:4000`.
Auto-start at login: `brew services start colima`.

## Bootstrap (one-time, DONE 2026-05-29)

ccLoad's SQLite volume (`.omni/api-management/ccload/`) persists across
container recreate. On a **fresh** volume `CCLOAD_API_TOKENS` seeds the proxy
token automatically; if the volume already exists (or the first run used
env.example), it does **not** — so both the channel and the proxy token were
created via the admin API. Reproduce on a clean volume with:

```bash
PY=~/opt/anaconda3/envs/omni-hub/bin/python
PASS=$(grep '^CCLOAD_PASS=' .env | cut -d= -f2-)
PROXY=$(grep '^CCLOAD_API_TOKENS=' .env | cut -d= -f2- | cut -d'|' -f1)
DSKEY=$($PY -c "import sys;sys.path.insert(0,'src');from omni_hub.secrets import resolve_secret_ref as r;print(r('local:omni-hub/api/deepseek/default'))")

# 1) login -> token is at data.token (NOT top-level)
TOK=$(curl -s -X POST localhost:8080/login -H 'Content-Type: application/json' \
  -d "{\"password\":\"$PASS\"}" | $PY -c "import sys,json;print(json.load(sys.stdin)['data']['token'])")

# 2) create the upstream channel (DeepSeek)
curl -s -X POST localhost:8080/admin/channels -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' -d "{\"name\":\"deepseek-direct\",\
\"url\":\"https://api.deepseek.com\",\"api_key\":\"$DSKEY\",\"priority\":100,\
\"enabled\":true,\"channel_type\":\"openai\",\
\"models\":[{\"model\":\"deepseek-chat\"},{\"model\":\"deepseek-reasoner\"}]}"

# 3) register the proxy auth-token (clients send this as the bearer)
curl -s -X POST localhost:8080/admin/auth-tokens -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' -d "{\"token\":\"$PROXY\",\"name\":\"omni-hub\",\"enabled\":true}"

# 4) (if a channel got cooled by earlier failed probes) clear cooldowns
curl -s -X POST localhost:8080/admin/cooldowns/clear -H "Authorization: Bearer $TOK" -d '{}'

# 5) smoke test the full proxy path
curl -s localhost:8080/v1/chat/completions -H "Authorization: Bearer $PROXY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"ping"}],"max_tokens":5}'
```

ccLoad API notes (verified): login is `POST /login {"password":...}` →
`{"success":true,"data":{"token":...}}` (token nested under `data`, used as
`Authorization: Bearer`, stored client-side in localStorage — not a cookie).
Admin login has a 5-attempt/IP rate-limiter (HTTP 429), reset on success.

## Working without the gateway (direct DeepSeek)

```bash
export OMNI_DEEPSEEK_BASE=https://api.deepseek.com   # uses the secrets.json key
```
Trade-off: no failover / cost-RPM limits / served-channel provenance. Fine for
single calls; use the gateway for sustained work.

## Light up the flywheel (gateway up)

```bash
PY=~/opt/anaconda3/envs/omni-hub/bin/python
$PY -m omni_hub.cli retrieve --query "<seed query>" --domain ai_progress --persist-evidence
$PY -m omni_hub.cli wiki-ingest --run-id <run_id> --domain ai_progress
$PY -m omni_hub.cli propose-list --state pending
$PY -m omni_hub.cli propose-approve --id <pid>
$PY -m omni_hub.cli wiki-apply-proposal --proposal <pid>
$PY -m omni_hub.cli wiki-vec-build        # optional hybrid index
$PY -m omni_hub.cli wiki-hybrid-search --query "<query>"
```

## Migration note: Docker Desktop → Colima (DONE 2026-05-29)

Desktop uninstalled (`/Applications/Docker.app/Contents/MacOS/uninstall` +
removed app bundle + `~/Library/...` support dirs). `docker` now → brew's
`/opt/homebrew/bin/docker`. **The uninstall removed Desktop's bundled
`docker compose` plugin**, re-wired to brew's:

```bash
mkdir -p ~/.docker/cli-plugins
ln -sf /opt/homebrew/lib/docker/cli-plugins/docker-compose \
       ~/.docker/cli-plugins/docker-compose
# also pruned ~/.docker/config.json: removed "credsStore":"desktop" + Desktop plugins
hash -r && docker compose version   # -> v5.1.4
```

Residual (harmless): dangling root-owned `/usr/local/bin/com.docker.cli` —
`sudo rm /usr/local/bin/com.docker.cli` to clean.
