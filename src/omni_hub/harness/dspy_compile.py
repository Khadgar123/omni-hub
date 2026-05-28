"""DSPy compile bridge with graceful fallback.

If ``dspy`` is importable we use the real BootstrapFewShot path; otherwise we
fall back to a deterministic "manual few-shot" assembly that still produces a
versioned prompt the rest of the harness can use.

Why a fallback?
---------------
- DSPy is fork-pinned but not yet a personal fork on Khadgar123.  The harness
  must keep working during the gap.
- The fallback path is deliberately simple: pick the top-N most-recent
  ``accepted`` preference records, render them as few-shot exemplars into a
  template, and write the resulting prompt to ``prompts/<domain>/<version>/``.
- When the real DSPy fork lands, swap the implementation; the public API
  (``compile`` returning ``CompileReport``) stays.
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from .preference import PreferenceRecord, PreferenceStore


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dspy_available() -> bool:
    try:
        import dspy  # noqa: F401
        return True
    except Exception:
        return False


@dataclass(slots=True)
class CompileReport:
    domain: str
    from_version: str
    to_version: str
    output_dir: str
    backend: str                       # "dspy" | "manual-fewshot"
    positive_used: int = 0
    negative_used: int = 0
    bootstrap_rounds: int = 0
    notes: str = ""
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compile(
    *,
    domain: str,
    from_version: str = "v0",
    output_root: Path | str = "prompts",
    preference_store: PreferenceStore | None = None,
    bootstrap_rounds: int = 8,
    max_positive: int = 12,
    max_negative: int = 6,
    backend: str = "auto",
) -> CompileReport:
    """Compile a new prompt program for ``domain`` from the preference store.

    Parameters
    ----------
    domain:
        Domain id (matches ``agent-harness/domain-profiles.json``).
    from_version:
        Previous prompt version this compile is derived from.
    output_root:
        Where the new prompt directory is written.
    preference_store:
        Defaults to ``PreferenceStore(".omni/preference")``.
    bootstrap_rounds:
        DSPy ``BootstrapFewShot`` rounds; ignored by the manual fallback.
    backend:
        ``"auto"`` (default — DSPy if importable, else manual), ``"dspy"``
        to require DSPy and fail otherwise, or ``"manual"`` to force the
        local renderer.
    """

    store = preference_store or PreferenceStore()
    positives = store.export(
        domain,
        include_decisions=("accepted", "edited"),
        max_records=max_positive,
    )
    negatives = store.export(
        domain,
        include_decisions=("rejected",),
        max_records=max_negative,
    )

    use_dspy = backend == "dspy" or (backend == "auto" and _dspy_available())
    if backend == "dspy" and not _dspy_available():
        raise RuntimeError(
            "backend='dspy' requested but the dspy package is not importable. "
            "Run scripts/add_pending_harness_forks.sh dspy and install it."
        )

    next_version = _bump_version(from_version)
    out_dir = Path(output_root) / domain / next_version
    out_dir.mkdir(parents=True, exist_ok=True)

    if use_dspy:  # pragma: no cover — exercised once DSPy is installed
        notes = _compile_dspy(out_dir, positives, negatives, bootstrap_rounds)
        backend_used = "dspy"
    else:
        notes = _compile_manual(out_dir, domain, positives, negatives)
        backend_used = "manual-fewshot"

    report = CompileReport(
        domain=domain,
        from_version=from_version,
        to_version=next_version,
        output_dir=str(out_dir),
        backend=backend_used,
        positive_used=len(positives),
        negative_used=len(negatives),
        bootstrap_rounds=bootstrap_rounds if use_dspy else 0,
        notes=notes,
    )
    (out_dir / "compile_report.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


_PROMPT_TEMPLATE = textwrap.dedent("""\
# Compiled prompt — domain={domain}, version={version}

You are working under the {domain} domain profile.  Follow these rules:

1. Ground every claim in retrieved sources.  Cite using `[id]` or
   `(Author Year)`.
2. Do NOT use the phrases the human reviewers have flagged as low-signal.
3. Match the style of the accepted exemplars below.

## Positive exemplars ({positive_count})

{positive_block}

## Negative exemplars — DO NOT IMITATE ({negative_count})

{negative_block}

## Reviewer feedback patterns

{reason_summary}
""")


def _compile_manual(
    out_dir: Path,
    domain: str,
    positives: Sequence[PreferenceRecord],
    negatives: Sequence[PreferenceRecord],
) -> str:
    def _exemplar_block(records: Sequence[PreferenceRecord], label: str) -> str:
        if not records:
            return f"_(no {label} exemplars yet — add some via `harness-preference-add`)_"
        chunks = []
        for rec in records:
            spans = rec.accepted_spans if label == "positive" else rec.rejected_spans
            body = rec.candidate_text.strip() or "\n".join(spans).strip()
            body = body[:1200]
            chunks.append(f"### {rec.record_id[:8]}  ({rec.created_at})\n\n{body}\n")
        return "\n".join(chunks)

    def _reason_summary(records: Sequence[PreferenceRecord]) -> str:
        reasons = [r.reason.strip() for r in records if r.reason.strip()]
        if not reasons:
            return "_(no reviewer reasons collected yet)_"
        # Cluster trivially by first 6 words
        seen: dict[str, int] = {}
        for r in reasons:
            key = " ".join(r.lower().split()[:6])
            seen[key] = seen.get(key, 0) + 1
        ranked = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:6]
        return "\n".join(f"- ({n}×) {key}" for key, n in ranked)

    body = _PROMPT_TEMPLATE.format(
        domain=domain,
        version=out_dir.name,
        positive_count=len(positives),
        negative_count=len(negatives),
        positive_block=_exemplar_block(positives, "positive"),
        negative_block=_exemplar_block(negatives, "negative"),
        reason_summary=_reason_summary(list(positives) + list(negatives)),
    )

    (out_dir / "system_prompt.md").write_text(body, encoding="utf-8")
    return (
        f"manual-fewshot: rendered {len(positives)} positive and "
        f"{len(negatives)} negative exemplars into system_prompt.md"
    )


def _compile_dspy(  # pragma: no cover
    out_dir: Path,
    positives: Sequence[PreferenceRecord],
    negatives: Sequence[PreferenceRecord],
    rounds: int,
) -> str:
    """Real DSPy path.  Stubbed for now — implementation lands once the DSPy
    fork is added as a submodule and we know the import surface for the
    pinned commit."""

    import dspy  # type: ignore[import-not-found]
    # Intentionally lightweight: just record what we'd run.  When the real
    # fork is in place, replace this with BootstrapFewShot / MIPROv2.
    (out_dir / "dspy_plan.json").write_text(
        json.dumps(
            {
                "rounds": rounds,
                "positives_n": len(positives),
                "negatives_n": len(negatives),
                "todo": "Wire BootstrapFewShot + MIPROv2 once dspy fork is pinned.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return "dspy backend invoked; full compile pending fork wire-up"


# ---------------------------------------------------------------------------
# Version bumper
# ---------------------------------------------------------------------------


_VERSION_RE = re.compile(r"^v(\d+)(?:\.(\d+))?$")


def _bump_version(version: str) -> str:
    match = _VERSION_RE.match(version.strip().lower())
    if not match:
        return f"{version}-next"
    major = int(match.group(1)) + 1
    return f"v{major}"
