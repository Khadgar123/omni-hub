"""Meta-skill CLI (v0.28) — cross-skill knowledge transfer scan."""

from __future__ import annotations

import argparse

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    scan = subparsers.add_parser(
        "meta-cross-skill-scan",
        help="Scan PreferenceStore across all 19 domains, surface tokens "
             "with strong accepted-signal in ≥ 3 domains but absent / "
             "negative in others.  Emits CrossSkillFinding list — humans "
             "still approve each transfer before SKILL.md is touched.",
    )
    scan.add_argument("--signal-threshold", type=float, default=0.4,
                       help="Minimum (accepted-rejected)/total signal to "
                            "count as 'strong' (default 0.4).")
    scan.add_argument("--min-strong-domains", type=int, default=3,
                       help="Minimum number of domains a token must be "
                            "strong in to qualify as cross-skill (default 3).")
    scan.add_argument("--min-accepted-in-strong", type=int, default=2,
                       help="Minimum accepted_count per strong domain "
                            "(default 2).")
    scan.add_argument("--max-findings", type=int, default=50)


def _scan(args, *, runner, workspace) -> int:
    return run_and_print(
        runner,
        OperationSpec(
            name="meta_cross_skill_scan",
            action="scan",
            payload={
                "signal_threshold": args.signal_threshold,
                "min_strong_domains": args.min_strong_domains,
                "min_accepted_in_strong": args.min_accepted_in_strong,
                "max_findings": args.max_findings,
            },
            risk_level=RiskLevel.READ_ONLY,
        ),
    )


COMMANDS = {
    "meta-cross-skill-scan": _scan,
}
