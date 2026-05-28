"""Broad web-search connectors.

Three providers, each gated on its own key with ``.omni/secrets.json``
fallback (same pattern as DeepSeek / S2 — no env var required after
``store_api_key('api/<name>/default', ...)``).

* **BraveSearchSource** — Brave's own index (~25B pages), 2k/month free,
  20M/month for $3.  Use when you want a Google-independent index.

* **TavilySearchSource** — AI-Agent-oriented search wrapper.  Returns
  *cleaned page content*, optional LLM-generated answer.  1k/month free,
  no credit card required.  Best default for RAG.

Cascade fan-out is parallel — all three can register and the RRF fusion
picks whichever returns first / best.  Fail-soft when their keys are
unset.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json
from .health import env_var_probe


BRAVE_WEB_SEARCH = "https://api.search.brave.com/res/v1/web/search"
TAVILY_API = "https://api.tavily.com/search"

BRAVE_SECRET_REF = "local:omni-hub/api/brave/default"
TAVILY_SECRET_REF = "local:omni-hub/api/tavily/default"


def _resolve_local_secret(ref: str) -> str:
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(ref) or ""
    except SecretStoreError:
        return ""
    except Exception:                                                # noqa: BLE001
        return ""


def _resolve_brave_key() -> str:
    env_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    return env_key or _resolve_local_secret(BRAVE_SECRET_REF)


def _resolve_tavily_key() -> str:
    env_key = os.environ.get("TAVILY_API_KEY", "").strip()
    return env_key or _resolve_local_secret(TAVILY_SECRET_REF)


class BraveSearchSource:
    """Brave Search Web API.  Key via env or ``.omni/secrets.json``."""

    name = "brave_search"
    tier = 1

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key if api_key is not None else _resolve_brave_key()
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.api_key:
            return "ok", "api key configured"
        return env_var_probe("BRAVE_SEARCH_API_KEY")

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
            raise RetrievalError("BRAVE_SEARCH_API_KEY not set")

        data = http_get_json(
            BRAVE_WEB_SEARCH,
            params={
                "q": query,
                "count": str(min(max(limit, 1), 20)),
                "text_decorations": "false",
            },
            headers={
                "X-Subscription-Token": self.api_key,
                "Accept": "application/json",
            },
            timeout=self.timeout,
        )
        web = data.get("web", {}) if isinstance(data, dict) else {}
        results = web.get("results", []) if isinstance(web, dict) else []

        records: list[RetrievalRecord] = []
        for item in results[:limit]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", ""))
            title = str(item.get("title", ""))
            snippet = str(item.get("description", "") or item.get("snippet", ""))
            canonical = f"web:{_url_hash(url)}" if url else ""
            profile = item.get("profile") if isinstance(item.get("profile"), dict) else {}
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=url,
                snippet=snippet[:500],
                score=0.0,
                canonical_id=canonical,
                metadata={
                    "age": item.get("age", ""),
                    "source_name": profile.get("name", ""),
                    "family_friendly": item.get("family_friendly", None),
                },
            ))
        return records


class TavilySearchSource:
    """Tavily Search API.  Cleaned content + optional LLM answer.

    Free tier: 1000 req/month, no credit card.  Key via env
    ``TAVILY_API_KEY`` or ``.omni/secrets.json::omni-hub/api/tavily/default``.

    Best default for RAG: ``content`` field is already extracted +
    cleaned (no boilerplate), and ``answer`` is an LLM-generated brief
    that can short-circuit further retrieval for simple factoids.
    """

    name = "tavily"
    tier = 1

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key if api_key is not None else _resolve_tavily_key()
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.api_key:
            return "ok", "api key configured (1000/mo free tier)"
        return "warn", "TAVILY_API_KEY not set"

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
            raise RetrievalError("TAVILY_API_KEY not set")

        # Pick depth based on cascade fan-out budget: "basic" returns
        # fast (~0.5s, fewer tokens), "advanced" multi-hops (slower,
        # higher recall).  Use basic by default — the cascade is itself
        # multi-source, so we don't need Tavily to also multi-hop.
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": min(max(limit, 1), 20),
            "search_depth": "basic",
            "include_answer": True,
            "include_raw_content": False,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            TAVILY_API,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise RetrievalError(f"tavily HTTPError {exc.code}: {exc.reason}") from exc
        except Exception as exc:                                  # noqa: BLE001
            raise RetrievalError(f"tavily {type(exc).__name__}: {exc}") from exc

        try:
            data: Any = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RetrievalError(f"tavily invalid JSON: {exc}") from exc

        if not isinstance(data, dict):
            return []

        results = data.get("results") or []
        answer = (data.get("answer") or "").strip()
        records: list[RetrievalRecord] = []

        # If Tavily generated an answer summary, surface it as the first
        # record so the cascade fusion sees it ranked highly.
        if answer:
            records.append(RetrievalRecord(
                source=self.name,
                title=f"Tavily answer for: {query[:80]}",
                url="",
                snippet=answer[:500],
                score=1.0,
                canonical_id=f"tavily-answer:{hashlib.sha1(answer.encode()).hexdigest()[:16]}",
                metadata={"kind": "llm_answer", "raw_query": query},
            ))

        for item in results[:limit]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", ""))
            title = str(item.get("title", ""))
            # Tavily's "content" is cleaned page text — much richer than
            # Brave's "description" snippet.  Cap at 800 chars for the
            # cascade record; full content is in metadata for callers
            # who need it.
            content = str(item.get("content", ""))
            score = float(item.get("score", 0.0) or 0.0)
            canonical = f"web:{_url_hash(url)}" if url else ""
            records.append(RetrievalRecord(
                source=self.name,
                title=title,
                url=url,
                snippet=content[:800],
                score=score,
                canonical_id=canonical,
                metadata={
                    "tavily_score": score,
                    "published_date": item.get("published_date", ""),
                    "content_chars": len(content),
                },
            ))
        return records


def _url_hash(url: str) -> str:
    base = url.split("#", 1)[0]
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
