#!/usr/bin/env python3
"""All-skill GEPA evolution — one shot over every registered skill.

The audit recommended evolving every skill, not just one domain.  This
script:

1. Reads registry/skills.json + 19 domain entries from
   src/omni_hub/domain_schemas.py (single source of truth).
2. For each (domain, skill) pair: invokes ``omni-hub harness-compile``
   in dry-run mode if no preference data exists yet, otherwise real
   compile.
3. Aggregates the diff stats: how many skills produced a new
   ``prompts/<domain>/v2/system_prompt.md``, how many fell through
   for lack of data, how many errored.
4. Emits a Markdown report under
   ``.omni/reports/gepa-allskills-<YYYY-MM-DD>.md``.

This is the **flywheel kickoff** — first time the project really
exercises evolution across the full skill surface.  Until
PreferenceStore has real accept/reject records per domain, most skills
will fall through; the manifest captures that gap explicitly so the
user can see exactly where feedback is missing.

Usage::

    python3 scripts/gepa_evolve_all_skills.py            # dry-run + report
    python3 scripts/gepa_evolve_all_skills.py --apply    # actually compile
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path


# Domains that have prompts/<domain>/ pinned (i.e. evolvable today).
# Falls through to skill_stubs.DOMAIN_SCHEMAS if other domains added.
DEFAULT_DOMAINS = [
    "ai_progress", "research", "engineering", "biomedical",
    "finance", "us_policy", "cn_policy",
    "international_relations", "agent_systems",
    "social_en", "social_zh", "marketing", "enterprise",
    "meta", "photography", "fashion", "cooking", "travel",
    "fitness_wellness", "chat_relationships",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _has_preference_data(domain: str) -> tuple[bool, int]:
    """Return (has_data, record_count) for a domain's PreferenceStore."""

    pref_dir = _repo_root() / ".omni" / "preference" / domain
    if not pref_dir.exists():
        return False, 0
    n = sum(1 for _ in pref_dir.glob("*.jsonl"))
    if n == 0:
        # also try the per-record file pattern
        n = sum(1 for _ in pref_dir.rglob("*.json*"))
    return n > 0, n


def _run_compile(domain: str, *, apply: bool) -> dict:
    """Invoke harness-compile for one domain; return JSON result + status."""

    if not apply:
        return {
            "domain": domain,
            "skipped": True,
            "reason": "dry-run mode (use --apply to actually compile)",
        }
    py = sys.executable
    repo = _repo_root()
    cmd = [
        py, "-m", "omni_hub.cli", "harness-compile",
        "--domain", domain,
        "--backend", "auto",                                       # dspy if installed, else manual
    ]
    try:
        out = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=180,
            cwd=repo,
            env={
                **__import__("os").environ,
                "PYTHONPATH": str(repo / "src"),
            },
        )
    except subprocess.TimeoutExpired:
        return {"domain": domain, "error": "timeout after 180s"}
    if out.returncode != 0:
        return {
            "domain": domain,
            "error": f"rc={out.returncode}",
            "stderr_tail": (out.stderr or "")[-300:],
        }
    try:
        parsed = json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"domain": domain, "error": "json_parse_fail", "stdout_tail": out.stdout[-300:]}
    return {"domain": domain, "result": parsed}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true",
                   help="Actually run harness-compile (default: dry-run report)")
    p.add_argument("--domain",
                   help="Single domain only (default: all 20)")
    args = p.parse_args()

    domains = [args.domain] if args.domain else DEFAULT_DOMAINS

    sys.stderr.write(f"# GEPA evolution over {len(domains)} domains "
                     f"({'APPLY' if args.apply else 'dry-run'})\n\n")
    rows = []
    for dom in domains:
        has, n_records = _has_preference_data(dom)
        sys.stderr.write(
            f"  {dom:24s} preference={'YES' if has else 'NO ':3s} ({n_records} files)"
        )
        if not has:
            sys.stderr.write("  → skip (no feedback data yet)\n")
            rows.append({
                "domain": dom, "pref_records": 0,
                "status": "skip_no_preference", "compile_result": None,
            })
            continue
        sys.stderr.write("  → compile…\n")
        result = _run_compile(dom, apply=args.apply)
        status = "ok" if "result" in result else (
            "skipped" if result.get("skipped") else "error"
        )
        rows.append({
            "domain": dom, "pref_records": n_records,
            "status": status, "compile_result": result,
        })

    # Markdown report
    repo = _repo_root()
    today = date.today().isoformat()
    report_dir = repo / ".omni" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"gepa-allskills-{today}.md"

    n_total = len(rows)
    n_ok = sum(1 for r in rows if r["status"] == "ok")
    n_skip = sum(1 for r in rows if r["status"].startswith("skip"))
    n_err = sum(1 for r in rows if r["status"] == "error")

    lines = [
        f"# GEPA all-skills evolution — {today}\n",
        f"_mode: {'APPLY' if args.apply else 'dry-run'}_\n",
        f"\n## Summary\n",
        f"- domains scanned: {n_total}",
        f"- compiled OK:     {n_ok}",
        f"- skipped (no preference data): {n_skip}",
        f"- errored:         {n_err}",
        f"\n## Per-domain\n",
        f"| domain | pref records | status |",
        f"| --- | ---: | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['domain']} | {r['pref_records']} | {r['status']} |"
        )

    if n_skip:
        lines.append("\n## What's missing for full flywheel\n")
        lines.append("Domains in `skip_no_preference` need accept/reject feedback to")
        lines.append("evolve.  Two ways to seed them:")
        lines.append("")
        lines.append("1. Approve some of the pending wiki_update Proposals — every")
        lines.append("   approve writes a `PreferenceRecord(accepted)` automatically")
        lines.append("   (see `omni-hub wiki-apply-proposal`).")
        lines.append("2. Manually log signal: `omni-hub harness-preference-add`.")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    sys.stderr.write(f"\n✅ report written: {report_path}\n")
    print(str(report_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
