"""level_fade_v1 — registry/contract, parsimony (3 free params), symmetric
long+short emission on a mean-reverting series, and the tail-risk regime gate."""

from types import SimpleNamespace

from quant.strategy import registry
from quant.strategy.base import LONG, SHORT
from quant.strategy.level_fade_v1 import LevelFadeV1


def _osc(n=320, period=40, mid=69000, amp=300, jit=25, hl=120):
    """A tight, jittered TRIANGLE oscillation: low ADX (ranging, so the trend gate
    passes) with repeated level retests — the regime a fade is built for. (A clean
    high-amplitude sine fails by construction: each leg trends hard -> ADX>25.)"""
    bars = []
    for i in range(n):
        tri = (4 * amp / period) * abs((i % period) - period / 2) - amp
        c = mid + tri + (jit if i % 2 else -jit)
        bars.append({"open": c, "high": c + hl, "low": c - hl, "close": c,
                     "volume": 10.0, "bucket_ts": i * 300_000_000})
    return bars


def _state(label="range"):
    return SimpleNamespace(symbol="BTCUSDT", regime_label=label,
                           composite_bias="long", stand_down=False, op_atr=None)


def test_registered_and_contract():
    s = registry.by_id("level_fade_v1")
    assert isinstance(s, LevelFadeV1) and s.timeframe == "5m"
    assert s.requires_bias is None                                  # symmetric
    # trades in all regimes; DIRECTION (not the eligible set) is gated by regime in evaluate
    assert {"range", "up", "down", "strong_up", "strong_down"} <= s.eligible_regimes


def test_only_two_free_parameters():
    # the contract: exactly TWO economically-named free knobs (rest are fixed constants)
    import dataclasses
    free = {f.name for f in dataclasses.fields(LevelFadeV1)
            if f.name not in ("id", "timeframe", "eligible_regimes", "requires_bias")}
    assert free == {"near_atr", "stop_atr"}


def test_insufficient_bars_returns_none():
    s = LevelFadeV1()
    bars = [{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1.0, "bucket_ts": i}
            for i in range(10)]
    assert s.evaluate(bars, _state(), 0.0) is None


def test_symmetric_emits_long_and_short_on_mean_reversion():
    s = LevelFadeV1()
    bars = _osc()
    dirs = set()
    for i in range(66, len(bars)):
        out = s.evaluate(bars[: i + 1], _state(), 0.0)
        if out is not None:
            dirs.add(out.direction)
    assert LONG in dirs and SHORT in dirs              # the support you long IS the short's target


def test_direction_gated_by_regime():
    # "大级别定方向": never fade AGAINST the dominant trend.
    s = LevelFadeV1()
    bars = _osc()
    # in a down regime the strategy may SHORT but must NEVER open a LONG (no knife-catching)
    in_down = [s.evaluate(bars[: i + 1], _state("strong_down"), 0.0) for i in range(66, len(bars))]
    assert all(o is None or o.direction != LONG for o in in_down)
    # in an up regime, never open a SHORT
    in_up = [s.evaluate(bars[: i + 1], _state("strong_up"), 0.0) for i in range(66, len(bars))]
    assert all(o is None or o.direction != SHORT for o in in_up)
