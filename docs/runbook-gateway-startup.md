# Gateway startup runbook (api-management)

Runtime is **Colima** (pure-CLI Docker on macOS — no Docker Desktop, no GUI,
Apache-2.0, no license tier). The `docker` / `docker compose` CLIs are
unchanged; only the daemon/VM behind them differs.

## Status (verified 2026-05-29 — gateway brought up live)

| Item | State |
|------|-------|
| Runtime | ✅ Colima (2 CPU / 4 GB / 20 GB), docker context `colima` |
| `.env` (root) — `CCLOAD_PASS`, `METAPI_AUTH_TOKEN`, `METAPI_PROXY_TOKEN`, `CCLOAD_API_TOKENS` | ✅ set (gitignored) |
| DeepSeek key (`local:omni-hub/api/deepseek/default`) | ✅ set |
| `omni-ccload` (`:8080`) | ✅ running, healthy (302 → admin UI) |
| `omni-metapi` (`:4000`) | ✅ running, healthy (HTTP 200) |
| `api-management-status` → `all_services_reachable` | ✅ True |

## Daily operations (all CLI, no GUI)

```bash
# start the daemon (first boot ~1-2 min to build the VM; later starts ~10s)
colima start                       # uses the saved 2cpu/4gb/20gb profile

# bring the gateway up (idempotent)
cd ~/Desktop/简历/个人知识库
docker compose --env-file api-management/env.example \
  -f api-management/compose.yml up -d

# verify
docker compose -f api-management/compose.yml ps
~/opt/anaconda3/envs/omni-hub/bin/python -m omni_hub.cli api-management-status

# stop the gateway (keep VM) / stop the VM (frees RAM+CPU)
docker compose -f api-management/compose.yml down
colima stop
```

Endpoints:
- ccLoad admin/proxy: `http://127.0.0.1:8080`  (admin UI `/web/`)
- metapi admin/proxy: `http://127.0.0.1:4000`

Optional — auto-start Colima at login:
```bash
brew services start colima
```

## First-time gateway config (one-off)

ccLoad is up but has no upstream channel yet. In the admin UI
(`http://127.0.0.1:8080/web/`, password = `CCLOAD_PASS` from `.env`):
1. add a channel pointing at DeepSeek (`https://api.deepseek.com`), or at
   metapi (`http://omni-metapi:4000`, bearer = `METAPI_PROXY_TOKEN`);
2. point Claude Code / Codex / the omni-hub harness at ccLoad on `:8080`.

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

## Migration note: Docker Desktop → Colima

Done 2026-05-29. `docker` resolves to `/opt/homebrew/bin/docker` (brew,
earlier on PATH than Desktop's `/usr/local/bin/docker`), so the CLI survives
removing Desktop. To uninstall Docker Desktop (frees ~2 GB + stops its
background helpers):

```bash
/Applications/Docker.app/Contents/MacOS/uninstall   # official uninstaller
# then drag Docker.app to Trash, and optionally:
rm -rf ~/Library/Containers/com.docker.docker \
       ~/Library/Application\ Support/Docker\ Desktop
hash -r   # refresh shell so `docker` -> the brew binary
```
