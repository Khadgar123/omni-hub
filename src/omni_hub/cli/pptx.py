"""PPTX CLI (v0.39) — typed DeckOutline → real .pptx via agent-harness broker."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..models import OperationSpec, RiskLevel
from ._common import run_and_print


def register(subparsers: argparse._SubParsersAction) -> None:
    build = subparsers.add_parser(
        "pptx-build",
        help="Render a DeckOutline JSON into a real .pptx.  Requires the "
             "pptx-omni broker on PATH (agent-harness/integrations/pptx/).  "
             "Without the broker, returns skipped=true so callers can plan "
             "around it.",
    )
    build.add_argument("--outline", required=True,
                        help="Path or `file://path` to a JSON DeckOutline")
    build.add_argument("--output", default="vault/decks/out.pptx",
                        help="Output relative to workspace")
    build.add_argument("--theme", default="default")


def _pptx_build(args, *, runner, workspace) -> int:
    outline_path = args.outline
    if outline_path.startswith("file://"):
        outline_path = outline_path[len("file://"):]
    path = Path(outline_path)
    if not path.is_absolute():
        path = workspace / path
    outline_dict = json.loads(path.read_text(encoding="utf-8"))
    # Optional theme override
    if args.theme:
        outline_dict["theme"] = args.theme
    return run_and_print(
        runner,
        OperationSpec(
            name="pptx_build", action="build",
            payload={
                "outline": outline_dict,
                "output_path": args.output,
            },
            risk_level=RiskLevel.LOCAL_WRITE,
        ),
    )


COMMANDS = {
    "pptx-build": _pptx_build,
}
