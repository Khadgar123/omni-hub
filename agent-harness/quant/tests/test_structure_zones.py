"""Demand/supply order-block zones — the institutional-origin entry geometry."""

from quant import structure


def _b(o, h, l, c, i):
    return {"open": o, "high": h, "low": l, "close": c, "volume": 100.0,
            "bucket_ts": i * 1_000_000}


# rise to a swing high (idx2 ~104), pull back with two DOWN candles, then a strong
# up candle (idx5) closes above 104 = BOS up. The last down candle (idx4) is the demand OB.
_BARS = [
    _b(100.0, 101.0, 99.0, 100.5, 0),
    _b(100.5, 102.0, 100.0, 101.8, 1),
    _b(101.8, 104.0, 101.5, 103.5, 2),   # swing high ~104
    _b(103.5, 103.8, 102.0, 102.3, 3),   # down
    _b(102.3, 102.5, 100.0, 100.5, 4),   # down  <- demand OB origin
    _b(100.5, 107.0, 100.3, 106.5, 5),   # strong up: close 106.5 > 104 => BOS up
    _b(106.5, 108.0, 106.0, 107.8, 6),
    _b(107.8, 109.0, 107.0, 108.5, 7),
]


def test_demand_zone_from_bos():
    zones = structure.order_blocks(_BARS, left=2, right=2)
    demand = [z for z in zones if z["kind"] == "demand"]
    assert demand, "should find a demand zone at the BOS origin"
    z = demand[-1]
    assert z["origin_idx"] == 4 and z["confirmed_idx"] == 5     # causal: confirmed at the break
    assert z["confirmed_idx"] > z["origin_idx"]
    assert z["lo"] == 100.0 and z["hi"] == 102.5                # the down-candle range
    assert z["lo"] < z["mid"] < z["hi"]
    # the origin really is a down candle (close < open)
    assert _BARS[z["origin_idx"]]["close"] < _BARS[z["origin_idx"]]["open"]


def test_zone_is_causal_not_emitted_before_break():
    # truncate BEFORE the BOS bar -> no demand zone yet (nothing has broken structure)
    early = _BARS[:5]
    zones = structure.order_blocks(early, left=2, right=2)
    assert not [z for z in zones if z["kind"] == "demand"]


def test_supply_zone_mirror():
    # fall to a swing low, pull up with up candles, then a strong down candle breaks it.
    bars = [
        _b(100.0, 101.0, 99.0, 99.5, 0),
        _b(99.5, 100.0, 98.0, 98.2, 1),
        _b(98.2, 98.5, 96.0, 96.5, 2),    # swing low ~96
        _b(96.5, 98.0, 96.2, 97.7, 3),    # up
        _b(97.7, 100.0, 97.5, 99.5, 4),   # up  <- supply OB origin
        _b(99.5, 99.7, 93.0, 93.5, 5),    # strong down: close 93.5 < 96 => BOS down
        _b(93.5, 94.0, 92.0, 92.5, 6),
        _b(92.5, 93.0, 91.0, 91.5, 7),
    ]
    zones = structure.order_blocks(bars, left=2, right=2)
    supply = [z for z in zones if z["kind"] == "supply"]
    assert supply
    z = supply[-1]
    assert z["origin_idx"] == 4 and z["confirmed_idx"] == 5
    assert bars[z["origin_idx"]]["close"] > bars[z["origin_idx"]]["open"]   # up candle origin
