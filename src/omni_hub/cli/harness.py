"""harness-* commands (excluding the three report commands; see reports.py).

All heavy harness submodules are imported lazily inside each handler so the
import cost is paid only for the command actually invoked — matches the
original cli.py behaviour.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..models import OperationSpec, RiskLevel
from ._common import print_json, run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    task_validate = subparsers.add_parser(
        "harness-task-validate",
        help="Validate a Task Packet JSON file against the harness contract.",
    )
    task_validate.add_argument("--file", required=True)

    ensemble = subparsers.add_parser(
        "harness-ensemble",
        help="Fan a prompt out to N models via ccLoad and print a GenerationRecord.",
    )
    ensemble.add_argument("--prompt", required=True)
    ensemble.add_argument(
        "--model",
        action="append",
        default=[],
        help="Model name; can be passed multiple times. Defaults to api-management/defaults.json provider models.",
    )
    ensemble.add_argument("--system", default="")
    ensemble.add_argument("--temperature", type=float, default=0.7)
    ensemble.add_argument("--max-tokens", type=int, default=1024)
    ensemble.add_argument("--timeout-seconds", type=float, default=60.0)
    ensemble.add_argument("--prompt-version", default="v0")
    ensemble.add_argument("--task-id", default="")
    ensemble.add_argument(
        "--gateway-url",
        default="http://127.0.0.1:8080",
        help="ccLoad base URL; default matches local compose layout.",
    )
    ensemble.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved EnsembleConfig without making any HTTP call.",
    )

    judge = subparsers.add_parser(
        "harness-judge",
        help="Run the local-heuristic judge ensemble + bias audit over a GenerationRecord JSON file.",
    )
    judge.add_argument("--record", required=True)
    judge.add_argument(
        "--judges", type=int, default=3,
        help="How many local heuristic judges to instantiate (default 3).",
    )
    judge.add_argument(
        "--severity-threshold", type=float, default=0.4,
        help="Bias finding above this severity gets tagged onto each judge score.",
    )

    ground = subparsers.add_parser(
        "harness-ground",
        help="Run atomic-claim + citation analysis on a piece of text.",
    )
    ground.add_argument("--text")
    ground.add_argument("--file")

    pref_add = subparsers.add_parser(
        "harness-preference-add",
        help="Append a human-preference record (accepted/rejected/edited).",
    )
    pref_add.add_argument("--domain", required=True)
    pref_add.add_argument(
        "--decision", required=True,
        choices=["accepted", "rejected", "edited"],
    )
    pref_add.add_argument("--text", default="")
    pref_add.add_argument("--file")
    pref_add.add_argument("--reason", default="")
    pref_add.add_argument("--task-id", default="")
    pref_add.add_argument("--prompt-version", default="v0")
    pref_add.add_argument("--store-root", default=".omni/preference")

    pref_stats = subparsers.add_parser(
        "harness-preference-stats",
        help="Show accepted/rejected/edited counts per domain.",
    )
    pref_stats.add_argument("--domain")
    pref_stats.add_argument("--store-root", default=".omni/preference")

    compile_p = subparsers.add_parser(
        "harness-compile",
        help="Compile a new prompt version from accepted/rejected preferences.",
    )
    compile_p.add_argument("--domain", required=True)
    compile_p.add_argument("--from-version", default="v0")
    compile_p.add_argument("--output-root", default="prompts")
    compile_p.add_argument("--store-root", default=".omni/preference")
    compile_p.add_argument(
        "--backend", default="auto", choices=["auto", "dspy", "manual"],
    )
    compile_p.add_argument("--bootstrap-rounds", type=int, default=8)
    compile_p.add_argument("--max-positive", type=int, default=12)
    compile_p.add_argument("--max-negative", type=int, default=6)

    compile_skill = subparsers.add_parser(
        "harness-compile-skill",
        help=(
            "Compile accepted/rejected preference spans into a SKILL.md file "
            "loadable by Claude Code / Codex (Anthropic Skills spec)."
        ),
    )
    compile_skill.add_argument("--domain", required=True)
    compile_skill.add_argument(
        "--skill-id", default="",
        help="kebab-case skill id (default: <domain>-wiki)",
    )
    compile_skill.add_argument(
        "--description", default="",
        help="Override the generated SKILL.md description (≤1024 chars).",
    )
    compile_skill.add_argument(
        "--output-root", default=".agents/skills",
        help="Where to write <skill-id>/SKILL.md (default: .agents/skills/)",
    )
    compile_skill.add_argument("--store-root", default=".omni/preference")
    compile_skill.add_argument("--from-version", default="v0")
    compile_skill.add_argument("--max-positive", type=int, default=10)
    compile_skill.add_argument("--max-negative", type=int, default=4)
    compile_skill.add_argument(
        "--backend", default="manual", choices=["auto", "dspy", "manual"],
        help="Underlying prompt compile backend (default: manual)",
    )

    redund = subparsers.add_parser(
        "harness-redundancy-scan",
        help="Scan memory for duplicate/stale/conflict/low_signal proposals.",
    )
    redund.add_argument("--db-path", default=".omni/memory.sqlite3")
    redund.add_argument(
        "--prefer-backend", default="auto",
        choices=["auto", "graphiti", "local"],
    )
    redund.add_argument("--freshness-days", type=int, default=365)
    redund.add_argument("--min-low-signal-ratio", type=float, default=0.5)
    redund.add_argument("--max-documents", type=int, default=5000)

    subparsers.add_parser(
        "harness-domain-list",
        help="List domain profiles declared in agent-harness/domain-profiles.json.",
    )

    domain_get = subparsers.add_parser(
        "harness-domain-get",
        help="Print a single domain profile.",
    )
    domain_get.add_argument("--domain", required=True)

    task_template = subparsers.add_parser(
        "harness-task-template",
        help="Print a starter Task Packet JSON for a domain (pipe to a file and edit).",
    )
    task_template.add_argument("--domain", required=True)
    task_template.add_argument("--goal", default="")
    task_template.add_argument("--audience", default="")

    stats = subparsers.add_parser(
        "harness-stats",
        help="Aggregate harness trace stats (preference / compile / judge wins / counts).",
    )
    stats.add_argument(
        "--prefer-backend", default="auto",
        choices=["auto", "opik", "local"],
    )

    replay = subparsers.add_parser(
        "harness-replay",
        help="Stream raw trace envelopes, optionally filtered by kind.",
    )
    replay.add_argument(
        "--kind",
        choices=["generation", "judge", "preference", "compile", "redundancy", "report"],
    )
    replay.add_argument(
        "--prefer-backend", default="auto",
        choices=["auto", "opik", "local"],
    )
    replay.add_argument("--limit", type=int, default=100)


def _task_validate(args, *, runner, workspace) -> int:
    from ..harness.models import TaskPacket

    path = Path(args.file)
    if not path.exists():
        print_json({"file": str(path), "error": "file not found"})
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print_json({"file": str(path), "error": f"invalid JSON: {exc}"})
        return 1
    packet = TaskPacket.from_dict(data)
    errors = packet.validate()
    print_json(
        {
            "file": str(path),
            "task_id": packet.task_id,
            "domain_profile": packet.domain_profile,
            "errors": errors,
            "ok": not errors,
        }
    )
    return 0 if not errors else 1


def _ensemble(args, *, runner, workspace) -> int:
    from dataclasses import asdict

    from ..harness.ensemble import (
        EnsembleConfig,
        ModelSpec,
        load_default_models,
        run_ensemble,
    )

    models = (
        load_default_models(workspace, extra_models=args.model)
        if not args.model
        else [
            ModelSpec(
                name=name,
                base_url=args.gateway_url,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                timeout_seconds=args.timeout_seconds,
            )
            for name in args.model
        ]
    )
    if not args.model:
        for spec in models:
            spec.base_url = args.gateway_url
            spec.temperature = args.temperature
            spec.max_tokens = args.max_tokens
            spec.timeout_seconds = args.timeout_seconds

    config = EnsembleConfig(
        models=models,
        system_prompt=args.system,
        prompt_version=args.prompt_version,
    )

    if args.dry_run:
        print_json(
            {
                "dry_run": True,
                "prompt": args.prompt,
                "models": [asdict(spec) for spec in config.models],
                "system_prompt": config.system_prompt,
                "prompt_version": config.prompt_version,
            }
        )
        return 0

    record = run_ensemble(args.prompt, config, task_id=args.task_id)
    print_json(record.to_dict())
    if record.candidates and all(c.error for c in record.candidates):
        return 1
    return 0


def _judge(args, *, runner, workspace) -> int:
    from ..harness import judge_ensemble
    from ..harness.models import (
        Candidate,
        GenerationRecord,
        HumanFeedback,
        JudgeRubric,
        JudgeScore,
    )

    path = Path(args.record)
    if not path.exists():
        print_json({"error": f"record file not found: {path}"})
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    candidates = [
        Candidate(
            candidate_id=str(c.get("candidate_id", "")) or None,  # type: ignore[arg-type]
            model=str(c.get("model", "")),
            text=str(c.get("text", "")),
            claim_evidence_map=list(c.get("claim_evidence_map", [])),
            judge_scores=[
                JudgeScore(
                    judge_id=str(js.get("judge_id", "")),
                    model=str(js.get("model", "")),
                    dimensions=dict(js.get("dimensions", {})),
                    rationale=str(js.get("rationale", "")),
                    detected_biases=list(js.get("detected_biases", [])),
                )
                for js in c.get("judge_scores", [])
            ],
            failure_tags=list(c.get("failure_tags", [])),
            elapsed_ms=int(c.get("elapsed_ms", 0)),
            error=c.get("error"),
        )
        for c in data.get("candidates", [])
    ]
    for c, raw in zip(candidates, data.get("candidates", [])):
        if not raw.get("candidate_id"):
            continue
        c.candidate_id = str(raw["candidate_id"])
    record = GenerationRecord(
        schema_version=int(data.get("schema_version", 1)),
        record_id=str(data.get("record_id") or ""),
        task_id=str(data.get("task_id", "")),
        prompt_version=str(data.get("prompt_version", "v0")),
        retrieval_snapshot=list(data.get("retrieval_snapshot", [])),
        candidates=candidates,
        human_feedback=(
            HumanFeedback(**data["human_feedback"])
            if data.get("human_feedback") else None
        ),
        regression_case_id=data.get("regression_case_id"),
        created_at=str(data.get("created_at") or ""),
    )

    judges = [
        judge_ensemble.LocalHeuristicJudge(f"local-{i}", f"heuristic-{i}")
        for i in range(max(1, args.judges))
    ]
    result = judge_ensemble.run_judges(
        record, judges, JudgeRubric(),
        audit_severity_threshold=args.severity_threshold,
    )
    print_json(result.to_dict())
    return 0


def _ground(args, *, runner, workspace) -> int:
    from ..harness import grounding

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        print_json({"error": "either --text or --file is required"})
        return 1
    print_json(grounding.analyze_grounding(text).to_dict())
    return 0


def _preference_add(args, *, runner, workspace) -> int:
    text = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    return run_and_print(
        runner,
        OperationSpec(
            name="harness_preference_add",
            action="append",
            payload={
                "domain": args.domain,
                "decision": args.decision,
                "text": text,
                "task_id": args.task_id,
                "prompt_version": args.prompt_version,
                "reason": args.reason,
                "store_root": args.store_root,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _preference_stats(args, *, runner, workspace) -> int:
    from ..harness.preference import PreferenceStore

    store = PreferenceStore(args.store_root)
    if args.domain:
        print_json(store.stats(args.domain))
    else:
        print_json({"domains": [store.stats(d) for d in store.list_domains()]})
    return 0


def _compile_skill(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="harness_compile_skill",
            action="compile_skill_md",
            payload={
                "domain": args.domain,
                "skill_id": args.skill_id,
                "description": args.description,
                "output_root": args.output_root,
                "store_root": args.store_root,
                "from_version": args.from_version,
                "max_positive": args.max_positive,
                "max_negative": args.max_negative,
                "backend": args.backend,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _compile(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="harness_compile",
            action="compile_prompt",
            payload={
                "domain": args.domain,
                "from_version": args.from_version,
                "output_root": args.output_root,
                "store_root": args.store_root,
                "backend": args.backend,
                "bootstrap_rounds": args.bootstrap_rounds,
                "max_positive": args.max_positive,
                "max_negative": args.max_negative,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _redundancy_scan(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="harness_redundancy_scan",
            action="scan",
            payload={
                "db_path": args.db_path,
                "prefer_backend": args.prefer_backend,
                "freshness_days": args.freshness_days,
                "min_low_signal_ratio": args.min_low_signal_ratio,
                "max_documents": args.max_documents,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


def _domain_list(args, *, runner, workspace) -> int:
    from ..harness import domain_profiles

    try:
        profiles = domain_profiles.load_all()
    except FileNotFoundError as exc:
        print_json({"error": str(exc)})
        return 1
    print_json({"domains": [p.to_dict() for p in profiles.values()]})
    return 0


def _domain_get(args, *, runner, workspace) -> int:
    from ..harness import domain_profiles

    try:
        profile = domain_profiles.get(args.domain)
    except (FileNotFoundError, KeyError) as exc:
        print_json({"error": str(exc)})
        return 1
    print_json(profile.to_dict())
    return 0


def _task_template(args, *, runner, workspace) -> int:
    from ..harness import domain_profiles

    try:
        packet = domain_profiles.build_task_packet_template(
            args.domain, goal=args.goal, audience=args.audience,
        )
    except (FileNotFoundError, KeyError) as exc:
        print_json({"error": str(exc)})
        return 1
    print_json(packet.to_dict())
    return 0


def _stats(args, *, runner, workspace) -> int:
    from ..harness import replay

    result = replay.stats(prefer_backend=args.prefer_backend)
    print_json(result.to_dict())
    return 0


def _replay(args, *, runner, workspace) -> int:
    from ..harness import replay

    count = 0
    for envelope in replay.replay(kind=args.kind, prefer_backend=args.prefer_backend):
        print_json(envelope)
        count += 1
        if count >= args.limit:
            break
    return 0


COMMANDS = {
    "harness-task-validate": _task_validate,
    "harness-ensemble": _ensemble,
    "harness-judge": _judge,
    "harness-ground": _ground,
    "harness-preference-add": _preference_add,
    "harness-preference-stats": _preference_stats,
    "harness-compile": _compile,
    "harness-compile-skill": _compile_skill,
    "harness-redundancy-scan": _redundancy_scan,
    "harness-domain-list": _domain_list,
    "harness-domain-get": _domain_get,
    "harness-task-template": _task_template,
    "harness-stats": _stats,
    "harness-replay": _replay,
}
