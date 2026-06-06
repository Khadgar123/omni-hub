"""CLI seam: `python -m quant.market_store ...` emits pure JSON on stdout."""

from __future__ import annotations

import contextlib
import io
import json

import pytest

duckdb = pytest.importorskip("duckdb")
pyarrow = pytest.importorskip("pyarrow")

from quant import market_store as ms  # noqa: E402


def run_cli(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = ms.main(argv)
    return rc, buf.getvalue()


def test_ingest_sample_then_full_read_flow(tmp_path):
    root = str(tmp_path / "market")

    rc, out = run_cli(["--root", root, "ingest-sample"])
    assert rc == 0
    assert json.loads(out)[0]["trades"] == 9

    rc, out = run_cli(["--root", root, "bars-from-trades", "--symbol", "DEMO",
                       "--freq", "1d", "--start", "2026-01-01", "--end", "2026-01-10",
                       "--persist"])
    assert rc == 0
    bars = json.loads(out)
    assert len(bars) == 2 and bars[0]["close"] == 100.5

    rc, out = run_cli(["--root", root, "bars", "--symbol", "DEMO", "--freq", "1d",
                       "--start", "2026-01-01", "--end", "2026-01-10",
                       "--asof", "2026-01-03", "--adjust"])
    bars = json.loads(out)
    assert bars[0]["close"] == pytest.approx(50.25) and bars[0]["adjusted"] is True

    rc, out = run_cli(["--root", root, "last-price", "--symbol", "DEMO",
                       "--asof", "2026-01-02"])
    assert json.loads(out)[0]["last_price"] == 100.5


def test_listings_live_only_and_corp_actions(tmp_path):
    root = str(tmp_path / "market")
    run_cli(["--root", root, "ingest-sample"])

    rc, out = run_cli(["--root", root, "listings", "--asof", "2026-01-03", "--live-only"])
    assert [r["symbol"] for r in json.loads(out)] == ["DEMO"]

    rc, out = run_cli(["--root", root, "corporate-actions", "--symbol", "DEMO",
                       "--asof", "2026-01-03"])
    assert json.loads(out)[0]["type"] == "split"

    rc, out = run_cli(["--root", root, "calendar", "--start", "2026-01-01",
                       "--end", "2026-01-05", "--venue", "XNYS"])
    assert [r["date"] for r in json.loads(out)] == ["2026-01-02", "2026-01-05"]


def test_csv_format(tmp_path):
    root = str(tmp_path / "market")
    run_cli(["--root", root, "ingest-sample"])
    rc, out = run_cli(["--root", root, "--format", "csv", "trades",
                       "--symbol", "DEMO", "--start", "2026-01-02", "--end", "2026-01-02"])
    lines = [ln for ln in out.splitlines() if ln]
    assert lines[0].startswith("exchange_ts,")  # header
    assert len(lines) == 1 + 6  # header + 6 day-1 trades
