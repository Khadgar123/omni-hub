#!/usr/bin/env python3
"""Standalone Semantic Scholar API key heartbeat.

S2 recycles keys after ~60 days of inactivity.  This script makes one
minimal GET against the search endpoint to mark the key as "used",
appends a JSONL audit line to ``.omni/logs/s2-heartbeat.log``, and
exits 0 on HTTP 200 / non-zero otherwise.

Designed to be run by the ``com.omni-hub.s2-heartbeat`` launchd agent
on a weekly cadence (8-9 heartbeats per recycle window).

Run manually::

    python3 scripts/s2_heartbeat.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    from omni_hub.builtins import make_s2_heartbeat
    from omni_hub.models import OperationSpec, RiskLevel

    spec = OperationSpec(
        name="s2_heartbeat",
        action="ping",
        payload={},
        risk_level=RiskLevel.READ_ONLY,
    )
    result = make_s2_heartbeat(repo_root)(spec)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
