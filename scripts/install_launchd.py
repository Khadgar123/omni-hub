#!/usr/bin/env python3
"""Render and install the omni-hub launchd agents.

Reads the .plist templates in ``scripts/launchd/``, substitutes
``{{WORKSPACE}}`` and ``{{PYTHON}}`` placeholders with absolute paths,
writes the rendered files to ``~/Library/LaunchAgents/``, and bootstraps
them with ``launchctl``.

Idempotent: re-running unloads then reloads.

Usage::

    python3 scripts/install_launchd.py [--dry-run] [--python /path/to/python] [--only daily,worker]

``--dry-run`` prints the rendered plists to stdout instead of installing
— useful for inspecting what will be deployed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


PLIST_NAMES = [
    "omni-hub.daily",
    "omni-hub.weekly",
    "omni-hub.monthly",
    "omni-hub.worker",
    "omni-hub.s2-heartbeat",
    "omni-hub.daily-follow",
    "omni-hub.quant",
]
REQUIRED_PYTHON = (3, 12)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _discover_quant_py() -> str:
    """Best-effort path to the quant venv interpreter (the one with duckdb +
    the ``quant`` package): ``QUANT_PY`` override, else a conda env named
    ``quant``, else a ``quant-market-store`` sibling. Empty string when not
    found — the quant plist is fail-soft without it."""
    import shutil

    env = os.environ.get("QUANT_PY")
    if env and Path(env).expanduser().exists():
        return str(Path(env).expanduser())
    for c in (
        Path.home() / "opt" / "anaconda3" / "envs" / "quant" / "bin" / "python",
        Path.home() / "anaconda3" / "envs" / "quant" / "bin" / "python",
        Path.home() / "miniconda3" / "envs" / "quant" / "bin" / "python",
        Path.home() / "miniforge3" / "envs" / "quant" / "bin" / "python",
    ):
        if c.exists():
            return str(c)
    cli = shutil.which("quant-market-store")
    if cli:
        sib = Path(cli).resolve().parent / "python"
        if sib.exists():
            return str(sib)
    return ""


def render(name: str, workspace: Path, python_bin: str, quant_py: str = "") -> str:
    template_path = _repo_root() / "scripts" / "launchd" / f"{name}.plist"
    text = template_path.read_text(encoding="utf-8")
    return (
        text.replace("{{WORKSPACE}}", str(workspace))
            .replace("{{PYTHON}}", python_bin)
            .replace("{{QUANT_PY}}", quant_py)
    )


def install(name: str, rendered: str) -> Path:
    target = _launch_agents_dir() / f"{name}.plist"
    _launch_agents_dir().mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")

    uid = os.getuid()
    # Idempotent: bootout first, ignore failure if not loaded.
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", str(target)],
        check=False, capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(target)],
        check=False, capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(
            f"warning: launchctl bootstrap failed for {name}: "
            f"{result.stderr.strip()}\n"
        )
    return target


def _check_python(python_bin: str) -> None:
    """Refuse to install a plist that points at a Python < 3.12."""

    try:
        out = subprocess.run(
            [python_bin, "-c",
             "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
        major, minor = (int(x) for x in out.split("."))
    except Exception as exc:                                # noqa: BLE001
        raise SystemExit(
            f"--python {python_bin!r} is not invokable: {exc}"
        ) from exc
    if (major, minor) < REQUIRED_PYTHON:
        raise SystemExit(
            f"--python {python_bin!r} resolves to {major}.{minor}; "
            f"omni-hub requires >= {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}. "
            f"Re-run with `--python /abs/path/to/python3.12+` or set PYTHON= in make."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print rendered plists; do not install.")
    parser.add_argument(
        "--python", default=sys.executable,
        help="Python interpreter to bake into the plist (default: the "
             "interpreter invoking THIS script — never shutil.which, which "
             "may resolve to a stale `python3` on PATH).",
    )
    parser.add_argument("--only",
                        help="Comma-separated subset of plists to install "
                             "(short names: daily, weekly, monthly, worker).")
    parser.add_argument(
        "--quant-py", default=None,
        help="quant venv interpreter baked into the quant plist's QUANT_PY "
             "(default: auto-discover; empty if not found — the job is fail-soft).")
    args = parser.parse_args(argv)

    _check_python(args.python)

    workspace = _repo_root().resolve()
    quant_py = args.quant_py if args.quant_py is not None else _discover_quant_py()
    requested: list[str]
    if args.only:
        short = {s.strip() for s in args.only.split(",") if s.strip()}
        requested = [n for n in PLIST_NAMES if n.split(".")[-1] in short]
        unknown = short - {n.split(".")[-1] for n in PLIST_NAMES}
        if unknown:
            parser.error(f"unknown plist(s): {unknown}")
    else:
        requested = list(PLIST_NAMES)

    if args.dry_run:
        for name in requested:
            print(f"# === {name}.plist ===")
            print(render(name, workspace, args.python, quant_py))
            print()
        return 0

    (workspace / ".omni" / "launchd").mkdir(parents=True, exist_ok=True)
    for name in requested:
        rendered = render(name, workspace, args.python, quant_py)
        target = install(name, rendered)
        print(f"installed: {target}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
