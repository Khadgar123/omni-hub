"""GitHub — repository / release audit for paper code-artifacts.

Replaces the (now-dead, Meta-sunset 2025-07-24) Papers-With-Code pathway:
for "is the code real / maintained / does it ship a checkpoint" we hit the
GitHub REST API directly.  Optional ``GITHUB_TOKEN`` lifts the rate limit
from 60 → 5000 req/hr.

* ``retrieve(query)`` — repository search (top by stars), for the cascade.
* ``repo_audit(owner/repo)`` — on-demand single-repo artifact audit:
  stars, license, last push (maintenance), releases + their assets
  (the release assets are the model checkpoints).
"""

from __future__ import annotations

import os

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json


API_BASE = "https://api.github.com"
SEARCH_REPOS = f"{API_BASE}/search/repositories"
GITHUB_SECRET_REF = "local:omni-hub/api/github/token"


def _resolve_github_token() -> str:
    env = os.environ.get("GITHUB_TOKEN", "").strip()
    if env:
        return env
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(GITHUB_SECRET_REF) or ""
    except SecretStoreError:
        return ""
    except Exception:                                            # noqa: BLE001
        return ""


class GitHubRepoSource:
    name = "github"
    tier = 0          # works anonymous (60/hr); GITHUB_TOKEN lifts to 5000/hr

    def __init__(
        self,
        *,
        token: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.token = token if token is not None else _resolve_github_token()
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def check(self) -> tuple[str, str]:
        if self.token:
            return "ok", "authenticated (5000 req/hr)"
        return "warn", "anonymous (60 req/hr); set GITHUB_TOKEN"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        try:
            data = http_get_json(
                SEARCH_REPOS,
                params={
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": str(min(limit, 25)),
                },
                headers=self._headers(),
                timeout=self.timeout,
            )
        except RetrievalError:
            return []                                  # fail-soft: cascade continues
        items = data.get("items", []) if isinstance(data, dict) else []
        return [self._repo_record(r) for r in items[:limit] if isinstance(r, dict)]

    def _repo_record(self, repo: dict) -> RetrievalRecord:
        full = str(repo.get("full_name", ""))
        stars = int(repo.get("stargazers_count", 0) or 0)
        license_obj = repo.get("license") or {}
        license_id = license_obj.get("spdx_id", "") if isinstance(license_obj, dict) else ""
        pushed = str(repo.get("pushed_at", ""))
        desc = str(repo.get("description", "") or "")
        snippet = f"★{stars}"
        if license_id and license_id != "NOASSERTION":
            snippet += f" · {license_id}"
        if pushed:
            snippet += f" · last push {pushed[:10]}"
        if desc:
            snippet += f" · {desc}"
        return RetrievalRecord(
            source=self.name,
            title=full,
            url=str(repo.get("html_url", "")),
            snippet=snippet[:500],
            score=float(stars),
            canonical_id=f"github:{full.lower()}" if full else "",
            metadata={
                "full_name": full,
                "stars": stars,
                "license": license_id,
                "pushed_at": pushed,
                "forks": int(repo.get("forks_count", 0) or 0),
                "open_issues": int(repo.get("open_issues_count", 0) or 0),
                "archived": bool(repo.get("archived", False)),
                "language": repo.get("language", "") or "",
            },
        )

    def repo_audit(self, owner_repo: str) -> dict | None:
        """On-demand artifact audit for ONE repo: maintenance signals +
        releases (release assets == model checkpoints).  Best-effort → None.

        Accepts ``owner/repo`` or a full ``https://github.com/owner/repo`` URL.
        """

        slug = str(owner_repo).strip()
        slug = slug.removeprefix("https://github.com/").removeprefix("http://github.com/")
        slug = slug.strip("/")
        if slug.count("/") < 1:
            return None
        owner, _, rest = slug.partition("/")
        repo = rest.split("/")[0]
        if not owner or not repo:
            return None
        try:
            meta = http_get_json(
                f"{API_BASE}/repos/{owner}/{repo}",
                headers=self._headers(),
                timeout=self.timeout,
            )
        except RetrievalError:
            return None
        if not isinstance(meta, dict):
            return None
        try:
            rel = http_get_json(
                f"{API_BASE}/repos/{owner}/{repo}/releases",
                params={"per_page": "5"},
                headers=self._headers(),
                timeout=self.timeout,
            )
        except RetrievalError:
            rel = []
        releases: list[dict[str, object]] = []
        for r in (rel if isinstance(rel, list) else []):
            if not isinstance(r, dict):
                continue
            assets = [
                a.get("name", "")
                for a in (r.get("assets") or [])
                if isinstance(a, dict)
            ]
            releases.append({
                "tag": r.get("tag_name", ""),
                "published_at": r.get("published_at", ""),
                "assets": assets,
            })
        lic = meta.get("license") or {}
        return {
            "full_name": meta.get("full_name", f"{owner}/{repo}"),
            "stars": int(meta.get("stargazers_count", 0) or 0),
            "license": lic.get("spdx_id", "") if isinstance(lic, dict) else "",
            "pushed_at": meta.get("pushed_at", ""),
            "archived": bool(meta.get("archived", False)),
            "open_issues": int(meta.get("open_issues_count", 0) or 0),
            "has_releases": bool(releases),
            "releases": releases,
            "homepage": meta.get("homepage", "") or "",
            "url": meta.get("html_url", ""),
        }
