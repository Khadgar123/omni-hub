"""Paper broker: avg-price PnL accounting (open/reduce/flip), neutrality, persist."""

import math

from quant import papertrade as pt
from quant.papertrade import PaperState


def test_open_mark_reduce_flip_pnl():
    st = PaperState(inception_equity=10000.0)
    pt.set_target(st, {"A": 1000.0}, {"A": 100.0})          # long 10 @ 100
    assert st.positions["A"]["qty"] == 10.0
    pt.mark(st, {"A": 110.0})                                # +10/unit
    assert st.equity() == 10100.0 and round(st.unrealized(), 2) == 100.0
    pt.set_target(st, {"A": 550.0}, {"A": 110.0})           # reduce to 5 -> realize 50 on the closed 5
    assert round(st.realized_pnl, 2) == 50.0 and st.positions["A"]["qty"] == 5.0
    assert round(st.equity(), 2) == 10100.0                 # equity continuous across the reduce
    pt.set_target(st, {"A": -550.0}, {"A": 110.0})          # flip to short 5 -> realize the other 50
    assert round(st.realized_pnl, 2) == 100.0
    assert st.positions["A"]["qty"] < 0 and round(st.positions["A"]["avg"], 2) == 110.0


def test_basket_is_dollar_neutral():
    st = PaperState()
    pt.set_target(st, {"A": 1000.0, "B": -1000.0}, {"A": 100.0, "B": 50.0})
    net = sum(p["qty"] * st.marks[s] for s, p in st.positions.items())
    assert abs(net) < 1e-6                                   # Σ long $ = Σ short $
    assert st.positions["A"]["qty"] == 10.0 and st.positions["B"]["qty"] == -20.0


def test_closing_removes_position():
    st = PaperState()
    pt.set_target(st, {"A": 1000.0}, {"A": 100.0})
    pt.set_target(st, {"A": 0.0}, {"A": 105.0})             # flat
    assert "A" not in st.positions and round(st.realized_pnl, 2) == 50.0


def test_fee_deducted():
    st = PaperState(inception_equity=10000.0, fee_bps=10.0)  # 10bp
    pt.set_target(st, {"A": 1000.0}, {"A": 100.0})          # fee = 1000*0.001 = 1.0
    assert round(st.realized_pnl, 2) == -1.0


def test_tick_baseline_forms_basket():
    prices = {}
    for i in range(6):
        drift = 0.015 - 0.006 * i
        prices[f"C{i}USDT"] = {d: 100.0 * ((1 + drift) ** d) * (1 + 0.015 * math.sin(d / 4.0 + i))
                               for d in range(40)}
    from quant.baseline import BaselineConfig
    st = PaperState()
    dec = pt.tick_baseline(st, prices, BaselineConfig(k=2))
    assert dec.longs and dec.shorts
    assert len(st.positions) == 4                            # 2 long + 2 short
    # strongest coin is long (qty>0), weakest is short (qty<0)
    assert st.positions["C0USDT"]["qty"] > 0
    assert st.positions["C5USDT"]["qty"] < 0


def test_discretionary_book_record_and_evaluate(tmp_path):
    from quant.execution import OrderLeg, OrderPlan
    plan = OrderPlan(asof=0, symbol="X", direction="short", conviction=0.5, ref_price=100.0, atr=2.0,
                     entries=[OrderLeg(100.0, 1.0, "entry", "now")], stop=103.0, stop_kind="double_top",
                     risk_dist=3.0, targets=[OrderLeg(94.0, 1.0, "final", "T")], final_target=94.0,
                     rr=2.0, size_cap_frac=1.0, mandatory_stop_rule="", mandatory_tp_rule="",
                     rationale="", manage_style="runner", disaster_stop=0.0, maker_bps=0.0)
    book = str(tmp_path / "book.json")
    tid = pt.record_trade(book, plan.to_dict(), symbol="X", tf="5m", since_ts=10, note="5min双头")
    assert pt.load_book(book)[0]["id"] == tid
    fut = [{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1, "bucket_ts": 10},
           {"open": 100, "high": 100, "low": 93, "close": 94, "volume": 1, "bucket_ts": 11}]
    res = pt.evaluate_trade(pt.load_book(book)[0], fut)
    assert res["filled"] == 1 and res["exit_reason"] == "target"   # short filled @100, hit 94
    # bars BEFORE since_ts are ignored (causal)
    assert pt.evaluate_trade({"plan": plan.to_dict(), "since_ts": 999}, fut)["exit_reason"] == "pending"


def _long_managed_plan():
    from quant.execution import OrderLeg, OrderPlan
    return OrderPlan(asof=0, symbol="X", direction="long", conviction=0.5, ref_price=100.0, atr=2.0,
                     entries=[OrderLeg(100.0, 1.0, "entry", "now")], stop=97.0, stop_kind="x",
                     risk_dist=3.0, targets=[OrderLeg(110.0, 1.0, "final", "T")], final_target=110.0,
                     rr=3.3, size_cap_frac=1.0, mandatory_stop_rule="", mandatory_tp_rule="",
                     rationale="", manage_style="runner", disaster_stop=94.0, maker_bps=0.0).to_dict()


def test_advance_auto_breakeven_protects_profit():
    trade = {"id": "t", "symbol": "X", "tf": "5m", "since_ts": 10, "plan": _long_managed_plan(),
             "breakeven_at_r": 1.0, "overrides": {}}
    bars = [
        {"open": 100, "high": 100.5, "low": 99.8, "close": 100, "bucket_ts": 10},   # fill long @100
        {"open": 100, "high": 103.2, "low": 100, "close": 103, "bucket_ts": 11},    # +1R -> stop to breakeven
        {"open": 103, "high": 103, "low": 99, "close": 99.5, "bucket_ts": 12},      # pull back: stop @ breakeven
    ]
    st = pt.advance_trade(trade, bars)
    assert st["be_done"] is True and st["active_stop"] == 100.0
    assert st["status"] == "stopped" and abs(st["realized_r"]) < 0.05    # ~0R, a winner did NOT become a loss


def test_advance_without_breakeven_would_not_stop_there():
    # same bars but breakeven disabled -> the 99.5 close does NOT hit the original 97 stop (stays open)
    trade = {"id": "t", "symbol": "X", "tf": "5m", "since_ts": 10, "plan": _long_managed_plan(),
             "breakeven_at_r": 99.0, "overrides": {}}
    bars = [{"open": 100, "high": 100.5, "low": 99.8, "close": 100, "bucket_ts": 10},
            {"open": 100, "high": 103.2, "low": 100, "close": 103, "bucket_ts": 11},
            {"open": 103, "high": 103, "low": 99, "close": 99.5, "bucket_ts": 12}]
    st = pt.advance_trade(trade, bars)
    assert st["status"] == "active" and st["position"] == 1.0           # not stopped (97 not hit)


def test_modify_trade_trails_stop(tmp_path):
    book = str(tmp_path / "b.json")
    pt.record_trade(book, _long_managed_plan(), symbol="X", tf="5m", since_ts=10, note="t")
    tid = pt.load_book(book)[0]["id"]
    pt.modify_trade(book, tid, stop=101.0, targets=[{"price": 108.0, "size_frac": 1.0, "role": "final", "label": "T"}])
    tr = pt.load_book(book)[0]
    assert tr["plan"]["stop"] == 101.0 and tr["plan"]["final_target"] == 108.0   # written to plan (single source)
    bars = [{"open": 100, "high": 100.5, "low": 99.8, "close": 100, "bucket_ts": 10},
            {"open": 100, "high": 102, "low": 99.5, "close": 99.6, "bucket_ts": 11}]   # close 99.6 <= 101
    st = pt.advance_trade(tr, bars)
    assert st["status"] == "stopped" and st["realized_r"] > 0           # stopped @101 = locked +profit


def test_advance_follow_base_fills_at_market(tmp_path):
    from quant.execution import OrderLeg, OrderPlan
    plan = OrderPlan(asof=0, symbol="X", direction="long", conviction=0.5, ref_price=100.0, atr=2.0,
                     entries=[OrderLeg(99.0, 1.0, "entry", "base", follow=True)], stop=96.0, stop_kind="x",
                     risk_dist=4.0, targets=[OrderLeg(110.0, 1.0, "final", "T")], final_target=110.0, rr=2.5,
                     size_cap_frac=1.0, mandatory_stop_rule="", mandatory_tp_rule="", rationale="",
                     manage_style="runner", disaster_stop=92.0, maker_bps=0.0).to_dict()
    trade = {"id": "t", "symbol": "X", "tf": "5m", "since_ts": 10, "plan": plan, "overrides": {}}
    bars = [{"open": 100, "high": 100.5, "low": 99.8, "close": 100.2, "bucket_ts": 10}]  # never dips to 99
    st = pt.advance_trade(trade, bars)
    assert 0 in st["filled"] and st["position"] == 1.0   # base filled despite NOT touching the 99 limit
    assert abs(st["avg"] - 100.2) < 0.01                 # filled at market (close), the follow chased


def test_pending_intent_emit_approve_reject(tmp_path):
    ip, bp = str(tmp_path / "intents.json"), str(tmp_path / "book.json")
    plan = _long_managed_plan()
    iid = pt.emit_pending(ip, plan, symbol="X", tf="5m", created_ts=100, note="t", ttl_sec=600)
    assert len(pt.load_intents(ip)) == 1 and pt.load_intents(ip)[0]["status"] == "pending"
    iid2 = pt.emit_pending(ip, plan, symbol="Y", tf="5m", created_ts=101, note="t2")
    pt.reject_intent(ip, iid2)                                  # reject removes it
    assert all(x["id"] != iid2 for x in pt.load_intents(ip))
    tid = pt.approve_intent(ip, bp, iid, since_ts=200)         # approve -> trade book + removed from intents
    assert pt.load_book(bp)[0]["id"] == tid
    assert pt.load_book(bp)[0]["since_ts"] == 200
    assert all(x["id"] != iid for x in pt.load_intents(ip))


def test_save_load_roundtrip(tmp_path):
    st = PaperState(inception_equity=5000.0)
    pt.set_target(st, {"A": 500.0}, {"A": 100.0})
    pt.mark(st, {"A": 120.0})
    p = pt.save_state(st, tmp_path / "paper.json")
    st2 = pt.load_state(p)
    assert round(st2.equity(), 2) == round(st.equity(), 2)
    assert st2.positions == st.positions
