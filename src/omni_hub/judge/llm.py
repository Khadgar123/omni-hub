"""LLM Judge stub (v0.23).

The LLMJudge prefers, in this order:

1. The local **ccLoad** gateway at ``OMNI_CCLOAD_BASE``
   (default ``http://localhost:8080``) — keeps the LLM call inside
   the existing api-management plane so cost / quota / cooldown all
   apply.  No third-party SDK needed.
2. The official **Anthropic SDK** if ``ANTHROPIC_API_KEY`` is set and
   the optional ``anthropic`` library is importable.
3. Falls back to :class:`HeuristicJudge` so callers never have to
   branch on availability.

The actual prompt is intentionally short — a single rubric-aware ask
that returns JSON.  Long Constitutional-AI prompts belong in
``agent-harness/integrations/llm-judge/`` as a pinned fork.

Hard constraint: this module does NOT add a runtime dependency.  The
``anthropic`` import is guarded.  When neither LLM channel is
available, evaluate() transparently delegates to HeuristicJudge.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .base import DimensionScore, Judge, JudgeRequest, JudgeVerdict, composite_score
from .heuristic import HeuristicJudge


CCLOAD_DEFAULT = "http://localhost:8080"
CCLOAD_TIMEOUT = 30


def _ccload_base() -> str:
    return (os.environ.get("OMNI_CCLOAD_BASE") or CCLOAD_DEFAULT).rstrip("/")


class LLMJudge:
    """Anthropic / ccLoad-backed Judge with HeuristicJudge fallback."""

    name = "llm"

    def __init__(
        self,
        *,
        model: str | None = None,
        ccload_base: str | None = None,
        timeout: int = CCLOAD_TIMEOUT,
        anthropic_api_key: str | None = None,
    ) -> None:
        self.model = model or os.environ.get("OMNI_LLM_JUDGE_MODEL", "claude-haiku-4-5-20251001")
        # Distinguish None (use env / default) from "" (explicit force-off).
        if ccload_base is None:
            self.ccload_base = _ccload_base().rstrip("/")
        else:
            self.ccload_base = ccload_base.rstrip("/")
        self.timeout = timeout
        if anthropic_api_key is None:
            self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        else:
            self.anthropic_api_key = anthropic_api_key
        self._fallback = HeuristicJudge()

    # ---- mode selection -----------------------------------------

    def _has_ccload(self) -> bool:
        return bool(self.ccload_base)

    def _has_anthropic_sdk(self) -> bool:
        if not self.anthropic_api_key:
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def available(self) -> bool:
        return self._has_ccload() or self._has_anthropic_sdk()

    # ---- evaluate -----------------------------------------------

    def evaluate(self, request: JudgeRequest) -> JudgeVerdict:
        if not self.available():
            verdict = self._fallback.evaluate(request)
            verdict.judge_name = self.name
            verdict.metadata["mode"] = "fallback-heuristic"
            verdict.metadata["reason"] = "no ccload + no anthropic sdk"
            return verdict

        prompt = _build_prompt(request)
        try:
            if self._has_ccload():
                raw = self._call_ccload(prompt)
                mode = "ccload"
            else:
                raw = self._call_anthropic_sdk(prompt)
                mode = "anthropic-sdk"
        except Exception as exc:                                # noqa: BLE001
            verdict = self._fallback.evaluate(request)
            verdict.judge_name = self.name
            verdict.metadata["mode"] = "fallback-heuristic"
            verdict.metadata["reason"] = f"{type(exc).__name__}: {exc}"
            return verdict

        parsed = _parse_verdict_json(raw)
        if parsed is None:
            verdict = self._fallback.evaluate(request)
            verdict.judge_name = self.name
            verdict.metadata["mode"] = "fallback-heuristic"
            verdict.metadata["reason"] = "non-JSON LLM output"
            return verdict

        dims = [
            DimensionScore(
                dimension=str(d.get("dimension", "")),
                score=max(0.0, min(1.0, float(d.get("score", 0.0)))),
                weight=float(d.get("weight", 1.0)),
                rationale=str(d.get("rationale", "")),
            )
            for d in parsed.get("dimensions", []) or []
            if isinstance(d, dict) and d.get("dimension")
        ]
        composite = float(parsed.get("composite", composite_score(dims)))
        return JudgeVerdict(
            judge_name=self.name,
            domain=request.domain,
            composite=composite,
            dimensions=dims,
            rationale=str(parsed.get("rationale", "")),
            trace_id=request.trace_id,
            metadata={"mode": mode, "model": self.model},
        )

    # ---- transports ---------------------------------------------

    def _call_ccload(self, prompt: str) -> str:
        url = f"{self.ccload_base}/v1/messages"
        payload = json.dumps({
            "model": self.model,
            "max_tokens": 800,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = resp.read()
        body = json.loads(data.decode("utf-8"))
        # Anthropic-compatible: content[0].text
        content = body.get("content") or []
        if content and isinstance(content[0], dict):
            return str(content[0].get("text", ""))
        return ""

    def _call_anthropic_sdk(self, prompt: str) -> str:
        import anthropic                                          # type: ignore

        client = anthropic.Anthropic(api_key=self.anthropic_api_key)
        message = client.messages.create(
            model=self.model,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        # Anthropic SDK shape
        return str(message.content[0].text) if message.content else ""


# ---------------------------------------------------------------------------
# Prompt + parsing helpers
# ---------------------------------------------------------------------------


def _build_prompt(request: JudgeRequest) -> str:
    rubric_md = "\n".join(
        f"- `{name}` (weight {weight:.2f})"
        for name, weight in (request.rubric or {}).items()
    ) or "- evidence_coverage / information_density / citation_support / style_fit / uncertainty_calibration"

    reference_block = ""
    if request.reference:
        reference_block = f"\n\n## Reference Context\n\n{request.reference[:4000]}"

    return f"""You are an evaluation judge.  Score the candidate answer against the rubric
below.  Domain: `{request.domain}`.  Return STRICT JSON only — no prose, no
markdown code fence — with the shape:

{{
  "composite": <float 0..1>,
  "dimensions": [
    {{"dimension": "evidence_coverage",      "score": <0..1>, "weight": <float>, "rationale": "..."}},
    {{"dimension": "information_density",    "score": <0..1>, "weight": <float>, "rationale": "..."}},
    {{"dimension": "citation_support",       "score": <0..1>, "weight": <float>, "rationale": "..."}},
    {{"dimension": "style_fit",              "score": <0..1>, "weight": <float>, "rationale": "..."}},
    {{"dimension": "uncertainty_calibration","score": <0..1>, "weight": <float>, "rationale": "..."}}
  ],
  "rationale": "1-2 sentence overall verdict."
}}

## Rubric

{rubric_md}

## Candidate Answer

{request.candidate[:8000]}{reference_block}
"""


def _parse_verdict_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    # Strip ``` fences if the model added them despite instructions.
    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1:] if first_nl != -1 else text
        if text.endswith("```"):
            text = text[: -3]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


__all__ = ["LLMJudge"]
