"""data.binance.vision bulk dump ingester: pure mappers + injected fetch/write.

Network-free: a synthetic in-memory ZIP + CHECKSUM is fed via an injected
fetcher; the write is captured. The 2025 µs timestamp switch and header rows
are exercised explicitly.
"""

import csv
import hashlib
import importlib.util
import io
import sys
import zipfile
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "agent-harness" / "integrations" / "finance" / "binance_vision.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("binance_vision", MODULE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bv = load_module()


def _zip(rows, csv_name="BTCUSDT-1s-2025-04.csv"):
    sbuf = io.StringIO()
    csv.writer(sbuf).writerows(rows)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(csv_name, sbuf.getvalue())
    return zbuf.getvalue()


_MS_ROW = ["1716950400000", "67000", "68000", "66000", "67500", "10",
           "1716950400999", "675000", "42", "0", "0", "0"]
_US_ROW = ["1748736000000000", "1", "2", "0", "1.5", "4",
           "1748736000999000", "6", "3", "0", "0", "0"]
_HEADER = ["open_time", "open", "high", "low", "close", "volume",
           "close_time", "quote_volume", "count", "tbb", "tbq", "ignore"]


def test_to_millis_handles_micros_switch():
    assert bv.to_millis("1716950400000") == 1716950400000
    assert bv.to_millis("1748736000000000") == 1748736000000  # µs -> ms


def test_kline_csv_row_to_bar():
    bar = bv.kline_csv_row_to_bar(_MS_ROW, "BTCUSDT")
    assert bar["bucket_ts"] == 1716950400000 * 1000  # ms -> µs
    assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (67000.0, 68000.0, 66000.0, 67500.0)
    assert bar["trades"] == 42
    assert bar["vwap"] == pytest.approx(675000 / 10)


def test_bars_from_zip_skips_header_and_normalizes():
    bars = bv.bars_from_zip(_zip([_HEADER, _MS_ROW, _US_ROW]), "BTCUSDT")
    assert len(bars) == 2
    assert bars[0]["bucket_ts"] == 1716950400000 * 1000
    assert bars[1]["bucket_ts"] == 1748736000000 * 1000  # µs row normalized


def test_verify_checksum_ok_and_mismatch():
    z = _zip([_MS_ROW])
    sha = hashlib.sha256(z).hexdigest()
    ok, _, _ = bv.verify_checksum(z, f"{sha}  BTCUSDT-1s-2025-04.csv")
    assert ok
    bad, _, _ = bv.verify_checksum(z, "deadbeef  x.csv")
    assert not bad


def test_backfill_month_fetch_and_write_captured():
    z = _zip([_MS_ROW, _US_ROW])
    sha = hashlib.sha256(z).hexdigest()

    def fake(url, timeout=120.0, **kw):
        return z if url.endswith(".zip") else f"{sha}  f.csv".encode()

    cap = {}

    def write_fn(bars, symbol, interval, root):
        cap.update(bars=bars, symbol=symbol, interval=interval)
        return ["part-0"]

    res = bv.backfill_month("BTCUSDT", "1s", 2025, 4, root="/tmp/x",
                            fetcher=fake, write_fn=write_fn)
    assert res["bars"] == 2 and res["partitions"] == 1
    assert cap["symbol"] == "BTCUSDT" and cap["interval"] == "1s"
    assert cap["bars"][1]["bucket_ts"] == 1748736000000 * 1000


def test_backfill_month_checksum_mismatch_raises():
    z = _zip([_MS_ROW])

    def fake(url, timeout=120.0, **kw):
        return z if url.endswith(".zip") else b"deadbeef  f.csv"

    with pytest.raises(bv.BinanceVisionError):
        bv.backfill_month("BTCUSDT", "1s", 2025, 4, fetcher=fake, write_fn=lambda *a: [])


def test_backfill_respects_storage_budget(tmp_path):
    def must_not_fetch(*a, **k):
        raise AssertionError("budget exceeded -> must not fetch")

    res = bv.backfill(["BTCUSDT"], "1s", "2025-01", "2025-03",
                      root=tmp_path, fetcher=must_not_fetch, max_bytes=0)
    assert any("stopped" in r for r in res)


def test_iter_months_wraps_year():
    assert list(bv.iter_months((2024, 11), (2025, 2))) == [(2024, 11), (2024, 12), (2025, 1), (2025, 2)]
