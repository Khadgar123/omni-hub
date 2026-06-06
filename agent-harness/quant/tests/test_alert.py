"""TradeAlert mapping + JSONL emission (the notify+manual surface)."""

import json
from types import SimpleNamespace

from quant import alert
from quant.strategy.base import LONG, StrategyIntent


def _intent(direction=LONG):
    return StrategyIntent(
        strategy_id="trend_donchian_v1", symbol="BTCUSDT", timeframe="1h",
        asof=1_700_000_000_000_000, direction=direction, conviction=0.6,
        entry_ref=67000.0, stop_price=65000.0, regime_at_signal="up",
        rationale="donchian breakout", features={},
    )


def _state(regime="up", bias="long", stand_down=False):
    return SimpleNamespace(regime_label=regime, composite_bias=bias, stand_down=stand_down)


def test_intent_to_alert_maps_buy():
    a = alert.intent_to_alert(_intent(LONG), _state())
    assert a.action == "buy"
    assert a.symbol == "BTCUSDT" and a.strategy_id == "trend_donchian_v1"
    assert a.regime == "up" and a.composite_bias == "long"
    assert a.ref_price == 67000.0 and a.suggested_stop == 65000.0
    assert a.conviction == 0.6 and a.stand_down is False
    assert a.kind == "trade_suggestion" and a.schema_version == "alert-v1"


def test_emit_appends_jsonl_roundtrip(tmp_path):
    feed = tmp_path / "alerts.jsonl"
    alert.emit(alert.intent_to_alert(_intent(LONG), _state()), feed)
    alert.emit(alert.intent_to_alert(_intent(LONG), _state(regime="strong_up")), feed)
    lines = feed.read_text().strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["action"] == "buy" and rec["symbol"] == "BTCUSDT"
    assert rec["kind"] == "trade_suggestion"
    assert json.loads(lines[1])["regime"] == "strong_up"
