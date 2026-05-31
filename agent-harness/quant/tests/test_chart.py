"""Interactive Lightweight-Charts HTML (local TradingView) — contract + file write."""

import re
from types import SimpleNamespace

from quant.backtest import chart
from quant.backtest.engine import Trade

_H = 3_600_000_000
_BASE = 1_704_067_200_000_000


def _bars(n, tf_us=_H):
    return [{"bucket_ts": _BASE + i * tf_us, "open": 100 + i, "high": 101 + i,
             "low": 99 + i, "close": 100.5 + i, "volume": 1.0} for i in range(n)]


def _result():
    trades = [Trade("BTCUSDT", _BASE + 2 * _H, _BASE + 5 * _H, 102.0, 105.0, 0.1, 0.0,
                    0.3, 0.029, 3, "signal", "背驰@support rr=1.5")]
    return SimpleNamespace(strategy_id="s", symbol="BTCUSDT", equity0=10000.0,
                           final_equity=10000.3, trades=trades,
                           equity_curve=[(_BASE, 10000.0), (_BASE + 5 * _H, 10000.3)])


def test_chart_html_contract():
    html = chart.build_chart_html(_result(), {"1h": _bars(20), "4h": _bars(6, 4 * _H)}, title="t")
    assert "lightweight-charts" in html                       # TradingView's OSS engine
    assert "addCandlestickSeries" in html and "setMarkers" in html
    assert "背驰@support" in html                              # the trigger reason is rendered
    assert 'data-tf="1h"' in html and 'data-tf="4h"' in html  # multi-timeframe switcher
    assert "setTf('1h')" in html and "subscribeCrosshairMove" in html
    assert not re.search(r"__[A-Z]+__", html)                 # every placeholder substituted


def test_chart_write_file(tmp_path):
    p = chart.write_chart(_result(), {"1h": _bars(20)}, tmp_path / "c.html", title="t")
    assert p.exists() and p.read_text(encoding="utf-8").startswith("<!doctype html")


def test_chart_default_tf_falls_back():
    html = chart.build_chart_html(_result(), {"4h": _bars(6, 4 * _H)}, default_tf="1h")
    assert "setTf('4h')" in html                              # missing default -> first available
