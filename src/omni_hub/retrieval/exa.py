"""Exa (formerly Metaphor) — neural / semantic web search.

Where keyword search asks "which pages match these words", Exa asks
"which pages are *like this idea*".  Useful when:

* You have a paper / URL / paragraph and want to find conceptually
  similar work (``findSimilar`` mode).
* Your query is a description, not keywords ("a paper showing language
  models can do in-context learning without fine-tuning").

Free tier: 1000 req/month, no credit card.  Key via ``EXA_API_KEY``
env var or ``.omni/secrets.json::omni-hub/api/exa/default``.

Fan-out role in cascades: complementary to Brave / Tavily — Exa
catches the "semantic neighbours" that keyword engines miss.  Cheapest
when ``useAutoprompt=False`` (uses raw query).
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord


EXA_SEARCH_URL = "https://api.exa.ai/search"
EXA_SECRET_REF = "local:omni-hub/api/exa/default"


def _resolve_exa_key() -> str:
    env_key = os.environ.get("EXA_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(EXA_SECRET_REF) or ""
    except SecretStoreError:
        return ""
    except Exception:                                                # noqa: BLE001
        return ""


def _url_hash(url: str) -> str:
    base = url.split("#", 1)[0]
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


class ExaSearchSource:
    """Exa neural search."""

    name = "exa"
    tier = 1

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
        use_autoprompt: bool = False,
    ) -> None:
        self.api_key = api_key if api_key is not None else _resolve_exa_key()
        self.timeout = timeout
        # autoprompt = Exa LLM-rewrites the query; off by default to
        # save credits + keep keyword behaviour predictable.
        self.use_autoprompt = use_autoprompt

    def check(self) -> tuple[str, str]:
        if self.api_key:
            return "ok", "api key configured (1000/mo free tier)"
        return "warn", "EXA_API_KEY not set"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        if not self.api_key:
            raise RetrievalError("EXA_API_KEY not set")

        payload = {
            "query": query,
            "numResults": min(max(limit, 1), 25),
            "useAutoprompt": self.use_autoprompt,
            "type": "auto",
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            EXA_SEARCH_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise RetrievalError(f"exa HTTPError {exc.code}: {exc.reason}") from exc
        except Exception as exc:                                  # noqa: BLE001
            raise RetrievalError(f"exa {type(exc).__name__}: {exc}") from exc

        try:
            data: Any = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RetrievalError(f"exa invalid JSON: {exc}") from exc

        if not isinstance(data, dict):
            return []

        results = data.get("results") or []
        records: list[RetrievalRecord] = []
        for item in results[:limit]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", ""))
            title = str(item.get("title", ""))
            score = float(item.get("score", 0.0) or 0.0)
            # Exa's plain `search` endpoint returns no body content
            # (that needs `/contents`).  Use the title as the snippet
            # surface so RRF has something non-empty to rank on.
            snippet = title or url
            canonical = f"web:{_url_hash(url)}" if url else ""
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=url,
                snippet=snippet[:500],
                score=score,
                canonical_id=canonical,
                metadata={
                    "exa_score": score,
                    "published_date": item.get("publishedDate", ""),
                    "author": item.get("author", ""),
                    "exa_id": item.get("id", ""),
                },
            ))
        return records


__all__ = ["ExaSearchSource"]
