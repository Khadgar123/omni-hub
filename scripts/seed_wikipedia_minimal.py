#!/usr/bin/env python3
"""Seed pack: pre-curated Wikipedia anchor entities per domain.

Pulls each domain's anchor entities from Wikipedia (English by default,
``zh`` available for cn-policy / social-zh / cooking) via the existing
WikipediaSource connector, writing each as a vault/evidence/<domain>/
record so the cascade has actual data when it goes to grade / dream
later.

Unlike ``seed_arxiv_hf.py`` this needs **no extra dependencies** — it
reuses the stdlib-only WikipediaSource that's already in the cascade.

Usage::

    python3 scripts/seed_wikipedia_minimal.py                # all domains
    python3 scripts/seed_wikipedia_minimal.py --domain finance
    python3 scripts/seed_wikipedia_minimal.py --domain research --limit 5

Anchor lists are curated below — they're the 20-30 highest-relevance
entities for each domain (companies / laws / concepts / people).
Tweak by editing ``DOMAIN_ANCHORS`` directly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


DOMAIN_ANCHORS: dict[str, list[str]] = {
    "ai_progress": [
        "Anthropic", "OpenAI", "Google DeepMind", "Meta AI", "Mistral AI",
        "xAI (company)", "Hugging Face", "GPT-4", "Claude (language model)",
        "Gemini (language model)", "Llama (language model)", "Transformer (deep learning architecture)",
        "Attention (machine learning)", "Reinforcement learning from human feedback",
        "Large language model", "Andrej Karpathy", "Geoffrey Hinton", "Yann LeCun",
        "Sam Altman", "Dario Amodei", "Ilya Sutskever",
    ],
    "research": [
        "Peer review", "ArXiv", "OpenAlex", "Crossref", "DOI",
        "Semantic Scholar", "Google Scholar", "h-index", "Citation impact",
        "Open access", "PubMed", "Europe PMC", "ORCID",
        "Replication crisis", "Preprint", "Open science",
    ],
    "engineering": [
        "Software engineering", "Programming language", "Open-source software",
        "GitHub", "Linux kernel", "Apache HTTP Server", "Continuous integration",
        "Containerization (computing)", "Kubernetes", "Apache Iceberg",
        "PostgreSQL", "Redis", "SQLite", "ELT", "Vector database",
    ],
    "finance": [
        "Federal Reserve", "S&P 500", "NASDAQ", "U.S. Securities and Exchange Commission",
        "Berkshire Hathaway", "Inflation", "Yield curve", "Quantitative easing",
        "Interest rate", "Stock market", "Bond market", "ETF",
        "Tesla, Inc.", "Nvidia", "Microsoft", "Apple Inc.", "Alphabet Inc.", "Amazon (company)",
    ],
    "us_policy": [
        "United States Congress", "Executive Order", "Federal Register",
        "Supreme Court of the United States", "Inflation Reduction Act",
        "CHIPS and Science Act", "Affordable Care Act",
        "United States Department of Justice", "FTC", "FCC",
        "AI Bill of Rights", "Executive Order 14110",
    ],
    "cn_policy": [
        "Government of China", "National People's Congress", "Communist Party of China",
        "Five-year plans of China", "People's Bank of China", "State Council of China",
        "Made in China 2025", "Belt and Road Initiative", "Cyberspace Administration of China",
        "Common prosperity",
    ],
    "international_relations": [
        "United Nations", "World Trade Organization", "G20", "BRICS",
        "European Union", "NATO", "China–United States relations",
        "Trans-Pacific Partnership", "Russo-Ukrainian War", "Israel–Hamas war",
        "Indo-Pacific", "ASEAN",
    ],
    "biomedical": [
        "CRISPR", "mRNA vaccine", "Cancer immunotherapy", "GLP-1",
        "Alzheimer's disease", "AlphaFold", "Protein structure prediction",
        "Genome-wide association study", "Diabetes mellitus", "FDA",
    ],
    "law": [
        "Common law", "Supreme Court of the United States", "Constitutional law",
        "Antitrust law", "Copyright law", "First Amendment to the United States Constitution",
        "Section 230",
    ],
    "agent_systems": [
        "Multi-agent system", "Autonomous agent", "ReAct (AI)",
        "AutoGPT", "Tool use (artificial intelligence)", "Retrieval-augmented generation",
        "Function calling (artificial intelligence)",
    ],
    "marketing": [
        "Search engine optimization", "Content marketing", "Performance marketing",
        "Customer acquisition cost", "Customer lifetime value", "Influencer marketing",
        "Marketing funnel",
    ],
    "enterprise": [
        "Bloomberg L.P.", "PitchBook", "Crunchbase", "OpenCorporates",
        "Venture capital", "Series A round", "Initial public offering",
        "Mergers and acquisitions", "Limited liability company",
    ],
    "fitness_wellness": [
        "VO2 max", "Heart rate variability", "Strength training",
        "Sleep hygiene", "Cardiorespiratory fitness", "Intermittent fasting",
        "Mindfulness", "Mediterranean diet",
    ],
    "cooking": [
        "Maillard reaction", "Sous vide", "Fermentation in food processing",
        "Sourdough", "Asian cuisine", "French cuisine", "Italian cuisine",
        "Knife skills", "Mise en place",
    ],
    "travel": [
        "Schengen Area", "Visa policy of the United States", "Passport",
        "Lonely Planet", "TripAdvisor", "Hostel", "Backpacking (travel)",
    ],
    "photography": [
        "Aperture", "Shutter speed", "ISO (photography)", "Bokeh",
        "Composition (visual arts)", "Color grading", "RAW image format",
    ],
    "fashion": [
        "Haute couture", "Streetwear", "Sustainable fashion", "Fast fashion",
        "Louis Vuitton", "Hermès", "Gucci",
    ],
    "social_en": [
        "Twitter", "Bluesky (social network)", "Reddit", "Hacker News",
        "Mastodon (social network)", "Truth Social",
    ],
    "social_zh": [
        "Sina Weibo", "WeChat", "Xiaohongshu", "Zhihu", "Bilibili", "Douyin",
    ],
    "chat_relationships": [
        "Active listening", "Nonviolent Communication", "Emotional intelligence",
        "Boundaries (personal)", "Conflict resolution",
    ],
    "meta": [
        "Software architecture", "Domain-driven design", "Microservices",
        "Event sourcing", "CQRS",
    ],
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_domain(repo_root: Path, domain: str, anchors: list[str], limit: int) -> int:
    """Pull each anchor via WikipediaSource and write evidence."""

    sys.path.insert(0, str(repo_root / "src"))
    from omni_hub.retrieval.wikipedia import WikipediaSource

    wp = WikipediaSource()
    # Filesystem MUST use the canonical slug (hyphen), never the raw underscore
    # domain key — otherwise evidence splits into ai_progress/ vs ai-progress/
    # twins that `wiki-ingest --domain` cannot reconcile.  Mirrors
    # scripts/seed_orchestrator.py (which already slugifies).
    from omni_hub.knowledge_plane import _slugify
    domain_slug = _slugify(domain)
    run_id = datetime.now().strftime(f"seed-wiki-{domain_slug}-%Y%m%d-%H%M%S")
    evidence_dir = repo_root / "vault" / "evidence" / domain_slug
    raw_dir = repo_root / "vault" / "raw" / domain_slug / run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for idx, anchor in enumerate(anchors[:limit], start=1):
        try:
            records = wp.retrieve(anchor, limit=1, domain=domain)
        except Exception as exc:                                  # noqa: BLE001
            sys.stderr.write(f"  ⚠ {anchor}: {type(exc).__name__}\n")
            continue
        if not records:
            sys.stderr.write(f"  ⚠ {anchor}: no Wikipedia hit\n")
            continue
        rec = records[0]
        canonical = rec.canonical_id or rec.url or anchor
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]

        raw_path = raw_dir / f"{idx:03d}__{digest}.md"
        raw_path.write_text(
            f"---\nrun_id: {run_id}\nidx: {idx}\nsource: wikipedia\n"
            f"canonical_id: {canonical}\nurl: {rec.url}\n"
            f"fetched_at: {_utcnow()}\n---\n\n"
            f"# {rec.title}\n\n{rec.snippet}\n",
            encoding="utf-8",
        )

        ev = {
            "run_id": run_id,
            "record_idx": idx,
            "cite_id": "",
            "source": "wikipedia",
            "title": rec.title,
            "url": rec.url,
            "snippet": rec.snippet,
            "canonical_id": canonical,
            "fetched_at": _utcnow(),
            "score": rec.score,
            "raw_path": str(raw_path.relative_to(repo_root)),
            "metadata": rec.metadata or {},
        }
        ev_path = evidence_dir / f"{run_id}__{idx:03d}__{digest}.json"
        ev_path.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
        written += 1
        sys.stderr.write(f"  ✓ [{idx:2d}/{len(anchors[:limit])}] {anchor}\n")
    return written


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--domain", default="",
                   help="single domain to seed; default ALL")
    p.add_argument("--limit", type=int, default=20,
                   help="per-domain anchor cap (default 20)")
    args = p.parse_args()

    repo_root = Path(__file__).resolve().parent.parent

    domains = [args.domain] if args.domain else sorted(DOMAIN_ANCHORS)
    total = 0
    for dom in domains:
        if dom not in DOMAIN_ANCHORS:
            sys.stderr.write(f"unknown domain: {dom}\n")
            continue
        sys.stderr.write(f"\n# seeding {dom} ({len(DOMAIN_ANCHORS[dom])} anchors)\n")
        n = _seed_domain(repo_root, dom, DOMAIN_ANCHORS[dom], args.limit)
        total += n
        sys.stderr.write(f"  wrote {n} evidence files for {dom}\n")
    sys.stderr.write(f"\n✅ total written: {total}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
