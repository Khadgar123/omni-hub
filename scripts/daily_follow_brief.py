#!/usr/bin/env python3
"""Daily multi-entity follow brief — entity_watchlist-driven + BGE rerank.

Replaces the v0.42 per-script subprocess approach with:

1. Read ``config/entity-watchlist.yaml`` for the canonical entity list.
2. Per entity: call ``follow_entity._gather_one()`` in-process.
3. Per entity: BGE-rerank the aggregated records (query = entity display
   name), so top items reflect cross-encoder relevance, not just source.
4. Write consolidated markdown to ``.omni/briefs/follow-YYYY-MM-DD.md``.

Designed for launchd ``com.omni-hub.daily-follow`` (daily 08:30).

Override defaults via env::

    DAILY_FOLLOW_PEOPLE=karpathy,altman,trump  \\
    DAILY_FOLLOW_COMPANIES=anthropic,openai     \\
    DAILY_FOLLOW_DISABLE_BGE=1                  \\  # skip rerank if you want speed
        python3 scripts/daily_follow_brief.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path


# Default watchlist subsets — keyed against config/entity-watchlist.yaml
DEFAULT_PEOPLE = [
    "karpathy", "altman", "dario", "huang", "musk",
    "dwarkesh", "simonw",
]
DEFAULT_COMPANIES = [
    "anthropic", "openai", "deepmind", "xai",
    "nvidia", "deepseek",
]
DEFAULT_PER_ENTITY_SOURCES = "rss,hn,openalex,tavily,edgar"
DEFAULT_PER_ENTITY_LIMIT = 4
DEFAULT_RERANK_TOP_K = 5                                          # post-BGE keep N


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _split_env(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return [s.strip() for s in raw.split(",") if s.strip()]


def _load_entity(entity_id: str) -> dict:
    """Pull one entity from the watchlist YAML."""

    import yaml
    wl_path = _repo_root() / "config" / "entity-watchlist.yaml"
    with wl_path.open(encoding="utf-8") as f:
        wl = yaml.safe_load(f)
    for bucket in ("people", "companies", "institutions", "topics"):
        node = (wl.get(bucket) or {}).get(entity_id)
        if node:
            node["_bucket"] = bucket
            return node
    return {}


def _rerank_records(records: list[dict], query: str, top_k: int) -> list[dict]:
    """BGE-rerank a list of follow records.  Falls back to identity sort
    when BGE isn't available (no FlagEmbedding install)."""

    if not records:
        return []
    try:
        from omni_hub.retrieval.bge_reranker import bge_rerank
        from omni_hub.retrieval.base import RetrievalRecord
    except ImportError:
        return records[:top_k]

    # Convert dicts → RetrievalRecord for BGE
    rrec_list: list = []
    for d in records:
        rrec_list.append(RetrievalRecord(
            source=d.get("source", "?"),
            title=d.get("title", ""),
            url=d.get("url", ""),
            snippet=d.get("snippet", ""),
            score=float(d.get("score", 0.0) or 0.0),
            canonical_id=d.get("canonical_id", ""),
            metadata=d.get("metadata", {}),
        ))
    try:
        ranked = bge_rerank(query, rrec_list, top_k=top_k)
    except Exception as exc:                                      # noqa: BLE001
        sys.stderr.write(f"  ⚠ BGE rerank failed: {exc}; falling back\n")
        return records[:top_k]
    # Map back to dicts preserving _via
    via_lookup = {(d.get("canonical_id", ""), d.get("title", "")): d.get("_via", "?")
                  for d in records}
    out: list[dict] = []
    for r in ranked:
        via = via_lookup.get((r.canonical_id, r.title), "?")
        out.append({
            "_via": via,
            "title": r.title, "url": r.url, "snippet": r.snippet,
            "source": r.source, "score": r.score,
            "canonical_id": r.canonical_id, "metadata": r.metadata,
            "_bge_score": r.score,
        })
    return out


def _gather_entity(entity_id: str, sources: list[str], per_source_limit: int) -> tuple[dict, list[dict]]:
    """Use follow_entity.gather_one to pull records."""

    sys.path.insert(0, str(_repo_root() / "src"))
    sys.path.insert(0, str(_repo_root() / "scripts"))
    import follow_entity                                          # type: ignore

    entity = _load_entity(entity_id)
    if not entity:
        return {}, []
    records = follow_entity._gather_one(entity, sources, per_source_limit)
    return entity, records


def _render_entity_section(entity: dict, records: list[dict]) -> str:
    """Markdown for one entity, grouped by source."""

    if not entity:
        return "\n_(entity not found)_\n"
    lines: list[str] = [f"## {entity['display']}  ({entity.get('_bucket','?')})\n"]
    if not records:
        lines.append("_(no records this run)_\n")
        return "\n".join(lines)
    for r in records:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("snippet") or "").strip()
        via = r.get("_via", "?")
        bge_score = r.get("_bge_score")
        score_tag = f" `bge={bge_score:+.2f}`" if isinstance(bge_score, float) else ""
        line = f"- [{title}]({url})" if url else f"- {title}"
        line = f"`{via}`{score_tag} {line}"
        lines.append(line)
        if snippet:
            lines.append(f"  > {snippet[:240]}")
    return "\n".join(lines) + "\n"


def main() -> int:
    today = date.today().isoformat()
    out_dir = _repo_root() / ".omni" / "briefs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"follow-{today}.md"

    people = _split_env("DAILY_FOLLOW_PEOPLE", DEFAULT_PEOPLE)
    companies = _split_env("DAILY_FOLLOW_COMPANIES", DEFAULT_COMPANIES)
    sources = os.environ.get("DAILY_FOLLOW_SOURCES", DEFAULT_PER_ENTITY_SOURCES)
    src_list = [s.strip() for s in sources.split(",") if s.strip()]
    per_source_limit = int(os.environ.get("DAILY_FOLLOW_LIMIT", str(DEFAULT_PER_ENTITY_LIMIT)))
    top_k = int(os.environ.get("DAILY_FOLLOW_RERANK_TOP_K", str(DEFAULT_RERANK_TOP_K)))
    bge_disabled = bool(os.environ.get("DAILY_FOLLOW_DISABLE_BGE", "").strip())

    sections: list[str] = [
        f"# Daily follow brief — {today}\n",
        f"_Generated by `daily_follow_brief.py`; "
        f"{'BGE rerank ON' if not bge_disabled else 'BGE OFF (raw)'}; "
        f"sources={sources}; per-source limit={per_source_limit}; top_k={top_k}_\n",
    ]

    sections.append(f"\n# People ({len(people)})\n")
    for eid in people:
        sys.stderr.write(f"→ person: {eid}\n")
        entity, records = _gather_entity(eid, src_list, per_source_limit)
        if not bge_disabled and entity:
            records = _rerank_records(records, query=entity.get("display", eid), top_k=top_k)
        sections.append(_render_entity_section(entity, records))
        sections.append("\n---\n")

    sections.append(f"\n# Companies ({len(companies)})\n")
    for eid in companies:
        sys.stderr.write(f"→ company: {eid}\n")
        entity, records = _gather_entity(eid, src_list, per_source_limit)
        if not bge_disabled and entity:
            records = _rerank_records(records, query=entity.get("display", eid), top_k=top_k)
        sections.append(_render_entity_section(entity, records))
        sections.append("\n---\n")

    out_path.write_text("\n".join(sections), encoding="utf-8")
    sys.stderr.write(f"\n✅ brief written: {out_path}\n")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
