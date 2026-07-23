"""Dynamic chart server — the page builder contract (server I/O is smoke-tested live)."""

import re

from quant.backtest import chartserver


def test_build_page_html_contract():
    meta = {"symbol": "BTCUSDT", "title": "t", "tfs": ["1m", "1h", "1d"],
            "tfsec": {"1m": 60, "1h": 3600, "1d": 86400}, "range": [1000, 2000], "default_tf": "1h",
            "trades": [{"i": 0, "e0": 1500, "e1": 1600, "e0iso": "2024-01-01 00:00",
                        "e1iso": "2024-01-01 01:00", "entry": 100, "exit": 101, "pnl": 1.0,
                        "ret": 0.01, "bars": 1, "reason": "signal", "rationale": "背驰@support"}]}
    html = chartserver.build_page_html(meta)
    assert "lightweight-charts" in html and "/api/bars" in html        # fetches bars on demand
    assert "subscribeVisibleLogicalRangeChange" in html                # lazy-loads history
    assert "背驰@support" in html and "setTf('1h')" in html
    assert "data-tf=\"1m\"" in html and "data-tf=\"1d\"" in html        # tf switcher
    assert not re.search(r"__[A-Z]+__", html)                          # every placeholder filled
