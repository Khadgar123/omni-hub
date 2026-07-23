"""``python -m quant`` entry point (mirrors ``python -m quant.market_store``)."""

from __future__ import annotations

import sys

from .market_store import main

if __name__ == "__main__":
    raise SystemExit(main())
