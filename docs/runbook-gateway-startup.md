# Gateway startup runbook (api-management)

Runtime is **Colima** (pure-CLI Docker on macOS — no Docker Desktop, no GUI,
Apache-2.0, no license tier). The `docker` / `docker compose` CLIs are
unchanged; only the daemon/VM behind them differs.

## Status (verified 2026-05-29 — full chain live)

| Item | State |
|------|-------|
| Runtime | ✅ Colima (2 CPU / 4 GB / 20 GB), docker context `colima` |
| `docker compose` | ✅ v2.39.4 (brew plugin, symlinked into `~/.docker/cli-plugins/`) |
| `.env` (root) — `CCLOAD_PASS`, `METAPI_AUTH_TOKEN`, `METAPI_PROXY_TOKEN`, `CCLOAD_API_TOKENS` | ✅ set (gitignored) |
| `omni-ccload` (`:8080`) | ✅ running, healthy |
| `omni-metapi` (`:4000`) | ✅ running, healthy |
| `api-management-status` → `all_services_reachable` | ✅ True |
| ccLoad channel `deepseek-direct` → api.deepseek.com | ✅ created, enabled |
| **End-to-end LLM call through gateway** | ✅ `POST :8080/v1/chat/completions` → HTTP 200, deepseek-chat replied |

## Daily operations (all CLI, no GUI)

```bash
# start the daemon (first boot ~1-2 min to build the VM; later starts ~10s)
colima start                       # uses the saved 2cpu/4gb/20gb profile

# bring the gateway up (idempotent)
cd ~/Desktop/简历/个人知识库
docker compose --env-file ./.env \
  -f api-management/compose.yml up -d

# verify
docker compose --env-file ./.env -f api-management/compose.yml ps
~/opt/anaconda3/envs/omni-hub/bin/python -m omni_hub.cli api-management-status

# stop the gateway (keep VM) / stop the VM (frees RAM+CPU)
docker compose --env-file ./.env -f api-management/compose.yml down
colima stop
```

> ⚠️ **Use `--env-file ./.env`, NOT `api-management/env.example`.** env.example
> holds `change-me-...` placeholders; the real secrets live in the root `.env`.
> Bringing the gateway up with env.example starts ccLoad with the placeholder
> admin password and login fails.

Endpoints:
- ccLoad admin/proxy: `http://127.0.0.1:8080`  (admin UI `/web/`)
- metapi admin/proxy: `http://127.0.0.1:4000`

Optional — auto-start Colima at login: `brew services start colima`

## First-time gateway config — DONE (2026-05-29)

The `deepseek-direct` channel was created via the admin API (login → POST
`/admin/channels`). To add/replace a channel later, in the admin UI
(`http://127.0.0.1:8080/web/`, password = `CCLOAD_PASS` from `.env`):
1. add a channel → DeepSeek (`https://api.deepseek.com`, type `openai`,
   api_key = the DeepSeek key, models `deepseek-chat`/`deepseek-reasoner`),
   or → metapi (`http://omni-metapi:4000`, bearer = `METAPI_PROXY_TOKEN`);
2. point Claude Code / Codex / the omni-hub harness at ccLoad on `:8080`.

Smoke-test a completion through the gateway (proxy token = first entry of
`CCLOAD_API_TOKENS`):
```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer <CCLOAD_API_TOKENS first token>" \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"ping"}],"max_tokens":5}'
```

## Working without the gateway (direct DeepSeek)

The flywheel's LLM calls (`judge --judge llm`, `app-report-build --narrate`,
`harness-*`) default to ccLoad on `:8080`. To bypass it:

```bash
export OMNI_DEEPSEEK_BASE=https://api.deepseek.com   # uses the secrets.json key
```

Trade-off: no failover, no per-channel cost/RPM limits, no served-channel
provenance header. Fine for single calls; use the gateway for sustained work.

## Light up the flywheel (after the gateway is up)

```bash
PY=~/opt/anaconda3/envs/omni-hub/bin/python
# seed: retrieve -> evidence -> proposal -> claims for a domain
$PY -m omni_hub.cli retrieve --query "<seed query>" --domain ai_progress --persist-evidence
$PY -m omni_hub.cli wiki-ingest --run-id <run_id> --domain ai_progress
$PY -m omni_hub.cli propose-list --state pending
$PY -m omni_hub.cli propose-approve --id <pid>
$PY -m omni_hub.cli wiki-apply-proposal --proposal <pid>
# optional: hybrid vector index over wiki pages (sqlite-vec + RRF)
$PY -m omni_hub.cli wiki-vec-build
$PY -m omni_hub.cli wiki-hybrid-search --query "<query>"
```

## Migration note: Docker Desktop → Colima (DONE 2026-05-29)

Docker Desktop was uninstalled (`/Applications/Docker.app/Contents/MacOS/uninstall`
+ removed app bundle + `~/Library/...` support dirs). `docker` now resolves to
`/opt/homebrew/bin/docker` (brew). **The Desktop uninstall removed the bundled
`docker compose` plugin**, so it was re-wired to the brew one:

```bash
# brew's compose plugin -> the default dir docker scans
mkdir -p ~/.docker/cli-plugins
ln -sf /opt/homebrew/lib/docker/cli-plugins/docker-compose \
       ~/.docker/cli-plugins/docker-compose
# and prune Desktop leftovers from ~/.docker/config.json:
#   removed "credsStore": "desktop" (helper is gone) + Desktop-only plugins/features
hash -r
docker compose version   # -> v2.39.4
```

Residual (harmless, needs sudo to remove): a dangling root-owned symlink
`/usr/local/bin/com.docker.cli`. Remove with
`sudo rm /usr/local/bin/com.docker.cli` if desired.
