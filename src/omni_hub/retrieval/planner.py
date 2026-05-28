"""Plan-then-search — let an LLM pick sources + rewrite the query.

Gemini Deep Research + Perplexity Research + APEX-Searcher all converge
on a single trick: before fanning out across N sources, do **one** small
LLM call that picks a subset and rewrites the query.  ROI:

* Quota saved (don't hit GDELT for a pure papers question)
* Recall improved (rewrite "claude agent SDK" → "Anthropic Claude Agent SDK
  Python TypeScript")
* Optional sub-query decomposition for hard questions

This module is stdlib-only — the LLM call is passed in as a
``Callable[[prompt], str]`` so callers wire their own (ccLoad, harness
ensemble, OpenAI compat, …).  Without a model_call, ``plan()`` returns
a no-op plan.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable


DEFAULT_PROMPT = """\
You are a retrieval planner.  Given a user query, pick which sources to
consult and (optionally) rewrite the query for better recall.  Respond
in JSON only.

User query: {query}
Domain profile: {domain}
Available sources: {sources}

Respond with this exact JSON shape:
{{
  "rewritten_query": "<the same query or a better version, ≤120 chars>",
  "sub_queries": ["<optional 0-3 sub-queries to broaden coverage>"],
  "sources": ["<pick 1-N from available_sources>"],
  "deep": false
}}

Pick fewer sources when the query is narrow (one specific paper).
Pick more sources for cross-domain or recent-events queries.
"""


@dataclass(slots=True)
class RetrievalPlan:
    rewritten_query: str
    sub_queries: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    deep: bool = False
    raw_response: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "rewritten_query": self.rewritten_query,
            "sub_queries": list(self.sub_queries),
            "sources": list(self.sources),
            "deep": self.deep,
        }


def plan(
    query: str,
    *,
    domain: str = "default",
    available_sources: list[str],
    model_call: Callable[[str], str] | None = None,
    prompt_template: str = DEFAULT_PROMPT,
    max_sources: int = 5,
) -> RetrievalPlan:
    """Build a :class:`RetrievalPlan` for ``query``.

    With no ``model_call`` (or any failure parsing), returns a no-op plan
    (``rewritten_query == query``, ``sources = available_sources``).
    """

    fallback = RetrievalPlan(
        rewritten_query=query,
        sources=list(available_sources),
    )

    if model_call is None:
        return fallback

    prompt = prompt_template.format(
        query=query,
        domain=domain,
        sources=", ".join(available_sources),
    )
    try:
        response = model_call(prompt) or ""
    except Exception:                                       # noqa: BLE001
        return fallback

    parsed = _extract_json(response)
    if not parsed:
        return fallback

    rewritten = str(parsed.get("rewritten_query", "")).strip() or query
    sub_queries = [
        str(q).strip()
        for q in (parsed.get("sub_queries") or [])
        if isinstance(q, str) and q.strip()
    ][:3]
    raw_sources = parsed.get("sources") or []
    if isinstance(raw_sources, str):
        raw_sources = [s.strip() for s in raw_sources.split(",")]
    # Whitelist: only sources we actually have are kept.
    available_set = set(available_sources)
    sources = [s for s in raw_sources if s in available_set][:max_sources]
    if not sources:
        sources = list(available_sources)
    deep = bool(parsed.get("deep", False))

    return RetrievalPlan(
        rewritten_query=rewritten,
        sub_queries=sub_queries,
        sources=sources,
        deep=deep,
        raw_response=response,
    )


def _extract_json(text: str) -> dict | None:
    """Find the first balanced ``{...}`` block in ``text`` and parse it.

    Models often wrap JSON in markdown fences or chat-style padding;
    this is a tiny scanner so we don't add a dependency for one regex.
    """

    text = text.strip()
    # Strip ``` fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None
