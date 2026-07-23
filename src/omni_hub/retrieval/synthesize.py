"""Cascade synthesis layer — records → coherent cited answer.

The v0.44 benchmark exposed the core quality gap: the cascade *finds*
relevant records (100% query hit-rate) but the raw record dump scored
only 0.258 on the DeepSeek LLMJudge — because a pile of snippets is not
an answer.  Every judge rationale said the same thing: "fragmented
snippets, no coherent synthesis, no citations".

This module closes that gap.  It takes the top-N fused records and asks
the LLM (DeepSeek by default, same channel as LLMJudge) to write a
tight, **citation-grounded** answer:

* 200-400 words, every factual claim tagged ``[n]`` to a source record
* explicit uncertainty when the records disagree or are thin
* NO fabrication — only what the records support
* a structured ``citations`` list mapping ``[n]`` → record

Design constraints (match the rest of the retrieval package):
* stdlib-only transport (urllib), no SDK dependency
* fail-soft: if no LLM channel is configured, returns a deterministic
  ``concat`` fallback so callers never crash
* same DeepSeek key resolution as ``judge.llm`` (env → secrets store)
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:                                                 # pragma: no cover
    from .base import RetrievalRecord


DEEPSEEK_DEFAULT_BASE = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"
DEEPSEEK_SECRET_REF = "local:omni-hub/api/deepseek/default"

CCLOAD_DEFAULT = "http://localhost:8080"
SYNTH_TIMEOUT_S = 60
DEFAULT_MAX_RECORDS = 8
DEFAULT_SNIPPET_CHARS = 600


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Citation:
    """One ``[n]`` reference in the synthesized answer."""

    n: int
    source: str
    title: str
    url: str = ""
    canonical_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SynthesisResult:
    """Output of :func:`synthesize_answer`."""

    query: str
    domain: str
    answer: str
    citations: list[Citation] = field(default_factory=list)
    mode: str = ""                       # "deepseek-direct" | "ccload" | "fallback-concat" | "no-records"
    model: str = ""
    used_record_count: int = 0
    cited_n: list[int] = field(default_factory=list)   # which [n] actually appeared in the answer

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["citations"] = [c.to_dict() for c in self.citations]
        return d


# ---------------------------------------------------------------------------
# Key / endpoint resolution (mirrors judge.llm)
# ---------------------------------------------------------------------------


def _resolve_deepseek_key() -> str:
    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(DEEPSEEK_SECRET_REF) or ""
    except SecretStoreError:
        return ""
    except Exception:                                            # noqa: BLE001
        return ""


def _ccload_base() -> str:
    return (os.environ.get("OMNI_CCLOAD_BASE") or "").rstrip("/")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def synthesize_answer(
    query: str,
    records: list["RetrievalRecord"],
    *,
    domain: str = "",
    max_records: int = DEFAULT_MAX_RECORDS,
    model: str | None = None,
    deepseek_api_key: str | None = None,
    deepseek_base: str | None = None,
    ccload_base: str | None = None,
    timeout: int = SYNTH_TIMEOUT_S,
) -> SynthesisResult:
    """Synthesize a cited answer from ``records``.

    Channel preference: ccLoad gateway (if ``OMNI_CCLOAD_BASE`` /
    ``ccload_base`` set) → DeepSeek direct → deterministic concat
    fallback.  Never raises — failures degrade to the concat fallback so
    the cascade stays robust.
    """

    if not query.strip():
        return SynthesisResult(query=query, domain=domain, answer="",
                               mode="no-records", used_record_count=0)
    if not records:
        return SynthesisResult(query=query, domain=domain,
                               answer="_(no records retrieved for this query)_",
                               mode="no-records", used_record_count=0)

    used = records[:max_records]
    citations = [
        Citation(
            n=i,
            source=getattr(r, "source", ""),
            title=(getattr(r, "title", "") or "")[:200],
            url=getattr(r, "url", "") or "",
            canonical_id=getattr(r, "canonical_id", "") or "",
        )
        for i, r in enumerate(used, start=1)
    ]

    model_name = model or os.environ.get("OMNI_SYNTH_MODEL", DEEPSEEK_DEFAULT_MODEL)
    prompt = _build_prompt(query, used, domain=domain)

    # Channel selection
    cc_base = ccload_base if ccload_base is not None else _ccload_base()
    ds_key = deepseek_api_key if deepseek_api_key is not None else _resolve_deepseek_key()
    ds_base = (deepseek_base or os.environ.get("OMNI_DEEPSEEK_BASE") or DEEPSEEK_DEFAULT_BASE).rstrip("/")

    answer = ""
    mode = "fallback-concat"
    try:
        if cc_base:
            answer = _call_ccload(cc_base, model_name, prompt, timeout)
            mode = "ccload"
        elif ds_key:
            answer = _call_deepseek(ds_base, ds_key, model_name, prompt, timeout)
            mode = "deepseek-direct"
    except Exception:                                            # noqa: BLE001
        answer = ""                                              # fall through to concat

    if not answer.strip():
        answer = _concat_fallback(query, used)
        mode = "fallback-concat"

    cited_n = _extract_cited_n(answer, max_n=len(used))
    return SynthesisResult(
        query=query,
        domain=domain,
        answer=answer.strip(),
        citations=citations,
        mode=mode,
        model=model_name if mode != "fallback-concat" else "",
        used_record_count=len(used),
        cited_n=cited_n,
    )


# ---------------------------------------------------------------------------
# Prompt + transports
# ---------------------------------------------------------------------------


def _build_prompt(query: str, records: list["RetrievalRecord"], *, domain: str) -> str:
    blocks: list[str] = []
    for i, r in enumerate(records, start=1):
        title = (getattr(r, "title", "") or "").strip()
        snippet = (getattr(r, "snippet", "") or "").strip()[:DEFAULT_SNIPPET_CHARS]
        src = getattr(r, "source", "")
        url = getattr(r, "url", "") or ""
        blocks.append(f"[{i}] source={src} | {title}\n{snippet}\n(url: {url})")
    sources_block = "\n\n".join(blocks)
    domain_hint = f" (domain: {domain})" if domain else ""

    return f"""You are a precise research assistant.  Using ONLY the numbered source
records below, write a tight, well-structured answer to the user's
query{domain_hint}.

Hard rules:
- Ground every factual claim with an inline citation like [1], [3].
- Use ONLY information present in the records.  Do NOT invent facts,
  numbers, dates, or names that are not in the records.
- If the records are thin, conflicting, or do not actually answer the
  query, say so explicitly and state what IS supported.
- Prefer recent + authoritative records.  If two records disagree,
  note the disagreement with both citations.
- 150-400 words.  No preamble like "Based on the sources"; just answer.
- End with a one-line "Confidence:" note (high / medium / low) + why.

## User query

{query}

## Source records

{sources_block}

## Answer
"""


def _call_deepseek(base: str, key: str, model: str, prompt: str, timeout: int) -> str:
    url = f"{base}/v1/chat/completions"
    payload = json.dumps({
        "model": model,
        "max_tokens": 900,
        "temperature": 0.3,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    choices = body.get("choices") or []
    if choices and isinstance(choices[0], dict):
        return str((choices[0].get("message") or {}).get("content", ""))
    return ""


def _call_ccload(base: str, model: str, prompt: str, timeout: int) -> str:
    url = f"{base}/v1/messages"
    payload = json.dumps({
        "model": model,
        "max_tokens": 900,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = body.get("content") or []
    if content and isinstance(content[0], dict):
        return str(content[0].get("text", ""))
    # ccLoad may proxy OpenAI-shaped responses too
    choices = body.get("choices") or []
    if choices and isinstance(choices[0], dict):
        return str((choices[0].get("message") or {}).get("content", ""))
    return ""


def _concat_fallback(query: str, records: list["RetrievalRecord"]) -> str:
    """Deterministic, citation-numbered concat when no LLM is available.

    This is intentionally honest: it labels itself as an un-synthesized
    digest so downstream judges (and humans) aren't misled into thinking
    an LLM wrote it.
    """

    lines = [f"_(LLM synthesis unavailable — showing top {len(records)} "
             f"un-synthesized records for: {query})_", ""]
    for i, r in enumerate(records, start=1):
        title = (getattr(r, "title", "") or "").strip()
        snippet = (getattr(r, "snippet", "") or "").strip()[:240]
        lines.append(f"[{i}] {title}\n    {snippet}")
    return "\n".join(lines)


def _extract_cited_n(answer: str, *, max_n: int) -> list[int]:
    found = sorted({int(m) for m in re.findall(r"\[(\d+)\]", answer)})
    return [n for n in found if 1 <= n <= max_n]


__all__ = ["Citation", "SynthesisResult", "synthesize_answer"]
