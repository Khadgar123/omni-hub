"""ffn adapter (optional MIT analytics oracle) — skipped if ffn isn't installed."""

from types import SimpleNamespace

import pytest

ffn = pytest.importorskip("ffn")

from quant.backtest import ffn_report  # noqa: E402


def _result(curve):
    return SimpleNamespace(equity_curve=curve)


def test_ffn_stats_on_dense_curve():
    curve = [(i * 86_400_000_000, 10000.0 * (1.0005 ** i) + (40 if i % 3 else -25)) for i in range(400)]
    s = ffn_report.ffn_stats(_result(curve))
    assert "daily_sharpe" in s and "max_drawdown" in s and "daily_sortino" in s
    assert s["max_drawdown"] is None or s["max_drawdown"] <= 0


def test_ffn_stats_survives_near_flat_equity():
    # the failure mode that crashes QuantStats (#334) — ffn must NOT raise
    curve = [(i * 86_400_000_000, 10000.0 + (1.0 if i % 5 == 0 else 0.0)) for i in range(120)]
    s = ffn_report.ffn_stats(_result(curve))
    assert isinstance(s, dict) and "daily_sharpe" in s
