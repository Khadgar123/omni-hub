"""Dashboard HTML rendering (network-free): loading state + a full assembled board."""

import math

from quant import dashboard, execution


def _bars(n=80, base=67000.0):
    out = []
    for i in range(n):
        c = base + 800 * math.sin(i / 3.0) + 20 * i
        out.append({"open": c, "high": c + 200, "low": c - 200, "close": c,
                    "volume": 100.0, "bucket_ts": i * 4 * 3600 * 1_000_000})
    return out


def test_render_loading_and_shell():
    assert "启动中" in dashboard.render_panels({"ready": False, "error": None})
    shell = dashboard.render_shell(10)                       # the SPA chart shell
    assert "<html" in shell and "addCandlestickSeries" in shell    # TradingView chart present
    assert "data-tf=1m" in shell and "data-tf=5m" in shell        # 1m/5m switches
    assert "data-tf=1h" not in shell                              # 1h dropped
    assert "非投资建议" in shell


def test_render_full_board():
    plan_long = execution.build_order_plan("BTCUSDC", "long", 0.55, _bars(), rr=5.0).to_dict()
    plan_short = execution.build_order_plan("BTCUSDC", "short", 0.55, _bars(), rr=5.0).to_dict()
    state = {
        "ready": True, "ts": 1700000000, "error": None,
        "paper": {"equity": 10100.0, "pnl_pct": 1.0, "realized_pnl": 50.0, "unrealized": 50.0,
                  "gross_exposure": 2000.0, "positions": {"BNBUSDT": {"qty": 1.5, "avg": 600.0}},
                  "marks": {"BNBUSDT": 620.0}},
        "decision": {"longs": [["BNBUSDT", 0.05]], "shorts": [["UNIUSDT", -0.1]],
                     "gross_scale": 1.3, "regime": "normal"},
        "board": {"order_walls": {"bid_walls": [{"price": 67000, "qty": 100}],
                                  "ask_walls": [{"price": 68000, "qty": 120}]},
                  "funding": {"last": 0.0001, "annualized": 0.1},
                  "open_interest": {"now": 100000, "delta_pct": 0.01, "flush": False},
                  "long_short_ratio": 2.2, "taker_buy_sell": 0.9, "notes": ["测试提示"]},
        "plan_long": plan_long, "plan_short": plan_short,
    }
    board = state.pop("board")                              # board is now per-symbol
    state["symbols"] = {
        "BTCUSDC": {
            "levels": {
                "1d": {"trend": "down", "ref": 67000.0, "atr": 1000.0,
                       "supports": [66000.0, 65000.0, 64000.0], "resistances": [68000.0, 69000.0, 70000.0]},
                "4h": {"trend": "strong_down", "ref": 67000.0, "atr": 500.0,
                       "supports": [66500.0], "resistances": [67500.0]},
            },
            "alignment": {"score": -0.8, "direction": "空", "agree": 2, "n": 2, "label": "全级别一致"},
            "board": board, "plan_long": plan_long, "plan_short": plan_short,
        },
        "ETHUSDC": {"levels": {"4h": {"trend": "down", "ref": 3000.0, "atr": 60.0,
                                      "supports": [2950.0], "resistances": [3050.0]}},
                    "alignment": {"score": -0.6, "direction": "空", "agree": 1, "n": 1, "label": "多数一致"},
                    "board": board, "plan_long": plan_long, "plan_short": plan_short},
    }
    state["trades"] = [{
        "trade": {"id": "BTCUSDC-5m-1", "symbol": "BTCUSDC", "tf": "5m", "note": "5min双头做空",
                  "breakeven_at_r": 1.0,
                  "plan": {"direction": "short", "stop": 67312.0, "disaster_stop": 67547.0,
                           "final_target": 66139.0, "rr": 3.0,
                           "entries": [{"price": 66955.0, "size_frac": 0.4, "role": "entry", "label": "now"},
                                       {"price": 67034.0, "size_frac": 0.35, "role": "entry", "label": "埋伏"}],
                           "targets": [{"price": 66139.0, "size_frac": 1.0, "role": "final", "label": "T"}]}},
        "state": {"status": "active", "total_r": 0.35, "avg": 66992.0, "active_stop": 67312.0,
                  "be_done": False, "filled": [0], "hit": []},
        "breakdown": [{"price": 66955.0, "size_frac": 0.4, "label": "now", "filled": True},
                      {"price": 67034.0, "size_frac": 0.35, "label": "埋伏", "filled": False}],
        "mark": 66888.0,
    }]
    html = dashboard.render_panels(state)
    assert "净值" in html and "$10,100" in html
    assert "BNBUSDT" in html and "UNIUSDT" in html          # basket
    assert "BTCUSDC" in html and "ETHUSDC" in html          # both symbols analyzed
    assert "MTF 趋势对齐" in html and "全级别一致" in html    # top alignment summary
    assert "支撑" in html and "压力" in html                 # multi-level S/R table
    assert "资金费" in html and "买墙" in html               # per-symbol auxiliary board
    assert "若做多" in html and "若做空" in html             # both execution scenarios
    assert "止损" in html and "硬顶" in html                 # stop + disaster cap shown
    assert "持仓(已成交)" in html and "委托(挂单待成交)" in html and "5min双头做空" in html   # 持仓+委托 split
    assert "止损→保本" in html and "立即平仓" in html and "持仓中" in html                  # quick TP/SL edit + status
