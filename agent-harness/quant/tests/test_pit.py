"""Point-in-time correctness: no look-ahead, corp actions, delisting, calendar."""

from __future__ import annotations

import pytest

duckdb = pytest.importorskip("duckdb")
pyarrow = pytest.importorskip("pyarrow")

from quant import market_store as ms  # noqa: E402


def test_last_price_no_lookahead(store):
    assert ms.last_price("DEMO", "2026-01-02", root=store) == 100.5  # last of day 1
    assert ms.last_price("DEMO", "2026-01-03", root=store) == 50.0   # last of day 2
    assert ms.last_price("DEMO", "2026-01-01", root=store) is None   # before any trade


def test_corporate_actions_asof_gate(store):
    # the 2:1 split has event_date 2026-01-03 -> invisible at asof 2026-01-02
    assert ms.corporate_actions_for("DEMO", "2026-01-02", root=store) == []
    acts = ms.corporate_actions_for("DEMO", "2026-01-03", root=store)
    assert len(acts) == 1 and acts[0]["type"] == "split" and acts[0]["ratio"] == 2.0


def _persist_daily_bars(store):
    tr = ms.trades("DEMO", "2026-01-02", "2026-01-03", root=store)
    ms.write_bars(ms.bars_from_trades(tr, freq="1d", symbol="DEMO"),
                  symbol="DEMO", freq="1d", root=store)


def test_split_back_adjustment_is_asof_gated(store):
    _persist_daily_bars(store)
    # asof before the split: action unknown -> no adjustment
    pre = ms.bars("DEMO", "1d", "2026-01-01", "2026-01-10", root=store,
                  asof="2026-01-02", adjust=True)
    assert len(pre) == 1 and pre[0]["close"] == 100.5 and "adjusted" not in pre[0]
    # asof on/after the split: day-1 back-adjusts by /2 (price) and x2 (volume)
    post = ms.bars("DEMO", "1d", "2026-01-01", "2026-01-10", root=store,
                   asof="2026-01-03", adjust=True)
    assert post[0]["close"] == pytest.approx(50.25)
    assert post[0]["open"] == pytest.approx(50.0)
    assert post[0]["volume"] == pytest.approx(18.0)
    assert post[0]["adjusted"] is True
    # the split-day bar itself (event_date not strictly after it) is unchanged
    assert post[1]["close"] == pytest.approx(50.0) and "adjusted" not in post[1]


def test_unadjusted_read_is_raw(store):
    _persist_daily_bars(store)
    raw = ms.bars("DEMO", "1d", "2026-01-01", "2026-01-10", root=store)
    assert raw[0]["close"] == 100.5  # no adjust -> raw truth


def test_listings_retain_delisted_and_asof(store):
    # delisted OLDCO is RETAINED in the master (anti-survivorship)
    rows = ms.listings_asof("2026-01-03", root=store)
    by_sym = {r["symbol"]: r for r in rows}
    assert "OLDCO" in by_sym and by_sym["OLDCO"]["delist_date"] == "2025-06-30"
    assert by_sym["OLDCO"]["is_live"] is False
    assert by_sym["DEMO"]["is_live"] is True
    # point-in-time universe (no survivorship bias)
    assert ms.live_symbols("2026-01-03", root=store) == ["DEMO"]
    assert ms.live_symbols("2025-01-01", root=store) == ["OLDCO"]


def test_trading_calendar_open_filter(store):
    sess = ms.trading_sessions("2026-01-01", "2026-01-05", root=store, venue="XNYS")
    assert [s["date"] for s in sess] == ["2026-01-02", "2026-01-05"]  # holiday + weekend closed
    alld = ms.trading_sessions("2026-01-01", "2026-01-05", root=store,
                               venue="XNYS", open_only=False)
    assert len(alld) == 5
    assert sess[0]["open_ts"] > 0 and sess[0]["close_ts"] > sess[0]["open_ts"]
