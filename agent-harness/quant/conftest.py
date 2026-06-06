"""pytest fixtures for the quant store tests.

Adds the package dir to ``sys.path`` so ``import quant`` works even without an
editable install, and provides a populated + an empty store root.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture
def empty_root(tmp_path):
    """A fresh, empty store root."""

    return tmp_path / "market"


@pytest.fixture
def store(tmp_path):
    """A store root with the bundled sample (trades + reference tables) written."""

    from quant import sample

    root = tmp_path / "market"
    sample.materialize_sample(root=root)
    return root
