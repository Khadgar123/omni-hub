"""Inline citation rendering — `<cite r="R3"/>` → ``[3]`` + References block.

The cascade assigns each ``RetrievalRecord`` a ``cite_id`` like ``R1`` /
``R2`` post-fusion.  Agent prompts include the records (with their
``cite_id``) and the model emits ``<cite r="R3"/>`` markers next to
claims.  This module:

1. parses those markers (regex-only, stdlib),
2. compacts them to inline ``[3]`` numerals,
3. appends a ``References`` block listing the cited records,
4. drops any cite to a record not in the supplied list (defensive — model
   sometimes invents marker IDs).

Mirrors Perplexity / Claude.ai inline-numbered citation UX without
needing any frontend — the rendered Markdown drops cleanly into the
existing CLI / vault / MCP surfaces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .base import RetrievalRecord


CITE_MARKER_RE = re.compile(
    r"<cite\s+r=\"(?P<id>R\d+)\"\s*/?>",
    re.IGNORECASE,
)


@dataclass(slots=True)
class RenderResult:
    body: str
    references: list[RetrievalRecord]
    unknown_ids: list[str]


def render_with_citations(
    text: str,
    records: Iterable[RetrievalRecord],
) -> RenderResult:
    """Replace ``<cite r="Rn"/>`` markers in ``text`` with ``[n]`` and append
    a `## References` block listing only the actually-cited records.

    * Markers referencing IDs not present in ``records`` are stripped from
      the body and surfaced in ``RenderResult.unknown_ids``.
    * Multiple markers next to each other (``<cite r="R1"/><cite r="R3"/>``)
      compact to ``[1][3]`` with no space inside.
    * If no citations are present, ``body`` is unchanged and
      ``references`` is empty.
    """

    by_id: dict[str, RetrievalRecord] = {
        r.cite_id: r for r in records if r.cite_id
    }
    used_ids: list[str] = []
    unknown: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        cid = match.group("id").upper()
        if cid in by_id:
            if cid not in used_ids:
                used_ids.append(cid)
            n = int(cid[1:])
            return f"[{n}]"
        unknown.append(cid)
        return ""

    body = CITE_MARKER_RE.sub(_sub, text)
    refs = [by_id[c] for c in used_ids]
    if refs:
        body = body.rstrip() + "\n\n## References\n\n" + _render_refs(refs)
    return RenderResult(body=body, references=refs, unknown_ids=unknown)


def _render_refs(records: list[RetrievalRecord]) -> str:
    lines: list[str] = []
    for rec in records:
        n = int(rec.cite_id[1:])
        title = rec.title or rec.url or rec.source
        if rec.url:
            line = f"[{n}] [{title}]({rec.url}) — *{rec.source}*"
        else:
            line = f"[{n}] {title} — *{rec.source}*"
        # surface the first sentence of snippet for at-a-glance review
        snippet = (rec.snippet or "").splitlines()[0][:140] if rec.snippet else ""
        if snippet:
            line += f"  \n    {snippet}"
        lines.append(line)
    return "\n".join(lines)


def render_to_structured_citations(
    records: Iterable[RetrievalRecord],
) -> list[dict[str, object]]:
    """Return the citations as a JSON-friendly array — useful for MCP
    clients that want to render rich pills instead of plain text."""

    out: list[dict[str, object]] = []
    for rec in records:
        if not rec.cite_id:
            continue
        out.append({
            "id": rec.cite_id,
            "n": int(rec.cite_id[1:]),
            "title": rec.title,
            "url": rec.url,
            "source": rec.source,
            "snippet": rec.snippet,
            "canonical_id": rec.canonical_id,
        })
    return out
