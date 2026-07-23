"""features.acceleration — the leading, ATR-normalized, scale-free exhaustion signal."""

from quant import features as F


def _bars(closes):
    return [{"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1.0,
             "bucket_ts": i} for i, c in enumerate(closes)]


def test_acceleration_causal_warmup_and_sign_flip():
    # close-to-close steps grow (1,2,3,4) then shrink (3,2,1,0): accel +,+,+ then -,-,-
    closes = [100, 101, 103, 106, 110, 113, 115, 116, 116]
    a = F.acceleration(_bars(closes), atr_len=3, smooth=1)
    assert len(a) == len(closes)
    assert a[0] is None and a[1] is None          # 2nd derivative warmup
    vals = [x for x in a if x is not None]
    assert any(v > 0 for v in vals)               # accelerating up
    assert any(v < 0 for v in vals)               # then decelerating (rolls over)


def test_acceleration_zero_on_constant_trend():
    # constant velocity (steps all = 2) => acceleration ~ 0
    closes = [100 + 2 * i for i in range(12)]
    a = F.acceleration(_bars(closes), atr_len=3, smooth=1)
    assert all(abs(x) < 1e-9 for x in a if x is not None)
