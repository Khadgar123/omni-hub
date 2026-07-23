# Closed-Loop Runbook — 2026-05-30

Operational guide to run the (now code-complete) knowledge→productivity closed
loop: **acquire → dedup → ingest → consume → evolve**.  Companion to
[review-2026-05-29-architecture-deep-dive.md](review-2026-05-29-architecture-deep-dive.md).

## Pipeline (modules / commands)

```
ACQUIRE          cascade retrieve (50 connectors, full-payload, fail-soft+retry)
                 + scripts/crawl_accepted.py   (conference accepted lists, config/venues.yaml)
   │
DEDUP            retrieval/paper_identity.merge_papers  (arXiv preprint ↔ accepted DOI ↔ OpenReview)
   │             — runs intra-run at wiki-ingest (paper domains); cross-source fold
DOWNLOAD         scripts/download_pdfs.py  (resumable, rate-limited, incremental → vault/raw/papers/)
   │
DEEP-PARSE       ResearchFlow / MinerU (+ GROBID for authors/affil/refs)   [submodule, on-demand]
   │
INGEST           wiki-ingest → Proposal(wiki_update) → propose-approve → wiki-apply-proposal
   │             → .omni/claims.jsonl (bitemporal) + vault/wiki pages + FTS5 reindex
CONSUME          context-pack-build(domain) → functional skills (report/pptx/order/chat-route)
   │             — R3 grounding wired (finance_screen/pptx_build/app_route_task/_multi)
EVOLVE           PreferenceStore (accept/reject) → harness-compile-skill → Proposal(skill_update)
                 → GEPA/DSPy (needs accumulated preference data)
```

## Run sequence (on a real machine with network)

```bash
PY=/Users/hzh/opt/anaconda3/envs/omni-hub/bin/python   # or conda env

# 0. Secrets + gateway (LLM synth/judge/compile route through ccLoad)
$PY -m omni_hub.secrets            # store api/deepseek/default ; optional: github/s2/data.gov/openalex-mailto
docker compose --env-file api-management/env.example -f api-management/compose.yml up -d
PYTHONPATH=src $PY -m omni_hub.cli api-management-status
PYTHONPATH=src $PY -m omni_hub.cli wiki-init

# 1. Conference accepted-paper corpus (top-AI; dedup against arXiv built in)
$PY scripts/crawl_accepted.py --all --limit 4000        # accepted lists → .omni/accepted/accepted_index.jsonl
$PY scripts/download_pdfs.py --dry-run                  # exact count + ~GB
$PY scripts/download_pdfs.py --max-gb 130               # resumable; re-run = incremental (only new)

# 2. Broad/timely acquisition (entities + domains)
$PY scripts/daily_follow_brief.py                       # 41 entities × X/GitHub/HF/RSS/... (config/entity-watchlist.yaml)
PYTHONPATH=src $PY -m omni_hub.cli retrieve --domain ai_progress --query "<seed>" --persist-evidence

# 3. Sediment into knowledge (the human-review gate)
PYTHONPATH=src $PY -m omni_hub.cli wiki-ingest --run-id <id> --domain ai_progress   # → Proposal
PYTHONPATH=src $PY -m omni_hub.cli propose-list --kind wiki_update --state pending
PYTHONPATH=src $PY -m omni_hub.cli propose-approve --id <pid>
PYTHONPATH=src $PY -m omni_hub.cli wiki-apply-proposal --proposal <pid>   # claims + PreferenceRecord + FTS

# 4. Consume + maintain
PYTHONPATH=src $PY -m omni_hub.cli context-pack-build --query "..." --domain ai_progress
PYTHONPATH=src $PY -m omni_hub.cli app-report-build --period weekly --persist
PYTHONPATH=src $PY -m omni_hub.cli wiki-lint --persist ; wiki-dream

# 5. Schedule (launchd: daily / daily-follow / weekly / monthly / worker)
make schedule-install
```

## Config / keys checklist

| Need | For |
|---|---|
| DeepSeek key (`local:omni-hub/api/deepseek/default`) + ccLoad/Metapi up | LLM synth / judge / compile / narrate (else heuristic fallback) |
| `OPENALEX_MAILTO`, `SEMANTIC_SCHOLAR_API_KEY`, `GITHUB_TOKEN`, `DATA_GOV_API_KEY` | polite pools / higher rate limits (all optional; connectors fail-soft) |
| `TWITTERAPI_IO` key, `we-mp-rss` (公众号), `xiaohongshu-cli` (小红书) brokers | the X / WeChat / XHS follow sources to return real data |
| MinerU (GPU for Pro tier) + GROBID (Docker `lfoppiano/grobid`) | PDF deep-parse: body/formula/table/figure (MinerU) + authors/affil/refs (GROBID) |

## Storage budget (2024–2026)

- **Metadata index** (titles/authors/abstracts/links/acceptance, no PDF): ~2 GB (top-AI) → ~6–8 GB (all major CS).
- **+ full PDFs** (~2 MB/paper): top-AI ~65k ≈ **~130 GB**; don't bulk-pull all CS (~1.4 TB) — fetch on-demand.
- `download_pdfs.py --dry-run` gives the exact figure from the real crawl.

## Incremental updates (steady state)

Re-run `crawl_accepted.py` + `download_pdfs.py` (skips existing → only new accepted papers) + `daily_follow_brief.py` (scheduled) → `wiki-ingest` the new runs.  `paper_identity.merge_papers` folds re-crawled papers; the bitemporal ledger supersedes stale claims.

## Known-remaining (NOT autonomous-codeable here)

1. **Reservoir is empty** — run §3 + approve proposals to populate `claims.jsonl` / PreferenceStore. GEPA produces non-trivial output only after weeks of accept/reject data.
2. **Cross-run paper UPDATE** — intra-run dedup is wired; "accepted version arriving in a *later* run updates the existing arXiv claim (set venue/accepted, supersede preprint)" is the next ingest increment.
3. **ResearchFlow/BITE** — bump submodule pin `1e6a9ed`→`145986a` (BITE rebrand + HF evidence layer; dirty `paper_list.csv` first); add the GROBID pass upstream.
4. **OpenAlex `venue_source_id`** in `config/venues.yaml` — fill for precise non-OpenReview venue crawl (now name-search fallback).
5. **Quant (betafish session)** — `tests/test_market_store.py` + `tests/test_binance_market_data.py` import a moved `agent-harness/quant/quant/market_store.py`; relocate them under `agent-harness/quant/tests/` (run by quant's own pytest) to restore `make test` = 0 failures in the main stdlib-only suite.
