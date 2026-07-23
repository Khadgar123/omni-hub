"""Cross-source paper identity resolution — fold an arXiv preprint and its
accepted / published version into ONE paper.

**Why this exists.**  The cascade dedups by a SINGLE ``canonical_id``
(``arxiv:X`` | ``doi:Y`` | ``openreview:Z``).  A paper commonly lives on arXiv
as a preprint, is later ACCEPTED with a proceedings DOI, and has an OpenReview
thread — three records with three different ids that the exact-match dedup
never folds together.  So re-ingesting the accepted version *duplicates* a
preprint that is already in the repo (the exact failure the operator flagged).

**How it's solvable.**  Semantic Scholar / OpenAlex records carry the FULL
external-id set (both ``ArXiv`` and ``DOI`` for the same work) in
``metadata['external_ids']``, so those records act as a *bridge*: union any
records that share ANY strong identifier and the preprint + accepted + thread
fold into one paper.

This module is the dedup ENGINE.  It is pure + side-effect-free so it can be
unit-tested in isolation and wired into (a) the conference-accepted-paper
ingest and (b) the cascade fusion, without each caller re-implementing it.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .base import RetrievalRecord

# Records may be RetrievalRecord objects (cascade) OR plain dicts (the
# wiki-ingest path reads evidence.jsonl as dicts).  These accessors let the
# whole module work on both without converting.


def _cid(rec) -> str:
    v = rec.get("canonical_id") if isinstance(rec, dict) else getattr(rec, "canonical_id", "")
    return str(v or "")


def _md(rec) -> dict:
    m = rec.get("metadata") if isinstance(rec, dict) else getattr(rec, "metadata", None)
    return m if isinstance(m, dict) else {}


def _title(rec) -> str:
    v = rec.get("title") if isinstance(rec, dict) else getattr(rec, "title", "")
    return str(v or "")


def _source(rec) -> str:
    v = rec.get("source") if isinstance(rec, dict) else getattr(rec, "source", "")
    return str(v or "")


def _score(rec) -> float:
    v = rec.get("score") if isinstance(rec, dict) else getattr(rec, "score", 0.0)
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _set_md(rec, md: dict) -> None:
    if isinstance(rec, dict):
        rec["metadata"] = md
    else:
        rec.metadata = md


def _norm_arxiv(v: object) -> str:
    """``2401.01234v3`` / ``arXiv:2401.01234`` / a URL → ``arxiv:2401.01234``."""
    s = str(v or "").strip().lower()
    s = s.replace("arxiv:", "").rstrip("/").rsplit("/", 1)[-1]
    s = re.sub(r"v\d+$", "", s)  # version-agnostic (v1 and v2 are one paper)
    return f"arxiv:{s}" if s else ""


def _norm_doi(v: object) -> str:
    s = str(v or "").strip().lower()
    for p in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
        if s.startswith(p):
            s = s[len(p):]
            break
    return f"doi:{s}" if s else ""


def normalized_title(title: object) -> str:
    """Lowercased, alnum-only title — a LAST-RESORT match key (use with care:
    distinct papers can share a short title)."""
    return re.sub(r"[^a-z0-9]+", "", str(title or "").lower())


def paper_identity_keys(rec: RetrievalRecord) -> set[str]:
    """Every STRONG identifier a record is known by, for cross-source union.

    Strong = arXiv id (version-stripped), DOI, PubMed id, S2 CorpusId,
    OpenReview forum.  Title is deliberately EXCLUDED (false-merge risk) —
    see :func:`normalized_title` for opt-in title assistance.
    """
    keys: set[str] = set()
    md = _md(rec)

    cid = _cid(rec).strip().lower()
    if cid.startswith("arxiv:"):
        keys.add(_norm_arxiv(cid))
    elif cid.startswith("doi:"):
        keys.add(_norm_doi(cid))
    elif cid:
        keys.add(cid)

    aid = md.get("arxiv_base_id") or md.get("arxiv_id")
    if aid:
        keys.add(_norm_arxiv(aid))
    if md.get("doi"):
        keys.add(_norm_doi(md.get("doi")))

    ext = md.get("external_ids")
    if isinstance(ext, dict):
        if ext.get("ArXiv"):
            keys.add(_norm_arxiv(ext["ArXiv"]))
        if ext.get("DOI"):
            keys.add(_norm_doi(ext["DOI"]))
        if ext.get("PubMed"):
            keys.add(f"pmid:{str(ext['PubMed']).strip()}")
        if ext.get("CorpusId"):
            keys.add(f"s2:{str(ext['CorpusId']).strip()}")

    if md.get("forum_id"):
        keys.add(f"openreview:{str(md['forum_id']).strip().lower()}")

    return {k for k in keys if k and k not in ("arxiv:", "doi:", "pmid:", "s2:")}


def _accept_rank(rec: RetrievalRecord) -> tuple:
    """Prefer the record representing the ACCEPTED/published version: has a
    DOI, then has a venue, then higher score — so the merged record's primary
    is the canonical published one, not the raw preprint."""
    md = _md(rec)
    has_doi = 1 if (md.get("doi") or _cid(rec).startswith("doi:")) else 0
    has_venue = 1 if (md.get("venue") or md.get("journal_ref") or md.get("venueid")) else 0
    return (has_doi, has_venue, _score(rec))


def _merge_group(group: list[RetrievalRecord]) -> RetrievalRecord:
    if len(group) == 1:
        return group[0]
    primary = max(group, key=_accept_rank)
    all_ids: set[str] = set()
    sources: list[str] = []
    for rec in group:
        all_ids |= paper_identity_keys(rec)
        s = _source(rec)
        if s and s not in sources:
            sources.append(s)
    md = dict(_md(primary))
    md["merged_ids"] = sorted(all_ids)
    md["merged_sources"] = sources
    # Backfill acceptance/venue/doi signals from whichever record carries them
    # (the preprint won't have a venue; the accepted record will).
    for rec in group:
        rmd = _md(rec)
        for field in ("venue", "venueid", "doi", "comment", "journal_ref"):
            if not md.get(field) and rmd.get(field):
                md[field] = rmd[field]
        if rmd.get("accepted") and not md.get("accepted"):
            md["accepted"] = rmd["accepted"]
    _set_md(primary, md)
    return primary


def merge_papers(
    records: list[RetrievalRecord],
    *,
    use_title_fallback: bool = False,
) -> list[RetrievalRecord]:
    """Union records that share ANY strong identifier; return one record per
    paper.  The merged record keeps the richest (accepted/published) source's
    fields and records ``metadata['merged_ids']`` + ``metadata['merged_sources']``
    so provenance is preserved.

    ``use_title_fallback`` additionally unions records with an identical
    normalized title ≥ 12 chars (off by default — can over-merge same-titled
    papers; enable only when sources lack cross-referenced ids).
    """
    n = len(records)
    if n <= 1:
        return list(records)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    key_owner: dict[str, int] = {}
    for i, rec in enumerate(records):
        keys = paper_identity_keys(rec)
        if use_title_fallback:
            t = normalized_title(_title(rec))
            if len(t) >= 12:
                keys.add(f"title:{t}")
        for k in keys:
            if k in key_owner:
                union(i, key_owner[k])
            else:
                key_owner[k] = i

    groups: dict[int, list[RetrievalRecord]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(records[i])
    return [_merge_group(g) for g in groups.values()]
