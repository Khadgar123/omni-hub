"""Paper enrichment — API-metadata-first dossier for the hard-to-parse fields.

Review gap #2 (lossy paper extraction): naive PDF parsing drops exactly the
fields a research KB cares about — venue / acceptance status, released code
+ its completeness, released model checkpoints + datasets.  The SOTA fix
(per the 2026 ingestion research) is *not* to parse the PDF harder, but to
join authoritative APIs keyed on the arXiv id / DOI / title:

* **DBLP**        → venue + venue-type (conf vs journal) = "did this appear
                    in a peer-reviewed venue" (中稿 signal).  CC0, no key.
* **Hugging Face**→ models (checkpoints) + datasets filtered by ``arxiv:<id>``
                    = released-weights / released-data signal.  No key.
* **GitHub**      → repo presence + the ML Code Completeness Checklist
                    (deps / train / eval / weights / results) = 代码完整度.
                    Anonymous works; a token lifts the rate limit.

Everything is fail-soft: a failed sub-fetch degrades one field, never the
dossier.  Each HTTP call is injectable (``fetch=``) so the whole thing is
unit-testable with no network.  Stdlib only (shared ``http_get_json``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .base import DEFAULT_TIMEOUT_SEC, http_get_json

DBLP_PUBL_API = "https://dblp.org/search/publ/api"
HF_MODELS_API = "https://huggingface.co/api/models"
HF_DATASETS_API = "https://huggingface.co/api/datasets"
GITHUB_API = "https://api.github.com"

# The ML Code Completeness Checklist (PwC / NeurIPS 2020), the de-facto
# standard 5-axis reproducibility score.  Each axis is detected from repo
# file presence + README content.
CODE_COMPLETENESS_AXES = (
    "dependencies",      # requirements.txt / environment.yml / setup.py / pyproject
    "training",          # train script
    "evaluation",        # eval / test script
    "pretrained",        # released weights / checkpoint link
    "results",           # results table / reproduce script
)

JsonFetch = Callable[..., Any]


@dataclass(slots=True)
class PaperDossier:
    """The joined, API-sourced view of a paper's hard-to-parse fields."""

    arxiv_id: str = ""
    doi: str = ""
    title: str = ""
    venue: str = ""
    venue_type: str = ""                 # "conference" | "journal" | ""
    venue_year: str = ""
    accepted: bool | None = None         # appeared in a peer-reviewed venue
    code_repos: list[str] = field(default_factory=list)
    code_completeness: dict[str, Any] = field(default_factory=dict)
    checkpoints: list[str] = field(default_factory=list)  # HF model ids
    datasets: list[str] = field(default_factory=list)     # HF dataset ids
    provenance: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "arxiv_id": self.arxiv_id,
            "doi": self.doi,
            "title": self.title,
            "venue": self.venue,
            "venue_type": self.venue_type,
            "venue_year": self.venue_year,
            "accepted": self.accepted,
            "code_repos": self.code_repos,
            "code_completeness": self.code_completeness,
            "checkpoints": self.checkpoints,
            "datasets": self.datasets,
            "provenance": self.provenance,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# DBLP — venue / acceptance
# ---------------------------------------------------------------------------


def fetch_dblp_venue(
    title: str,
    *,
    fetch: JsonFetch = http_get_json,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Look up a paper's venue in DBLP by title.

    DBLP only indexes peer-reviewed publications, so a hit there *is* the
    acceptance signal.  Returns ``{venue, venue_type, year, accepted}`` (empty
    when no confident hit).
    """

    title = title.strip()
    if not title:
        return {}
    data = fetch(
        DBLP_PUBL_API,
        params={"q": title, "format": "json", "h": "5"},
        timeout=timeout,
    )
    hits = (
        (data.get("result", {}) or {}).get("hits", {}).get("hit", [])
        if isinstance(data, dict) else []
    )
    if isinstance(hits, dict):
        hits = [hits]
    norm_title = _norm(title)
    for hit in hits:
        info = (hit or {}).get("info", {}) if isinstance(hit, dict) else {}
        if not isinstance(info, dict):
            continue
        hit_title = _norm(str(info.get("title", "")))
        # require a strong title match so we don't mis-attribute a venue
        if not hit_title or not _title_match(norm_title, hit_title):
            continue
        venue = str(info.get("venue", "")).strip()
        pub_type = str(info.get("type", "")).strip().lower()
        venue_type = (
            "conference" if "conference" in pub_type
            else "journal" if "journal" in pub_type
            else ""
        )
        return {
            "venue": venue,
            "venue_type": venue_type,
            "year": str(info.get("year", "")).strip(),
            "accepted": True,             # DBLP-indexed = peer-reviewed
        }
    return {}


# ---------------------------------------------------------------------------
# Hugging Face — checkpoints + datasets by arxiv id
# ---------------------------------------------------------------------------


def fetch_hf_artifacts(
    arxiv_id: str,
    *,
    fetch: JsonFetch = http_get_json,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    limit: int = 25,
) -> dict[str, list[str]]:
    """Released models (checkpoints) + datasets tagged ``arxiv:<id>`` on HF Hub.

    HF auto-extracts the ``arxiv:<id>`` tag from any repo README, so this is a
    live bidirectional index of released weights/data for a paper.
    """

    aid = _norm_arxiv(arxiv_id)
    if not aid:
        return {"checkpoints": [], "datasets": []}
    out: dict[str, list[str]] = {"checkpoints": [], "datasets": []}
    for key, api in (("checkpoints", HF_MODELS_API), ("datasets", HF_DATASETS_API)):
        data = fetch(
            api,
            params={"filter": f"arxiv:{aid}", "limit": str(limit)},
            timeout=timeout,
        )
        if isinstance(data, list):
            out[key] = [
                str(item.get("id", "")).strip()
                for item in data
                if isinstance(item, dict) and item.get("id")
            ]
    return out


# ---------------------------------------------------------------------------
# GitHub — code completeness (ML Code Completeness Checklist)
# ---------------------------------------------------------------------------


def score_code_completeness(
    repo: str,
    *,
    fetch: JsonFetch = http_get_json,
    token: str = "",
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Score a GitHub repo on the 5-axis ML Code Completeness Checklist.

    ``repo`` is ``owner/name`` or a full github URL.  Detects each axis from
    the repo's top-level file listing + README; returns per-axis booleans, a
    0..1 score, and health signals (stars, license, last push).
    """

    owner_name = _parse_github_repo(repo)
    if not owner_name:
        return {}
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    meta = fetch(f"{GITHUB_API}/repos/{owner_name}", headers=headers, timeout=timeout)
    if not isinstance(meta, dict) or "full_name" not in meta:
        return {}

    listing = fetch(
        f"{GITHUB_API}/repos/{owner_name}/contents",
        headers=headers, timeout=timeout,
    )
    names = [
        str(e.get("name", "")).lower()
        for e in listing if isinstance(e, dict)
    ] if isinstance(listing, list) else []
    joined = " ".join(names)

    readme_text = ""
    try:
        readme = fetch(
            f"{GITHUB_API}/repos/{owner_name}/readme",
            headers=headers, timeout=timeout,
        )
        if isinstance(readme, dict):
            import base64
            content = readme.get("content", "")
            if content:
                readme_text = base64.b64decode(content).decode("utf-8", "replace").lower()
    except Exception:                                              # noqa: BLE001
        readme_text = ""

    axes = {
        "dependencies": any(
            n in joined for n in
            ("requirements.txt", "environment.yml", "setup.py", "pyproject.toml", "poetry.lock")
        ),
        "training": bool(re.search(r"\btrain\w*\.(py|sh|ipynb)\b", joined)),
        "evaluation": bool(re.search(r"\b(eval|test|inference|predict)\w*\.(py|sh|ipynb)\b", joined)),
        "pretrained": (
            any(n in joined for n in (".pt", ".pth", ".ckpt", ".safetensors", "checkpoints", "weights"))
            or any(k in readme_text for k in ("pretrained", "checkpoint", "model weights", "huggingface.co/"))
        ),
        "results": (
            any(k in readme_text for k in ("results", "benchmark", "leaderboard", "reproduce"))
            or "results" in joined
        ),
    }
    score = round(sum(axes.values()) / len(CODE_COMPLETENESS_AXES), 3)
    return {
        "repo": meta.get("full_name", owner_name),
        "axes": axes,
        "score": score,
        "stars": int(meta.get("stargazers_count", 0) or 0),
        "license": ((meta.get("license") or {}) or {}).get("spdx_id", "") if isinstance(meta.get("license"), dict) else "",
        "pushed_at": str(meta.get("pushed_at", "")),
        "archived": bool(meta.get("archived", False)),
    }


# ---------------------------------------------------------------------------
# top-level join
# ---------------------------------------------------------------------------


def enrich_paper(
    *,
    arxiv_id: str = "",
    doi: str = "",
    title: str = "",
    code_repos: list[str] | None = None,
    fetch: JsonFetch = http_get_json,
    github_token: str = "",
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> PaperDossier:
    """Build a :class:`PaperDossier` by joining DBLP + HF Hub + GitHub.

    Each join is fail-soft; failures land in ``dossier.errors`` and leave the
    rest intact.  ``code_repos`` are scored if supplied (the caller usually
    has them from the paper's abstract / arXiv "Code" tab).
    """

    dossier = PaperDossier(arxiv_id=_norm_arxiv(arxiv_id), doi=doi.strip(), title=title.strip())

    if title.strip():
        try:
            venue = fetch_dblp_venue(title, fetch=fetch, timeout=timeout)
            if venue:
                dossier.venue = venue.get("venue", "")
                dossier.venue_type = venue.get("venue_type", "")
                dossier.venue_year = venue.get("year", "")
                dossier.accepted = venue.get("accepted")
                dossier.provenance["venue"] = "dblp"
        except Exception as exc:                                   # noqa: BLE001
            dossier.errors.append(f"dblp: {exc}")

    if dossier.arxiv_id:
        try:
            arts = fetch_hf_artifacts(dossier.arxiv_id, fetch=fetch, timeout=timeout)
            dossier.checkpoints = arts.get("checkpoints", [])
            dossier.datasets = arts.get("datasets", [])
            if dossier.checkpoints or dossier.datasets:
                dossier.provenance["artifacts"] = "huggingface"
        except Exception as exc:                                   # noqa: BLE001
            dossier.errors.append(f"hf_hub: {exc}")

    for repo in code_repos or []:
        try:
            score = score_code_completeness(
                repo, fetch=fetch, token=github_token, timeout=timeout,
            )
            if score:
                dossier.code_repos.append(score["repo"])
                # keep the most-complete repo's checklist as the headline
                if (not dossier.code_completeness
                        or score["score"] > dossier.code_completeness.get("score", -1)):
                    dossier.code_completeness = score
                dossier.provenance["code"] = "github"
        except Exception as exc:                                   # noqa: BLE001
            dossier.errors.append(f"github({repo}): {exc}")

    return dossier


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _title_match(a: str, b: str) -> bool:
    """Strong-ish title match: one is a prefix of the other, or high token overlap."""
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(len(ta), len(tb))
    return overlap >= 0.8


def _norm_arxiv(arxiv_id: str) -> str:
    """Normalise to a bare arXiv id (strip arxiv:, version suffix, URL)."""
    a = arxiv_id.strip().lower()
    a = re.sub(r"^arxiv:", "", a)
    a = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", a)
    a = re.sub(r"v\d+$", "", a)
    a = a.removesuffix(".pdf")
    return a.strip()


def _parse_github_repo(repo: str) -> str:
    """``owner/name`` from a slug or a github URL; '' if not parseable."""
    r = repo.strip()
    m = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+)", r)
    if m:
        return m.group(1).removesuffix(".git")
    if re.fullmatch(r"[\w.-]+/[\w.-]+", r):
        return r.removesuffix(".git")
    return ""


__all__ = [
    "PaperDossier",
    "CODE_COMPLETENESS_AXES",
    "fetch_dblp_venue",
    "fetch_hf_artifacts",
    "score_code_completeness",
    "enrich_paper",
]
