"""PPTX plane (v0.35).

Per the 2026 Q2 SOTA brief: the dominant pattern is **LLM emits a
typed outline → typed library renders the .pptx** (Anthropic official
``pptx`` Skill drives ``python-pptx``).  Reverse — LLM generating raw
OOXML — is anti-pattern.

Main repo (stdlib-only) ships:

* :class:`DeckOutline` + :class:`Slide` + :class:`Bullet` dataclasses
  (the *typed* outline)
* :class:`PPTXBuilder` Protocol (every concrete builder satisfies it)
* :class:`StubPPTXBuilder` that fails fast with a clear pointer to
  ``agent-harness/integrations/pptx/``

Real :class:`PPTXBuilder` lives under
``agent-harness/integrations/pptx/cli/pptx_omni.py`` (a ~200 LOC
python-pptx shim) and is invoked via subprocess so the dependency
stays out of the main repo.
"""

from __future__ import annotations

from .builder import (
    Bullet,
    DeckOutline,
    PPTXBuilder,
    PPTXResult,
    Slide,
    StubPPTXBuilder,
)

__all__ = [
    "Bullet",
    "DeckOutline",
    "PPTXBuilder",
    "PPTXResult",
    "Slide",
    "StubPPTXBuilder",
]
