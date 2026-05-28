"""Subprocess bridge to ``trafilatura`` for HTML→markdown fallback.

When Jina Reader returns empty / errors / the user is offline, fall back
to ``trafilatura`` for robust boilerplate-stripped extraction.  We do NOT
``pip install`` trafilatura in the main repo (``dependencies = []`` hard
rule) — instead the user pins it under ``agent-harness/forks/trafilatura``
or installs it globally with ``pipx``.  Both modes resolve via PATH so
the bridge stays stdlib-only.

Install paths (any of):

    # Option 1 — pipx (cleanest for a single user):
    pipx install trafilatura

    # Option 2 — agent-harness pin:
    cd agent-harness && git submodule add \
        https://github.com/adbar/trafilatura.git forks/trafilatura
    cd forks/trafilatura && pipx install --editable .

Bridge contract:

* ``extract_main_content(html, url) -> (text, status)`` where ``status``
  is ``"ok"`` / ``"empty"`` / ``"not_installed"`` / ``"timeout"`` /
  ``"error"`` and ``text`` is the markdown (empty string for non-ok).
* Stays silent on missing binary — graceful "not_installed" so callers
  can degrade to whatever they had before.
"""

from __future__ import annotations

import json
import shutil
import subprocess


def has_trafilatura() -> bool:
    """Return True iff ``trafilatura`` CLI is on PATH."""

    return shutil.which("trafilatura") is not None


def extract_main_content(
    html: str,
    url: str,
    *,
    timeout_sec: float = 10.0,
) -> tuple[str, str]:
    """Pipe ``html`` to trafilatura; return ``(markdown, status)``.

    Status taxonomy (stable contract for callers):
      ``"ok"``            extractor produced ≥1 char of text
      ``"empty"``         trafilatura ran but found no main content
      ``"not_installed"`` trafilatura binary missing on PATH
      ``"timeout"``       trafilatura exceeded ``timeout_sec``
      ``"error"``         non-zero exit / parser exception
    """

    if not has_trafilatura():
        return "", "not_installed"
    if not html or not html.strip():
        return "", "empty"

    try:
        # `--output markdown` ships in trafilatura 1.10+; `-u` passes url
        # for canonical / language detection.
        result = subprocess.run(
            ["trafilatura", "--output", "markdown", "-u", url],
            input=html,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return "", "timeout"
    except Exception:                                       # noqa: BLE001
        return "", "error"

    if result.returncode != 0:
        return "", "error"
    out = (result.stdout or "").strip()
    if not out:
        return "", "empty"
    return out, "ok"


def extract_with_metadata(
    html: str,
    url: str,
    *,
    timeout_sec: float = 10.0,
) -> tuple[dict, str]:
    """Same as ``extract_main_content`` but request JSON output with
    metadata (title, author, date, categories, raw text).

    Returns ``(payload_dict, status)``.  ``payload_dict`` is ``{}`` for
    any non-``"ok"`` status.
    """

    if not has_trafilatura():
        return {}, "not_installed"
    if not html or not html.strip():
        return {}, "empty"

    try:
        result = subprocess.run(
            ["trafilatura", "--output", "json", "--with-metadata", "-u", url],
            input=html,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return {}, "timeout"
    except Exception:                                       # noqa: BLE001
        return {}, "error"

    if result.returncode != 0:
        return {}, "error"
    raw = (result.stdout or "").strip()
    if not raw:
        return {}, "empty"
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return {}, "error"
        return payload, "ok"
    except json.JSONDecodeError:
        return {}, "error"
