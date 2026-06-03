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


def test_save_load_roundtrip(tmp_path):
    st = PaperState(inception_equity=5000.0)
    pt.set_target(st, {"A": 500.0}, {"A": 100.0})
    pt.mark(st, {"A": 120.0})
    p = pt.save_state(st, tmp_path / "paper.json")
    st2 = pt.load_state(p)
    assert round(st2.equity(), 2) == round(st.equity(), 2)
    assert st2.positions == st.positions
