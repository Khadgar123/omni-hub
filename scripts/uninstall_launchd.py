#!/usr/bin/env python3
"""Bootout and remove the omni-hub launchd agents.

Mirror of ``install_launchd.py``: unloads each plist with
``launchctl bootout`` and deletes the file under
``~/Library/LaunchAgents/``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


PLIST_NAMES = ["omni-hub.daily", "omni-hub.weekly", "omni-hub.monthly", "omni-hub.worker"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only",
                        help="Comma-separated subset (daily, weekly, monthly, worker).")
    args = parser.parse_args(argv)

    launch_agents = Path.home() / "Library" / "LaunchAgents"
    uid = os.getuid()
    if args.only:
        short = {s.strip() for s in args.only.split(",") if s.strip()}
        requested = [n for n in PLIST_NAMES if n.split(".")[-1] in short]
    else:
        requested = list(PLIST_NAMES)

    for name in requested:
        path = launch_agents / f"{name}.plist"
        if not path.exists():
            print(f"skip (missing): {path}")
            continue
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}", str(path)],
            check=False, capture_output=True,
        )
        path.unlink()
        print(f"removed: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
