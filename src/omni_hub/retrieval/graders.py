"""CRAG-style graders for retrieval results.

A grader is just ``Callable[[query, RetrievalRecord], "correct" | "ambiguous" | "incorrect"]``.
``Cascade.retrieve(grader=...)`` calls it on every record after fusion;
records graded ``"incorrect"`` are dropped (and counted in
``CascadeResult.graded_dropped``).

Two stdlib-only graders are shipped:

* :class:`HeuristicGrader` — substring overlap + URL/title sanity checks.
  Fast, deterministic, no LLM call.  Catches obvious junk (empty
  snippets, paywall stubs, 4xx error pages parsed as text).

* :class:`LLMJudgeGrader` — wraps a user-supplied ``Callable[[prompt], str]``
  (e.g. a thin ccLoad client) so the harness's ``judge_ensemble`` model
  can act as the grader.  Constructor takes the callable so this module
  stays dependency-free.

This is the CRAG paper's "external retrieval evaluator" generalised:
heuristic when free, LLM when affordable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from .base import RetrievalRecord


Verdict = Literal["correct", "ambiguous", "incorrect"]

# Substrings that almost always indicate a useless result.  Conservative
# list — better to err on "ambiguous" than to wrongly drop a result.
_PAYWALL_TELLS = (
    "subscribe to continue",
    "you have reached your article limit",
    "please enable javascript",
    "access denied",
    "this content is for members only",
    "404 not found",
    "page not found",
)
_ERROR_TELLS = (
    "internal server error",
    "service unavailable",
    "503",
    "502 bad gateway",
)


@dataclass(slots=True)
class HeuristicGrader:
    """Lightweight grader: substring tells + token-overlap sanity."""

    min_overlap_ratio: float = 0.15      # of query tokens vs (title+snippet)
    min_snippet_chars: int = 20

    def __call__(self, query: str, record: RetrievalRecord) -> Verdict:
        text = f"{record.title}\n{record.snippet}".lower()
        if not text.strip():
            return "incorrect"
        for tell in _PAYWALL_TELLS:
            if tell in text:
                return "incorrect"
        for tell in _ERROR_TELLS:
            if tell in text:
                return "incorrect"
        if not record.url and not record.snippet:
            return "incorrect"
        if record.snippet and len(record.snippet) < self.min_snippet_chars:
            # Very short snippets are usually placeholder cards.
            return "ambiguous"

        query_tokens = {t for t in _tokenize(query) if len(t) > 2}
        if not query_tokens:
            return "ambiguous"
        text_tokens = set(_tokenize(text))
        overlap = len(query_tokens & text_tokens) / len(query_tokens)
        if overlap >= self.min_overlap_ratio:
            return "correct"
        if overlap > 0:
            return "ambiguous"
        return "incorrect"


@dataclass(slots=True)
class LLMJudgeGrader:
    """Defers verdict to an LLM call.  Provide ``model_call`` at construction.

    ``model_call(prompt) -> str`` is anything that takes a prompt and
    returns the model's text response.  The grader parses the first
    occurrence of ``correct`` / ``ambiguous`` / ``incorrect`` in the
    response.  Defaults to ``"ambiguous"`` if the model is unparseable.
    """

    model_call: Callable[[str], str]
    prompt_template: str = (
        "Grade whether this retrieved snippet is useful for the query.\n"
        "Reply with exactly one word: correct | ambiguous | incorrect\n\n"
        "Query: {query}\n"
        "Title: {title}\n"
        "URL: {url}\n"
        "Snippet: {snippet}\n"
    )

    def __call__(self, query: str, record: RetrievalRecord) -> Verdict:
        prompt = self.prompt_template.format(
            query=query,
            title=record.title,
            url=record.url,
            snippet=(record.snippet or "")[:600],
        )
        try:
            response = (self.model_call(prompt) or "").strip().lower()
        except Exception:                                  # noqa: BLE001
            return "ambiguous"
        for verdict in ("incorrect", "correct", "ambiguous"):
            if verdict in response:
                return verdict  # type: ignore[return-value]
        return "ambiguous"


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            current.append(ch)
        else:
            if current:
                out.append("".join(current))
                current = []
    if current:
        out.append("".join(current))
    return out
