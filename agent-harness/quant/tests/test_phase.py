"""phase.classify — the 2D (trendiness × volatility) regime map + phase_trend_v1 contract."""

from types import SimpleNamespace

from quant import phase as P
from quant.strategy import registry
from quant.strategy.base import LONG, SHORT
from quant.strategy.phase_trend_v1 import PhaseTrendV1


def _bars(closes, hl=20):
    return [{"open": c, "high": c + hl, "low": c - hl, "close": c, "volume": 10.0,
             "bucket_ts": i * 14_400_000_000} for i, c in enumerate(closes)]


def test_classify_detects_trend_up_and_chop():
    up = _bars([60000 + 150 * i for i in range(80)])          # clean ramp -> trend_up
    ph = P.classify(up)
    assert ph[-1]["phase"] == "trend_up" and ph[-1]["sign"] == 1
    # alternating saw at low net travel -> ER low -> range (chop or coil), never trend
    saw = _bars([60000 + (400 if i % 2 else -400) for i in range(80)])
    assert P.classify(saw)[-1]["phase"] in ("chop", "coil", "mid")


def test_classify_trend_down_sign():
    dn = _bars([70000 - 150 * i for i in range(80)])
    assert P.classify(dn)[-1]["phase"] == "trend_down"


def test_classify_causal_and_full_length():
    b = _bars([60000 + 100 * i for i in range(70)])
    ph = P.classify(b)
    assert len(ph) == len(b)
    assert ph[0]["phase"] == "none"                           # warmup -> no phase yet


def test_phase_trend_registered_and_follows():
    s = registry.by_id("phase_trend_v1")
    assert isinstance(s, PhaseTrendV1) and s.timeframe == "4h"
    st = SimpleNamespace(symbol="BTCUSDT", regime_label="up", composite_bias="long", stand_down=False)
    out = s.evaluate(_bars([60000 + 150 * i for i in range(80)]), st, 0.0)
    assert out is not None and out.direction == LONG          # follows an uptrend
    out2 = s.evaluate(_bars([70000 - 150 * i for i in range(80)]), st, 0.0)
    assert out2 is not None and out2.direction == SHORT       # symmetric


def test_phase_trend_only_two_free_params():
    import dataclasses
    free = {f.name for f in dataclasses.fields(PhaseTrendV1)
            if f.name not in ("id", "timeframe", "eligible_regimes", "requires_bias")}
    assert free == {"stop_atr", "trail_atr"}
