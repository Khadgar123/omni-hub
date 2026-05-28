"""Test helpers used by the test suite.

These live inside ``omni_hub`` (rather than under ``tests/`` or a pytest
``conftest.py``) because the test runner is ``python -m unittest discover``
which does not auto-load conftest, and adding ``tests/__init__.py`` would
break discover's module-naming.  Placing the helpers next to the code
keeps the import path uniform: ``from omni_hub.testing import cli_runner``.
"""

from __future__ import annotations

import json
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any


def cli_runner(workspace: Path, argv: list[str]) -> dict[str, Any]:
    """Invoke ``omni_hub.cli.main`` inside ``workspace`` and return parsed stdout.

    The previous in-tree ``_run_cli`` was duplicated across five test
    files; tests now do ``from omni_hub.testing import cli_runner``.

    ``original`` cwd is taken from the *repo root* — never ``os.getcwd()`` —
    so a previously-aborted test that left cwd inside a now-deleted
    tmpdir cannot make this helper crash (the lesson from the
    test_harness_opik_replay cwd-not-restored bug).

    The return value has ``__exit`` injected with the CLI's exit code so
    tests can branch on success/failure without losing the parsed body.
    """

    from .cli import main

    repo_root = Path(__file__).resolve().parents[2]  # …/src/omni_hub → …/
    buffer = StringIO()
    try:
        os.chdir(workspace)
        with redirect_stdout(buffer):
            exit_code = main(argv)
    finally:
        os.chdir(repo_root)
    payload = json.loads(buffer.getvalue())
    payload["__exit"] = exit_code
    return payload


__all__ = ["cli_runner"]
