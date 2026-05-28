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


# ---------------------------------------------------------------------------
# SKILL.md compilation (Anthropic Skills spec) — the flywheel closure point
# that turns ``.omni/preference/<domain>.jsonl`` into a loadable
# ``.agents/skills/<skill-id>/SKILL.md`` so Claude Code / Codex / other
# agent runtimes pick up the accepted-span style next session.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SkillSignature:
    """v0.18-K: DSPy-style typed I/O signature for a compiled skill.

    Carries the input/output schema so a Skills-aware runtime (Claude
    Code / Codex / Cursor) can wire the compiled skill as a typed tool.
    """

    skill_id: str
    domain: str
    inputs: dict = field(default_factory=lambda: {
        "task": {"type": "string", "description": "task description"},
        "context": {"type": "string", "description": "compiled context pack"},
    })
    outputs: dict = field(default_factory=lambda: {
        "answer": {"type": "string", "description": "ground-truth answer with citations"},
    })
    schema_version: str = "v0.18"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class SkillMetric:
    """v0.18-K: objective the optimizer is tuning."""

    name: str = "preference_accuracy"
    description: str = "rate of human accept on next sample after compile"
    higher_is_better: bool = True
    last_score: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class SkillModule:
    """v0.18-K: composition unit (think DSPy Module).  For now we have
    one Module per skill (system prompt + few-shot demos);  v0.19+ can
    introduce ChainOfThought / ReAct subclasses without touching the
    Signature/Metric/Optimizer/CompiledSkill surface."""

    module_type: str = "PromptedFewShot"
    demos_hash: str = ""
    prompt_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class SkillOptimizer:
    """v0.18-K: optimizer that compiled the current artifact.

    ``backend`` mirrors CompileReport.backend (manual-fewshot / dspy /
    gepa);  ``trace_count`` is how many preference traces fed the
    optimizer (positive + negative)."""

    backend: str = "manual-fewshot"
    trace_count: int = 0
    parameters: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class CompiledSkill:
    """v0.18-K: the artifact subtype emitted by compile_skill_md.

    Pulls Signature/Module/Metric/Optimizer into one typed envelope so
    downstream consumers (SkillRegistry / MCP exposure / DSPy
    integration later) get a stable contract.
    """

    skill_id: str
    domain: str
    target_path: str
    signature: SkillSignature
    module: SkillModule
    metric: SkillMetric
    optimizer: SkillOptimizer
    bytes_written: int
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "domain": self.domain,
            "target_path": self.target_path,
            "signature": self.signature.to_dict(),
            "module": self.module.to_dict(),
            "metric": self.metric.to_dict(),
            "optimizer": self.optimizer.to_dict(),
            "bytes_written": self.bytes_written,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class SkillCompileReport:
    skill_id: str
    domain: str
    target_path: str
    prompt_version: str
    backend: str
    positive_used: int
    negative_used: int
    bytes_written: int
    skill_sync: dict = field(default_factory=dict)            # v0.17-D
    compiled_skill: dict = field(default_factory=dict)        # v0.18-K
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        return asdict(self)


_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def compile_skill_md(
    *,
    domain: str,
    skill_id: str = "",
    description: str = "",
    output_root: Path | str = ".agents/skills",
    prompt_compile_report: CompileReport | None = None,
    preference_store: PreferenceStore | None = None,
    max_positive: int = 10,
    max_negative: int = 4,
    backend: str = "manual",
) -> SkillCompileReport:
    """Compile accepted/rejected preference spans into a SKILL.md file.

    Follows the Anthropic Skills frontmatter contract:

        ---
        name: <kebab-case, ≤64 chars>
        description: <≤1024 chars; both WHAT the skill does AND WHEN to use it>
        ---

    Body section is a curated few-shot brief: positive exemplars
    (accepted spans), explicit anti-patterns (rejected spans), and the
    domain sub-schema authoritative-source list when available.  No
    Anthropic / Claude / DSPy import is required — fully stdlib.
    """

    skill_id = (skill_id or f"{domain.replace('_', '-')}-wiki").strip().lower()
    if not _SKILL_NAME_RE.match(skill_id):
        raise ValueError(
            f"skill_id {skill_id!r} must be kebab-case [a-z][a-z0-9-]{{0,63}}"
        )

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

    if prompt_compile_report is None:
        prompt_compile_report = compile(
            domain=domain,
            preference_store=store,
            max_positive=max_positive,
            max_negative=max_negative,
            backend=backend,
        )

    description = description.strip() or _default_description(
        domain=domain, positives=len(positives), negatives=len(negatives)
    )
    if len(description) > 1024:
        description = description[:1021].rstrip() + "..."

    target_dir = Path(output_root) / skill_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "SKILL.md"

    body = _render_skill_body(
        skill_id=skill_id,
        domain=domain,
        description=description,
        prompt_compile_report=prompt_compile_report,
        positives=positives,
        negatives=negatives,
    )
    target.write_text(body, encoding="utf-8")

    # v0.17-D: auto-sync the freshly-emitted SKILL.md into
    # `registry/skills.json` so `skill-list` and SkillRegistry.list()
    # surface it without a separate operator step.  Best-effort — the
    # compile already succeeded by this point; sync failures are
    # surfaced via the report but don't raise.
    sync_result: dict = {}
    try:
        sync_result = _auto_sync_after_compile(output_root)
    except Exception as exc:                                    # noqa: BLE001
        sync_result = {"error": f"{type(exc).__name__}: {exc}"}

    # v0.18-K: emit a typed CompiledSkill artifact alongside the markdown.
    import hashlib as _hashlib
    body_hash = _hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    demos_hash = _hashlib.sha256(
        ("|".join(p.candidate_text for p in positives)).encode("utf-8")
    ).hexdigest()[:16]
    compiled = CompiledSkill(
        skill_id=skill_id,
        domain=domain,
        target_path=str(target),
        signature=SkillSignature(skill_id=skill_id, domain=domain),
        module=SkillModule(
            module_type="PromptedFewShot",
            demos_hash=demos_hash,
            prompt_hash=body_hash,
        ),
        metric=SkillMetric(),
        optimizer=SkillOptimizer(
            backend=prompt_compile_report.backend,
            trace_count=len(positives) + len(negatives),
            parameters={"max_positive": max_positive, "max_negative": max_negative},
        ),
        bytes_written=target.stat().st_size,
    )

    return SkillCompileReport(
        skill_id=skill_id,
        domain=domain,
        target_path=str(target),
        prompt_version=prompt_compile_report.to_version,
        backend=prompt_compile_report.backend,
        positive_used=len(positives),
        negative_used=len(negatives),
        bytes_written=target.stat().st_size,
        skill_sync=sync_result,
        compiled_skill=compiled.to_dict(),
    )


def _auto_sync_after_compile(output_root: Path | str) -> dict:
    """Find the workspace root from ``output_root`` and run skill-sync.

    ``output_root`` is expected to be ``<workspace>/.agents/skills`` or a
    test-mode temp dir.  We climb until we find the workspace marker
    (``pyproject.toml`` or ``.git``) and run sync there.  When neither
    marker is found we sync against ``output_root.parent.parent``
    (matching the canonical layout) without complaining.
    """

    from ..skill_sync import sync_skills

    out = Path(output_root).resolve()
    workspace = out
    for ancestor in [out, *out.parents]:
        if (ancestor / "pyproject.toml").exists() or (ancestor / ".git").exists():
            workspace = ancestor
            break
    else:
        # No marker — fall back to two-levels-up from output_root
        # (`.agents/skills` -> repo root).
        try:
            workspace = out.parents[1]
        except IndexError:
            workspace = out
    return sync_skills(workspace, apply=True)


def _default_description(*, domain: str, positives: int, negatives: int) -> str:
    """Anthropic Skills convention: third-person, declare what + when.

    Description must be a single self-contained sentence (skills get
    loaded by their description before SKILL.md body is read).
    """

    return (
        f"Compile a domain-{domain} wiki page or claim from accepted span "
        f"exemplars and known anti-patterns. Use when the user asks to write, "
        f"propose, ingest, or revise content for the {domain} domain wiki "
        f"(synthesised from {positives} accepted and {negatives} rejected "
        f"local preference spans)."
    )


def _render_skill_body(
    *,
    skill_id: str,
    domain: str,
    description: str,
    prompt_compile_report: CompileReport,
    positives: Sequence[PreferenceRecord],
    negatives: Sequence[PreferenceRecord],
) -> str:
    """Render the SKILL.md body.  Frontmatter follows Anthropic spec; the
    body is a progressive-disclosure brief the agent reads on trigger."""

    # Pull authoritative sources for the domain when available; falls
    # silently to "(none registered)" otherwise.
    try:
        from ..domain_schemas import DOMAIN_SCHEMAS
        schema = DOMAIN_SCHEMAS.get(domain) or DOMAIN_SCHEMAS.get(domain.replace("-", "_"))
        sources = list(schema.authoritative_sources) if schema else []
        stale_after_days = schema.stale_after_days if schema else 30
    except Exception:                                           # noqa: BLE001
        sources = []
        stale_after_days = 30

    def _exemplar_block(records: Sequence[PreferenceRecord], label: str) -> str:
        if not records:
            return f"_(no {label} exemplars yet)_"
        chunks: list[str] = []
        for rec in records:
            text = (rec.candidate_text or "").strip()
            if not text:
                spans = rec.accepted_spans if label == "positive" else rec.rejected_spans
                text = "\n".join(s for s in spans if s).strip()
            if not text:
                continue
            # Cap each exemplar to keep the loaded SKILL.md under a few kB.
            text = text[:1500]
            chunks.append(
                f"### {rec.record_id[:8]}  ·  {rec.created_at[:10]}  ·  reviewer={rec.reviewer}\n\n"
                f"{text}\n"
            )
        return "\n".join(chunks) if chunks else f"_(no {label} exemplars with body text)_"

    lines = [
        "---",
        f"name: {skill_id}",
        f"description: {description}",
        "---",
        "",
        f"# {skill_id}",
        "",
        f"Compiled from `.omni/preference/{domain}.jsonl` at "
        f"`{prompt_compile_report.created_at}` "
        f"(backend={prompt_compile_report.backend}, "
        f"version={prompt_compile_report.to_version}).  "
        f"Regenerate with `harness-compile-skill --domain {domain}`.",
        "",
        "## Domain contract",
        "",
        f"- Domain: `{domain}`",
        f"- Stale-after-days: `{stale_after_days}`",
        "- Authoritative sources (cite at least one when possible):",
    ]
    if sources:
        for src in sources:
            lines.append(f"  - `{src}`")
    else:
        lines.append("  - _(none registered — domain is reactive; see `vault/wiki/domains/<x>/_schema.md`)_")

    lines.extend([
        "",
        "## When to use",
        "",
        f"- The user asks to write, propose, ingest, or revise a `{domain}` wiki page.",
        f"- The user asks for a `{domain}` context pack at tier=standard or expanded.",
        "- The user mentions a topic / paper / entity already in this domain's claim ledger.",
        "",
        "## How to use",
        "",
        "1. Read the positive exemplars below and match their tone / structure / level of detail.",
        "2. Avoid the anti-patterns called out in the rejected exemplars.",
        "3. Output a Karpathy wiki page body: full YAML frontmatter (page_type, "
        "domain, claim_ids, source_ids, t_valid_from, t_valid_to, confidence, "
        "review_state), then `## Question`, `## Sources`, `## Compiled Findings`, "
        "`## Candidate Claims`, `## References`.",
        "4. Do NOT write directly to `vault/wiki/`.  Emit a `Proposal(kind=wiki_update)` "
        "via `wiki-ingest` or `wiki-propose-research` and let the human approve.",
        "",
        f"## Positive exemplars ({len(positives)})",
        "",
        _exemplar_block(positives, "positive"),
        "",
        f"## Anti-patterns — do not imitate ({len(negatives)})",
        "",
        _exemplar_block(negatives, "negative"),
        "",
        "## See also",
        "",
        f"- `vault/wiki/AGENTS.md` — global wiki schema",
        f"- `vault/wiki/domains/{domain.replace('_', '-')}/_schema.md` — domain sub-schema",
        f"- `prompts/{domain}/{prompt_compile_report.to_version}/system_prompt.md` — raw compiled prompt",
        "",
    ])
    return "\n".join(lines)
