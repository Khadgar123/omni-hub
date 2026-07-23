"""Order-plan engine: plan geometry (entries/stop/targets) + lifecycle simulation."""

import json
import math

import pytest

from quant import execution
from quant.execution import OrderLeg, OrderPlan, build_order_plan, simulate_plan


def _bars(closes, *, span=1.0, vol=100.0, t0=0, dt_us=4 * 3600 * 1_000_000):
    out = []
    for i, c in enumerate(closes):
        out.append({"open": c, "high": c + span, "low": c - span, "close": c,
                    "volume": vol, "bucket_ts": t0 + i * dt_us})
    return out


def _wave(n=90, base=100.0, amp=6.0, drift=0.15):
    return [base + amp * math.sin(i / 3.0) + drift * i for i in range(n)]


def test_long_plan_geometry():
    bars = _bars(_wave())
    plan = build_order_plan("BTCUSDT", "long", 0.6, bars, tranches=(0.4, 0.35, 0.25), rr=4.0)
    assert plan.direction == "long"
    ref = plan.ref_price
    # 3 entry tranches, ALL on the pullback (below ref for a long), nearest-first
    assert len(plan.entries) == 3
    assert all(e.price < ref for e in plan.entries)
    assert [e.price for e in plan.entries] == sorted((e.price for e in plan.entries), reverse=True)
    assert [round(e.size_frac, 2) for e in plan.entries] == [0.25, 0.35, 0.4]   # deeper-weighted
    assert plan.entries[-1].size_frac > plan.entries[0].size_frac               # don't buy too early
    # stop below the deepest entry; final target above; R:R respected
    assert plan.stop < plan.entries[-1].price
    assert plan.final_target > ref
    assert plan.risk_dist > 0
    assert plan.targets[-1].role == "final"
    # target sizes sum to 1.0 (scale-outs + held)
    assert abs(sum(t.size_frac for t in plan.targets) - 1.0) < 1e-6
    # final target is ~rr away from avg entry, in risk units
    wsum = sum(e.size_frac for e in plan.entries)
    avg = sum(e.price * e.size_frac for e in plan.entries) / wsum
    assert (plan.final_target - avg) / plan.risk_dist == pytest.approx(4.0, abs=0.3)
    assert plan.size_cap_frac > 0
    assert "结构失效" in plan.mandatory_stop_rule and "了结" in plan.mandatory_tp_rule
    assert plan.kind == "order_intent"


def test_short_plan_mirrors_long():
    bars = _bars(_wave())
    plan = build_order_plan("BTCUSDT", "short", 0.6, bars, rr=4.0)
    ref = plan.ref_price
    assert plan.direction == "short"
    assert all(e.price > ref for e in plan.entries)            # entries ABOVE for a short
    assert [e.price for e in plan.entries] == sorted(e.price for e in plan.entries)  # nearest-first up
    assert plan.stop > plan.entries[-1].price                  # stop ABOVE
    assert plan.final_target < ref                             # target BELOW


def test_flat_direction_is_empty():
    plan = build_order_plan("BTCUSDT", "flat", 0.5, _bars(_wave()))
    assert plan.direction == "flat"
    assert plan.entries == [] and plan.size_cap_frac == 0.0


def test_conviction_scales_size_cap():
    bars = _bars(_wave())
    lo = build_order_plan("BTCUSDT", "long", 0.3, bars)
    hi = build_order_plan("BTCUSDT", "long", 0.9, bars)
    assert hi.size_cap_frac > lo.size_cap_frac                 # vol-target × conviction


def _long_plan():
    return OrderPlan(
        asof=0, symbol="X", direction="long", conviction=0.6, ref_price=100.0, atr=2.0,
        entries=[OrderLeg(99.0, 0.4, "entry", "a"), OrderLeg(98.0, 0.35, "entry", "b"),
                 OrderLeg(97.0, 0.25, "entry", "c")],
        stop=95.0, stop_kind="atr", risk_dist=3.0,
        targets=[OrderLeg(110.0, 1.0, "final", "5R")], final_target=110.0, rr=5.0,
        size_cap_frac=0.3, mandatory_stop_rule="...", mandatory_tp_rule="...", rationale="...")


def test_sim_fill_then_target():
    plan = _long_plan()
    future = _bars([98, 105, 111, 112], span=2.0)   # bar0 low=96 fills all; bar2 high=113 hits 110
    r = simulate_plan(plan, future, fill_window=3, reverse_choch_exit=False)
    assert r["filled"] == 1
    assert r["exit_reason"] == "target"
    assert r["realized_r"] > 3.0                    # ~ (110-98.15)/3 ≈ 3.95R


def test_sim_fill_then_stop():
    plan = _long_plan()
    future = _bars([98, 94, 93], span=2.0)          # bar0 low=96 fills; bar1 low=92 <= 95 stop
    r = simulate_plan(plan, future, fill_window=3, reverse_choch_exit=False, stop_on_close=False)
    assert r["filled"] == 1
    assert r["exit_reason"] == "stop"
    assert r["realized_r"] == pytest.approx(-1.05, abs=0.1)   # intrabar stop exits AT 95 = -1.05R


def test_close_confirm_stop_avoids_fakeout_wick():
    plan = _long_plan()                              # stop 95, avg entry ~98.15, target 110
    future = [                                       # bar1 WICKS to 94 (< 95) but CLOSES 97 (> 95)
        {"open": 98, "high": 100, "low": 96, "close": 99, "volume": 1, "bucket_ts": 0},
        {"open": 99, "high": 99, "low": 94, "close": 97, "volume": 1, "bucket_ts": 1},
        {"open": 97, "high": 111, "low": 97, "close": 110, "volume": 1, "bucket_ts": 2},
    ]
    # default close-confirm: the 94 wick does NOT take us out -> rides to the target
    assert simulate_plan(plan, future, fill_window=1, reverse_choch_exit=False)["exit_reason"] == "target"
    # intrabar wick stop: the SAME 94 wick DOES stop us out (the fakeout)
    assert simulate_plan(plan, future, fill_window=1, reverse_choch_exit=False,
                         stop_on_close=False)["exit_reason"] == "stop"


def test_sim_no_fill_is_no_trade():
    plan = _long_plan()
    future = _bars([101, 102, 103], span=0.3)       # lows ~100.7, never reaches 99
    r = simulate_plan(plan, future, fill_window=3)
    assert r["filled"] == 0 and r["exit_reason"] == "no_fill"
    assert r["realized_r"] == 0.0


def test_plan_from_live_injected_no_network():
    # Binance fapi klines payload -> mapper -> plan, with an injected HTTP opener.
    rows = [[1700000000000 + i * 14400000, "100", "101", "99", str(100 + (i % 7)), "10",
             0, "0", 5, "6", "0", "0"] for i in range(60)]

    class _Resp:
        def __init__(self, d): self._d = d
        def read(self): return self._d
        def __enter__(self): return self
        def __exit__(self, *a): return False

    payload = json.dumps(rows).encode()

    def opener(req, timeout=15.0):
        return _Resp(payload)

    plan = execution.plan_from_live("BTCUSDC", "long", 0.55, venue="binance", tf="4h", opener=opener)
    assert plan.direction == "long" and len(plan.entries) == 3
    assert plan.to_dict()["schema_version"] == "orderplan-v2"


def test_usdc_is_zero_maker_and_tick_precise():
    plan = build_order_plan("BTCUSDC", "long", 0.55, _bars(_wave()))
    assert plan.maker_bps == 0.0 and plan.tick == 0.01
    for e in plan.entries:                              # every price snapped to the 0.01 tick
        assert abs(round(e.price / 0.01) * 0.01 - e.price) < 1e-6


def test_adaptive_management_by_conviction():
    bars = _bars(_wave())
    assert build_order_plan("BTCUSDC", "long", 0.50, bars).manage_style == "defensive"
    assert build_order_plan("BTCUSDC", "long", 0.70, bars).manage_style == "runner"


def test_stop_sits_beyond_zone_distal_no_fakeout():
    # a zone-quality long: stop must be BELOW the deepest entry (beyond the distal wick + buffer)
    plan = build_order_plan("BTCUSDC", "long", 0.55, _bars(_wave()))
    assert plan.stop < min(e.price for e in plan.entries)
    if plan.entry_zone:
        assert plan.stop < plan.entry_zone["lo"]       # below the zone wick (stop-hunt-resistant)


def test_render_is_human_readable():
    txt = execution.render_plan(build_order_plan("BTCUSDC", "long", 0.6, _bars(_wave())))
    assert "做多" in txt and "止损" in txt and "Proposal" in txt


def test_stream_executor_arms_proximal_on_reclaim():
    plan = _long_plan()                              # entries 99(.4)/98(.35)/97(.25)
    bars = [
        {"open": 100, "high": 100, "low": 98.6, "close": 98.8, "bucket_ts": 0},   # tag 99, no reclaim
        {"open": 98.8, "high": 99.5, "low": 98.7, "close": 99.3, "bucket_ts": 1}, # close>99 => reclaim fill
        {"open": 99.3, "high": 99.4, "low": 97.5, "close": 98.0, "bucket_ts": 2}, # 98 limit fills
    ]
    r = execution.stream_executor(plan, bars)
    armed = {f["armed"] for f in r["fills"]}
    assert "reclaim" in armed and "limit" in armed
    prox = [f for f in r["fills"] if f["armed"] == "reclaim"][0]
    assert prox["frac"] == 0.4 and prox["price"] > 99            # filled on the confirming close
    assert r["filled_frac"] == 0.75


def test_stream_executor_no_reclaim_skips_proximal():
    plan = _long_plan()
    bars = [                                          # tags 99 then keeps falling — no reclaim
        {"open": 100, "high": 100, "low": 98.5, "close": 98.6, "bucket_ts": 0},
        {"open": 98.6, "high": 98.7, "low": 96.8, "close": 97.0, "bucket_ts": 1},  # 98 & 97 limits fill
    ]
    r = execution.stream_executor(plan, bars)
    assert all(f["armed"] == "limit" for f in r["fills"])       # proximal never armed (didn't buy too early)
    assert r["filled_frac"] == 0.6


def test_emit_intent_writes_proposal_jsonl(tmp_path):
    import json as _json
    plan = build_order_plan("BTCUSDC", "long", 0.6, _bars(_wave()))
    path = execution.emit_intent(plan, tmp_path / "intents.jsonl")
    with open(path, encoding="utf-8") as stream:
        rec = _json.loads(stream.read().splitlines()[-1])
    assert rec["kind"] == "order_intent" and rec["plan"]["direction"] == "long"
    assert rec["plan"]["schema_version"] == "orderplan-v2"


def test_wall_entries_rest_on_real_liquidity():
    walls = {"bid_walls": [{"price": 64500, "qty": 50}, {"price": 63000, "qty": 80},
                           {"price": 61500, "qty": 30}], "ask_walls": []}
    entries, zone, distal, quality = execution._wall_entries(
        67000, 1000.0, 1, walls, (0.4, 0.35, 0.25), 6.0, 0.01)
    assert quality == "order_wall"
    assert [e.price for e in entries] == [64500.0, 63000.0, 61500.0]    # ON the walls, nearest-first
    assert entries[-1].size_frac > entries[0].size_frac                # deeper-weighted
    assert distal == 61500.0


def test_manual_plan_from_entered_levels():
    p = execution.manual_plan("BTCUSDC", "short", entry=67000, stop=67300, targets=[66000, 64000], size=0.2)
    assert p.direction == "short" and p.entries[0].price == 67000.0
    assert p.stop == 67300.0 and p.risk_dist == 300.0
    assert len(p.targets) == 2 and p.final_target == 64000.0
    assert p.disaster_stop > p.stop                    # short: disaster sits ABOVE the stop
    assert p.size_cap_frac == 0.2
    assert p.rr == pytest.approx(10.0, abs=0.1)        # (67000-64000)/300
    # a long mirrors
    pl = execution.manual_plan("BTCUSDC", "long", entry=67000, stop=66700, targets=[68000], size=0.1)
    assert pl.disaster_stop < pl.stop                  # long: disaster BELOW the stop


def test_auto_plan_per_timeframe():
    bars = _bars(_wave())
    p = execution.auto_plan("BTCUSDC", "5m", bars)     # direction=None -> inferred from regime
    assert p.direction in ("long", "short", "flat") and p.follow is True
    assert "5m" in p.rationale
    if p.entries:
        assert p.entries[0].follow is True             # proximal entry = the base (maker-follow)
        assert all(not e.follow for e in p.entries[1:])  # deeper tranches are passive 埋伏 limits
    p2 = execution.auto_plan("BTCUSDC", "4h", bars, direction="short", follow=False)
    assert p2.direction == "short" and p2.follow is False and len(p2.entries) == 3
