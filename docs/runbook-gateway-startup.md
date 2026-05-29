# Gateway startup runbook (api-management)

Everything below is verified ready **except** the Docker daemon, which must be
started manually (it's a desktop app). Once it's running, bringing the gateway
up is a single command.

## Status (verified 2026-05-29)

| Item | State |
|------|-------|
| `.env` (root) — `CCLOAD_PASS`, `METAPI_AUTH_TOKEN`, `METAPI_PROXY_TOKEN`, `CCLOAD_API_TOKENS` | ✅ set (gitignored) |
| `docker compose ... config` | ✅ validates (exit 0) |
| DeepSeek key (`local:omni-hub/api/deepseek/default`) | ✅ set |
| Docker daemon | ⚠️ **down — start Docker Desktop** |

## Start (after launching Docker Desktop)

```bash
# 1. start the daemon: open Docker Desktop (GUI), wait for it to report Running
open -a Docker            # then wait ~30s

# 2. bring the gateway up (image-based; first run pulls images)
docker compose --env-file api-management/env.example \
  -f api-management/compose.yml up -d

# 3. verify
docker compose -f api-management/compose.yml ps
~/opt/anaconda3/envs/omni-hub/bin/python -m omni_hub.cli api-management-status
```

Services land on:
- ccLoad admin/proxy: `http://127.0.0.1:8080`  (admin UI `/web/`)
- metapi admin/proxy: `http://127.0.0.1:4000`

## Working without Docker (LLM features now)

The flywheel's LLM calls (`judge --judge llm`, `app-report-build --narrate`,
`harness-*`) default to the ccLoad gateway on `:8080`. To run them **without**
Docker, point the harness at DeepSeek directly using the already-stored key:

```bash
export OMNI_DEEPSEEK_BASE=https://api.deepseek.com   # bypass ccLoad
# the key in .omni/secrets.json is used as the bearer
```

Trade-off vs the gateway: no failover, no per-channel cost/RPM limits, no
served-channel provenance header. Fine for local dev / single calls; use the
gateway for sustained or cost-capped work.

## After the gateway is up — light up the flywheel

```bash
PY=~/opt/anaconda3/envs/omni-hub/bin/python
# seed retrieval -> evidence -> proposal -> claims for a domain
$PY -m omni_hub.cli retrieve --query "<seed query>" --domain ai_progress --persist-evidence
$PY -m omni_hub.cli wiki-ingest --run-id <run_id> --domain ai_progress
$PY -m omni_hub.cli propose-list --state pending
$PY -m omni_hub.cli propose-approve --id <pid>
$PY -m omni_hub.cli wiki-apply-proposal --proposal <pid>
# optional: build the hybrid vector index over wiki pages
$PY -m omni_hub.cli wiki-vec-build
$PY -m omni_hub.cli wiki-hybrid-search --query "<query>"
```
