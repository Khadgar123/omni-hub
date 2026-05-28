"""Shared helpers for omni_hub.cli.* submodules."""

from __future__ import annotations

import json
from typing import Any

from ..models import OperationSpec
from ..runner import OperationRunner


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def run_and_print(
    runner: OperationRunner,
    spec: OperationSpec,
    *,
    approved: bool = False,
) -> int:
    result = runner.run(spec, approved=approved)
    print_json(result.to_dict())
    return 0 if result.error is None else 1
