"""Wiki-as-projection: render synthesis pages deterministically from claims.

WS1 of the 2026-05-29 refactor (see docs/rfc-2026-05-29-claims-skills-researchflow.md).

The drift problem this kills: before WS1, a synthesis wiki ``.md`` and the
claims it references were *two independent sources of truth* — a hand-edit to
the page silently diverged from ``.omni/claims.jsonl``.

The fix: **claims are the single source of truth; a synthesis page is a
deterministic projection of the claims that carry its ``target_path``.** Same
claims → byte-identical page, so the page can be deleted and rebuilt at will
(``omni-hub wiki-render``).

Scope (deliberately minimal — "若非必要勿增实体"):
* Only ``page_type: synthesis`` pages are projections (they ARE claim
  aggregations).  ``concept`` / ``entity`` / ``method`` pages may carry
  human-authored exposition and keep their proposal body.
* Page *identity* (title / query / page_type / domain) that is not derivable
  from any single claim lives in a tiny rebuild index
  ``.omni/wiki_pages.jsonl`` — a projection/cache, NOT a second knowledge
  source.  Deleting it only loses cosmetic framing, never facts.

No third-party deps; stdlib only.  No top-level import of knowledge_plane
(that module imports this one lazily) so there is no import cycle.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RENDER_VERSION = "v1"
CLAIM_LEDGER_PATH = ".omni/claims.jsonl"
WIKI_PAGES_INDEX = ".omni/wiki_pages.jsonl"
SYNTHESIS_DIR = "vault/wiki/syntheses/"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# page_type detection
# ---------------------------------------------------------------------------


def is_synthesis_target(target_path: str, page_type: str = "") -> bool:
    """A page is a claims-projection iff it is a synthesis page.

    Prefer an explicit ``page_type``; fall back to the canonical
    ``vault/wiki/syntheses/`` location.
    """

    if page_type:
        return page_type.strip().lower() == "synthesis"
    return target_path.replace("\\", "/").find(SYNTHESIS_DIR) != -1


def infer_page_type(target_path: str, page_type: str = "") -> str:
    if page_type:
        return page_type.strip().lower()
    norm = target_path.replace("\\", "/")
    for marker in ("syntheses/", "concepts/", "entities/", "methods/", "events/"):
        if marker in norm:
            return marker.rstrip("s/") if marker != "syntheses/" else "synthesis"
    return "synthesis" if SYNTHESIS_DIR in norm else "concept"


# ---------------------------------------------------------------------------
# claim loading (own minimal loader — no knowledge_plane internals)
# ---------------------------------------------------------------------------


def _load_claims(workspace_root: Path) -> list[dict[str, Any]]:
    ledger = workspace_root / CLAIM_LEDGER_PATH
    if not ledger.exists():
        return []
    rows: list[dict[str, Any]] = []
    with ledger.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _is_closed(claim: dict[str, Any]) -> bool:
    """Closed = retired on the valid timeline or rejected/superseded."""

    if claim.get("t_valid_to"):
        return True
    return str(claim.get("review_state", "")).lower() in {"rejected", "superseded"}


def active_claims_for_page(
    workspace_root: Path, target_path: str
) -> list[dict[str, Any]]:
    """Active (non-closed) claims whose ``target_path`` is this page.

    Deterministic order: (t_valid_from, claim_id) so the rendered page is
    byte-stable across rebuilds.
    """

    target = target_path.replace("\\", "/")
    out = [
        c
        for c in _load_claims(workspace_root)
        if str(c.get("target_path", "")).replace("\\", "/") == target
        and not _is_closed(c)
    ]
    out.sort(key=lambda c: (str(c.get("t_valid_from", "")), str(c.get("claim_id", ""))))
    return out


def stamp_target_path(claims: list[dict[str, Any]], target_path: str) -> list[dict[str, Any]]:
    """Stamp ``target_path`` + ``render_version`` onto each claim dict (in place).

    This is the claim→page projection link.  Idempotent: re-stamping the same
    page is a no-op on dedup at append time.
    """

    for claim in claims:
        if isinstance(claim, dict):
            claim["target_path"] = target_path
            claim.setdefault("render_version", RENDER_VERSION)
    return claims


# ---------------------------------------------------------------------------
# page-identity rebuild index (.omni/wiki_pages.jsonl)
# ---------------------------------------------------------------------------


def record_page_meta(
    workspace_root: Path,
    target_path: str,
    *,
    page_type: str,
    domain: str,
    title: str = "",
    query: str = "",
) -> None:
    """Upsert page identity into the rebuild index (atomic rewrite)."""

    index = workspace_root / WIKI_PAGES_INDEX
    index.parent.mkdir(parents=True, exist_ok=True)
    rows = _load_page_index(workspace_root)
    rows[target_path] = {
        "target_path": target_path,
        "page_type": page_type,
        "domain": domain,
        "title": title,
        "query": query,
        "updated_at": _utcnow(),
    }
    tmp = index.with_suffix(index.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for key in sorted(rows):
            handle.write(json.dumps(rows[key], ensure_ascii=False) + "\n")
    tmp.replace(index)


def _load_page_index(workspace_root: Path) -> dict[str, dict[str, Any]]:
    index = workspace_root / WIKI_PAGES_INDEX
    if not index.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with index.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("target_path"):
                rows[str(row["target_path"])] = row
    return rows


def load_page_meta(workspace_root: Path, target_path: str) -> dict[str, Any] | None:
    return _load_page_index(workspace_root).get(target_path)


def iter_page_meta(workspace_root: Path) -> list[dict[str, Any]]:
    return [_load_page_index(workspace_root)[k] for k in sorted(_load_page_index(workspace_root))]


# ---------------------------------------------------------------------------
# the deterministic renderer
# ---------------------------------------------------------------------------


def _derive_title(target_path: str) -> str:
    stem = Path(target_path).stem.replace("-", " ").replace("_", " ").strip()
    return stem.title() if stem else "Synthesis"


def render_synthesis_from_claims(
    workspace_root: Path,
    target_path: str,
    *,
    page_meta: dict[str, Any] | None = None,
) -> str:
    """Render a synthesis page deterministically from its active claims.

    ``page_meta`` (title/query/page_type/domain) supplies the page identity;
    when omitted it is read from the rebuild index, then defaulted.  The body
    — the actual knowledge — comes entirely from claims, so the same claims
    always yield the same page.
    """

    meta = dict(page_meta or load_page_meta(workspace_root, target_path) or {})
    claims = active_claims_for_page(workspace_root, target_path)

    domain = str(meta.get("domain", "")) or (str(claims[0].get("domain", "")) if claims else "")
    title = str(meta.get("title", "")) or _derive_title(target_path)
    query = str(meta.get("query", ""))

    claim_ids = [str(c.get("claim_id", "")) for c in claims if c.get("claim_id")]
    source_ids: list[str] = []
    seen: set[str] = set()
    for c in claims:
        for sup in c.get("support", []) or []:
            sid = str((sup or {}).get("source_id", "")).strip()
            if sid and sid not in seen:
                seen.add(sid)
                source_ids.append(sid)

    # confidence: average of the contributing claims, mapped to the page's
    # coarse scale.
    confs = [float(c.get("confidence", 0.5) or 0.5) for c in claims] or [0.5]
    avg = sum(confs) / len(confs)
    page_conf = "high" if avg >= 0.75 else "medium" if avg >= 0.4 else "low"

    # DETERMINISM: derive t_valid_from from the claims (earliest), never from
    # wall-clock — so the same claims always render a byte-identical page and a
    # rebuild is a no-op.  Only the truly-empty page falls back to meta/now.
    valid_froms = [str(c.get("t_valid_from", "")) for c in claims if c.get("t_valid_from")]
    page_valid_from = (
        min(valid_froms) if valid_froms
        else (str(meta.get("t_valid_from", "")) or _utcnow())
    )

    lines = [
        "---",
        "page_type: synthesis",
        f"domain: {domain}",
        f"claim_ids: {json.dumps(claim_ids, ensure_ascii=False)}",
        f"source_ids: {json.dumps(source_ids, ensure_ascii=False)}",
        f"t_valid_from: {page_valid_from}",
        "t_valid_to: null",
        "superseded_by: null",
        f"confidence: {page_conf}",
        "review_state: approved",
        f"render_version: {RENDER_VERSION}",
        "rendered_from: claims",
        "---",
        "",
        f"# {title}",
        "",
        f"> Synthesis projected from {len(claims)} claim(s) in `.omni/claims.jsonl`"
        f"{f' for **{query}**' if query else ''} ({domain}).",
        "",
        "<!-- This page is a PROJECTION of claims. Do not hand-edit; edits are "
        "overwritten on the next `wiki-render`. Change claims via Proposal[T]. -->",
        "",
    ]
    if query:
        lines += ["## Question", "", query, ""]

    lines += ["## Compiled Findings", ""]
    if claims:
        for idx, claim in enumerate(claims, start=1):
            statement = str(claim.get("statement", "")).strip()
            cid = str(claim.get("claim_id", ""))
            conf = claim.get("confidence", "")
            # Surface [R1]-style cite markers from the claim's support so a
            # finding still links back to its evidence record (preserved from
            # the pre-projection synthesis format).
            cites = " ".join(
                f"[{(s or {}).get('cite_id')}]"
                for s in (claim.get("support") or [])
                if (s or {}).get("cite_id")
            )
            prefix = f"{cites} " if cites else ""
            lines.append(f"{idx}. {prefix}{statement} [{cid}] _(confidence: {conf})_")
    else:
        lines.append("_No active claims reference this page._")
    lines.append("")

    lines += ["## Provenance", ""]
    for claim in claims:
        cid = str(claim.get("claim_id", ""))
        sups = claim.get("support", []) or []
        srcs = ", ".join(
            f"{(s or {}).get('source', '?')}:{(s or {}).get('source_id', '')}"
            for s in sups
        ) or "(no support)"
        lines.append(f"- `{cid}` ← {srcs}")
    lines.append("")

    lines += [
        "## Projection Metadata",
        "",
        f"- render_version: `{RENDER_VERSION}`",
        f"- claims projected: {len(claims)}",
        f"- target_path: `{target_path}`",
        "",
    ]
    return "\n".join(lines)


def render_page(workspace_root: Path, target_path: str) -> dict[str, Any]:
    """Rebuild a single synthesis page from its claims and write it to disk.

    Returns a small report.  Raises ``ValueError`` if the page is not a
    synthesis target (concept/entity/method pages are authored, not
    projected, so they are never rebuilt from claims).
    """

    meta = load_page_meta(workspace_root, target_path)
    page_type = str((meta or {}).get("page_type", ""))
    if not is_synthesis_target(target_path, page_type):
        raise ValueError(
            f"{target_path} is not a synthesis page; only synthesis pages are "
            "projections of claims"
        )
    claims = active_claims_for_page(workspace_root, target_path)
    if not claims:
        # Nothing to project — never clobber an existing (possibly authored)
        # body with an empty projection.  A zero-claim synthesis page is a
        # `doctor_projection` orphan, surfaced there rather than silently
        # blanked here.
        return {"target_path": target_path, "claims": 0, "skipped": "no active claims"}
    body = render_synthesis_from_claims(workspace_root, target_path, page_meta=meta)
    page_path = workspace_root / target_path
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(body.rstrip() + "\n", encoding="utf-8")
    return {
        "target_path": target_path,
        "claims": len(claims),
        "bytes": len(body),
    }


def render_all(workspace_root: Path) -> dict[str, Any]:
    """Rebuild every known synthesis page from claims.

    Source of pages: the rebuild index (.omni/wiki_pages.jsonl) UNION any
    synthesis target_path referenced by an active claim (so a page is
    reconstructable even if the index was lost).  This is what makes the
    wiki disposable: ``rm -rf vault/wiki/syntheses && wiki-render`` rebuilds
    it byte-identically.
    """

    targets: set[str] = {
        str(m.get("target_path", ""))
        for m in iter_page_meta(workspace_root)
        if is_synthesis_target(str(m.get("target_path", "")), str(m.get("page_type", "")))
    }
    # Union with claim-referenced synthesis targets (index-loss recovery).
    for claim in _load_claims(workspace_root):
        tp = str(claim.get("target_path", ""))
        if tp and is_synthesis_target(tp) and not _is_closed(claim):
            targets.add(tp)
    targets.discard("")

    rendered: list[dict[str, Any]] = []
    for target in sorted(targets):
        try:
            rendered.append(render_page(workspace_root, target))
        except Exception as exc:                                 # noqa: BLE001
            rendered.append({"target_path": target, "error": str(exc)})
    return {
        "pages_rendered": sum(1 for r in rendered if "error" not in r),
        "pages_failed": sum(1 for r in rendered if "error" in r),
        "pages": rendered,
    }


def doctor_projection(workspace_root: Path) -> dict[str, Any]:
    """Integrity probe for the claims↔wiki projection (WS1).

    * ``orphan_pages``  — a synthesis ``.md`` on disk with no active claim
      pointing at it (it would render empty / is stale).
    * ``unrendered``    — a synthesis target referenced by active claims but
      with no ``.md`` on disk (a missing projection).
    """

    syn_dir = workspace_root / "vault" / "wiki" / "syntheses"
    on_disk = {
        str(p.relative_to(workspace_root)).replace("\\", "/")
        for p in syn_dir.glob("*.md")
    } if syn_dir.exists() else set()

    claim_targets: set[str] = set()
    for claim in _load_claims(workspace_root):
        tp = str(claim.get("target_path", "")).replace("\\", "/")
        if tp and is_synthesis_target(tp) and not _is_closed(claim):
            claim_targets.add(tp)

    orphan_pages = sorted(on_disk - claim_targets)
    unrendered = sorted(claim_targets - on_disk)
    return {
        "orphan_pages": orphan_pages,
        "unrendered": unrendered,
        "ok": not orphan_pages and not unrendered,
    }


__all__ = [
    "RENDER_VERSION",
    "WIKI_PAGES_INDEX",
    "is_synthesis_target",
    "infer_page_type",
    "active_claims_for_page",
    "stamp_target_path",
    "record_page_meta",
    "load_page_meta",
    "iter_page_meta",
    "render_synthesis_from_claims",
    "render_page",
    "render_all",
    "doctor_projection",
]
