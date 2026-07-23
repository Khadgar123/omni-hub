from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import re

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import quant.discord_backtest as discord_backtest
from quant.discord_backtest import main, read_closed_1m_bars, simulate_lifecycles


MINUTE_US = 60_000_000


def _us(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1_000_000)


def _bar(
    minute: int,
    *,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.0,
    symbol: str = "BTCUSDT",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "bucket_ts": _us("2026-01-01T10:00:00Z") + minute * MINUTE_US,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1.0,
        "vwap": close,
        "trades": 1,
    }


def _lifecycle(
    lifecycle_id: str = "trade-1",
    *,
    effective_at: str = "2026-01-01T10:00:30Z",
    direction: str = "long",
    symbol: str = "BTCUSDT",
    entry: float | None = None,
    entry_low: float | None = None,
    entry_high: float | None = None,
    sl: float = 90.0,
    tps: list[float] | None = None,
    **extra: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "lifecycle_id": lifecycle_id,
        "profile": "coin-chief-v1",
        "symbol": symbol,
        "direction": direction,
        "effective_at": effective_at,
        "entry": entry,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "sl": sl,
        "tps": [110.0] if tps is None else tps,
        "evaluable": True,
        "confidence": "high",
    }
    row.update(extra)
    return row


def _simulate(
    lifecycles,
    bars,
    *,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    max_entry_wait_minutes: int = 1440,
):
    return simulate_lifecycles(
        lifecycles=lifecycles,
        bars=bars,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        max_entry_wait_minutes=max_entry_wait_minutes,
    )


def test_market_entry_uses_next_full_bar_and_never_signal_bar() -> None:
    bars = [
        _bar(0, open_=50, high=120, low=40, close=100),
        _bar(1, open_=100, high=105, low=99, close=104),
        _bar(2, open_=104, high=111, low=103, close=110),
    ]

    trade = _simulate([_lifecycle()], bars)["trades"][0]

    assert trade["entry_bucket_ts"] == _bar(1)["bucket_ts"]
    assert trade["entry_price"] == 100.0
    assert trade["exit_reason"] == "take_profit"


def test_exact_minute_signal_still_waits_for_following_bar() -> None:
    lifecycle = _lifecycle(effective_at="2026-01-01T10:00:00Z")
    trade = _simulate(
        [lifecycle],
        [_bar(0, high=120), _bar(1, open_=100), _bar(2, high=111)],
    )["trades"][0]
    assert trade["entry_bucket_ts"] == _bar(1)["bucket_ts"]


def test_missing_exact_next_bar_is_data_gap_not_a_late_entry() -> None:
    trade = _simulate(
        [_lifecycle()],
        [_bar(2, open_=100, high=111, low=95)],
    )["trades"][0]

    assert trade["status"] == "excluded"
    assert trade["exclusion_reason"] == "market_data_gap_at_entry_start"
    assert trade["entry_price"] is None


def test_limit_zone_never_falls_back_to_market_when_untouched() -> None:
    lifecycle = _lifecycle(entry_low=95.0, entry_high=97.0, sl=90.0, tps=[110.0])
    bars = [_bar(0), _bar(1, open_=100, high=105, low=98), _bar(2, open_=120, high=130, low=118, close=120)]

    trade = _simulate([lifecycle], bars, max_entry_wait_minutes=2)["trades"][0]

    assert trade["status"] == "unfilled"
    assert trade["entry_price"] is None
    assert trade["exclusion_reason"] == "unfilled_expired_assumption"


@pytest.mark.parametrize(
    ("direction", "zone", "bar", "expected_raw"),
    [
        ("long", (95.0, 97.0), {"high": 100.0, "low": 96.0}, 97.0),
        ("short", (103.0, 105.0), {"high": 104.0, "low": 100.0}, 103.0),
    ],
)
def test_zone_fill_uses_directionally_adverse_intersection_and_slippage(
    direction: str,
    zone: tuple[float, float],
    bar: dict[str, float],
    expected_raw: float,
) -> None:
    sl = 90.0 if direction == "long" else 110.0
    targets = [110.0] if direction == "long" else [90.0]
    lifecycle = _lifecycle(
        direction=direction,
        entry_low=zone[0],
        entry_high=zone[1],
        sl=sl,
        tps=targets,
    )
    inside = 99.0 if direction == "long" else 102.0
    fill_bar = _bar(1, open_=inside, high=bar["high"], low=bar["low"], close=inside)

    trade = _simulate([lifecycle], [_bar(0), fill_bar], slippage_bps=100)["trades"][0]

    multiplier = 1.01 if direction == "long" else 0.99
    assert trade["entry_price"] == pytest.approx(expected_raw * multiplier)
    assert trade["status"] == "right_censored"


def test_cancel_before_first_eligible_fill_is_unfilled() -> None:
    lifecycle = _lifecycle(
        entry_low=95.0,
        entry_high=97.0,
        cancel_effective_at="2026-01-01T10:00:45Z",
    )
    trade = _simulate([lifecycle], [_bar(0), _bar(1, low=95.0)])["trades"][0]

    assert trade["status"] == "unfilled"
    assert trade["exclusion_reason"] == "cancelled_before_fill"


def test_market_entry_precedes_a_cancel_later_in_the_same_bar() -> None:
    lifecycle = _lifecycle(cancel_effective_at="2026-01-01T10:01:30Z")
    trade = _simulate(
        [lifecycle],
        [_bar(0), _bar(1, open_=100, high=105, low=95), _bar(2, high=111, low=95)],
    )["trades"][0]

    assert trade["entry_bucket_ts"] == _bar(1)["bucket_ts"]
    assert trade["status"] == "closed"


def test_limit_touch_and_intrabar_cancel_is_explicitly_ambiguous() -> None:
    lifecycle = _lifecycle(
        entry_low=95.0,
        entry_high=97.0,
        cancel_effective_at="2026-01-01T10:01:30Z",
    )
    trade = _simulate([lifecycle], [_bar(0), _bar(1, low=96.0)])["trades"][0]

    assert trade["status"] == "excluded"
    assert trade["exclusion_reason"] == "limit_touch_cancel_intrabar_ambiguous"


@pytest.mark.parametrize("terminal_status", ["cancelled_unfilled", "expired_unreported"])
def test_curated_terminal_without_timestamp_is_excluded_not_future_leaked(
    terminal_status: str,
) -> None:
    lifecycle = _lifecycle(
        entry=100.0,
        terminal_status=terminal_status,
        open_message_id="123",
        parameter_fingerprint="a" * 64,
        confidence="high",
        explicit_reference_ids=[],
        link_basis="standalone_open",
    )
    lifecycle.pop("lifecycle_id")

    trade = _simulate([lifecycle], [_bar(0), _bar(1, low=90.0)])["trades"][0]

    assert trade["lifecycle_id"] == "123"
    assert trade["status"] == "excluded"
    assert trade["exclusion_reason"] == "excluded_known_cancel_or_expiry"


def test_limit_entry_wait_expires_without_a_stale_late_fill() -> None:
    lifecycle = _lifecycle(entry_low=95.0, entry_high=97.0, sl=90.0, tps=[110.0])
    bars = [
        _bar(0),
        _bar(1, high=105, low=98),
        _bar(2, high=105, low=95),
    ]

    report = _simulate([lifecycle], bars, max_entry_wait_minutes=2)
    trade = report["trades"][0]

    assert trade["status"] == "unfilled"
    assert trade["exclusion_reason"] == "unfilled_expired_assumption"
    assert report["parameters"]["max_entry_wait_minutes"] == 2


def test_expiry_precedes_a_later_cancel() -> None:
    lifecycle = _lifecycle(
        entry_low=95.0,
        entry_high=97.0,
        cancel_effective_at="2026-01-01T10:02:00Z",
    )
    trade = _simulate(
        [lifecycle],
        [_bar(0), _bar(1, low=98.0), _bar(2, low=95.0)],
        max_entry_wait_minutes=1,
    )["trades"][0]

    assert trade["status"] == "unfilled"
    assert trade["exclusion_reason"] == "unfilled_expired_assumption"


def test_entry_bar_can_stop_but_never_counts_take_profit() -> None:
    lifecycle = _lifecycle(sl=90.0, tps=[110.0])
    trade = _simulate(
        [lifecycle],
        [_bar(0), _bar(1, open_=100, high=120, low=80, close=100)],
    )["trades"][0]

    assert trade["status"] == "closed"
    assert trade["exit_reason"] == "stop_loss"
    assert trade["tp_fills"] == []


def test_later_bar_with_stop_and_target_is_stop_first() -> None:
    trade = _simulate(
        [_lifecycle()],
        [_bar(0), _bar(1, open_=100, high=105, low=95), _bar(2, high=120, low=80)],
    )["trades"][0]

    assert trade["status"] == "closed"
    assert trade["exit_reason"] == "stop_loss"
    assert trade["tp_fills"] == []


def test_stop_gap_uses_adverse_bar_open_before_slippage() -> None:
    trade = _simulate(
        [_lifecycle()],
        [
            _bar(0),
            _bar(1, open_=100, high=105, low=95),
            _bar(2, open_=80, high=85, low=75, close=82),
        ],
        slippage_bps=100,
    )["trades"][0]

    assert trade["exit_reason"] == "stop_loss"
    assert trade["net_return_pct"] == pytest.approx((80 * 0.99 - 100 * 1.01) / (100 * 1.01) * 100)


def test_explicit_multiple_targets_are_equal_weight_and_fees_are_two_sided() -> None:
    lifecycle = _lifecycle(tps=[105.0, 110.0])
    bars = [
        _bar(0),
        _bar(1, open_=100, high=104, low=95, close=103),
        _bar(2, open_=103, high=106, low=95, close=105),
        _bar(3, open_=105, high=111, low=100, close=110),
    ]

    trade = _simulate([lifecycle], bars, fee_bps=10, slippage_bps=100)["trades"][0]

    assert [fill["fraction"] for fill in trade["tp_fills"]] == [0.5, 0.5]
    assert [fill["exit_price"] for fill in trade["tp_fills"]] == pytest.approx([103.95, 108.9])
    entry = 101.0
    gross = ((103.95 - entry) * 0.5 + (108.9 - entry) * 0.5) / entry * 100
    fees = (entry + 103.95 * 0.5 + 108.9 * 0.5) * 0.001 / entry * 100
    slippage = (1.0 + 1.05 * 0.5 + 1.1 * 0.5) / entry * 100
    assert trade["gross_return_pct"] == pytest.approx(gross)
    assert trade["fees_pct"] == pytest.approx(fees)
    assert trade["slippage_pct"] == pytest.approx(slippage)
    assert trade["net_return_pct"] == pytest.approx(gross - fees)
    assert trade["remaining_fraction"] == 0.0


@pytest.mark.parametrize(
    ("lifecycle", "reason"),
    [
        (_lifecycle(tps=[]), "missing_explicit_take_profits"),
        (_lifecycle(entry_low=95.0, entry_high=97.0, sl=96.0), "invalid_price_geometry"),
        (_lifecycle(direction="short", entry_low=103.0, entry_high=105.0, sl=104.0, tps=[90.0]), "invalid_price_geometry"),
        (_lifecycle(symbol="SOLUSDT"), "unsupported_symbol"),
    ],
)
def test_unsupported_or_invalid_lifecycle_is_explicitly_excluded(
    lifecycle: dict[str, object], reason: str
) -> None:
    trade = _simulate([lifecycle], [_bar(0), _bar(1)])["trades"][0]
    assert trade["status"] == "excluded"
    assert trade["exclusion_reason"] == reason


def test_source_exclusion_is_preserved_without_validating_trade_geometry() -> None:
    lifecycle = _lifecycle(
        entry_low=200.0,
        entry_high=100.0,
        evaluable=False,
        exclusion_reason="duplicate_parameter_fingerprint",
    )
    trade = _simulate([lifecycle], [_bar(0), _bar(1)])["trades"][0]
    assert trade["status"] == "excluded"
    assert trade["exclusion_reason"] == "duplicate_parameter_fingerprint"


def test_duplicate_marker_has_stable_exclusion_reason() -> None:
    lifecycle = _lifecycle(
        duplicate_of="other",
        exclusion_reason="exact_duplicate_open",
    )
    trade = _simulate([lifecycle], [_bar(0), _bar(1)])["trades"][0]
    assert trade["status"] == "excluded"
    assert trade["exclusion_reason"] == "excluded_duplicate"


def test_source_exclusion_reason_blocks_even_if_evaluable_is_true() -> None:
    lifecycle = _lifecycle(exclusion_reason="terminal_event_conflict")
    trade = _simulate([lifecycle], [_bar(0), _bar(1)])["trades"][0]
    assert trade["status"] == "excluded"
    assert trade["exclusion_reason"] == "terminal_event_conflict"


def test_non_high_confidence_is_retained_but_never_executed() -> None:
    lifecycle = _lifecycle(confidence="medium")
    trade = _simulate([lifecycle], [_bar(0), _bar(1), _bar(2, high=120)])["trades"][0]

    assert trade["status"] == "excluded"
    assert trade["exclusion_reason"] == "excluded_low_confidence"


@pytest.mark.parametrize(
    "change",
    [
        {"lifecycle_id": "https://example.invalid/secret"},
        {"profile": "unknown-profile"},
        {"exclusion_reason": "invented_reason"},
        {"explicit_reference_ids": ["Bearer secret-value"]},
        {"lifecycle_id": ".".join(("x" * 24, "x" * 6, "x" * 30))},
        {"evaluable": 1},
        {"unexpected_field": "safe-looking-but-not-in-schema"},
    ],
)
def test_lifecycle_schema_and_sensitive_strings_fail_closed(change: dict[str, object]) -> None:
    lifecycle = _lifecycle()
    lifecycle.update(change)
    with pytest.raises(ValueError, match="lifecycle"):
        _simulate([lifecycle], [_bar(0), _bar(1)])


def test_unsupported_symbol_is_excluded_without_echoing_arbitrary_input() -> None:
    lifecycle = _lifecycle(symbol="SECRET-PAIR")
    trade = _simulate([lifecycle], [_bar(0), _bar(1)])["trades"][0]
    assert trade["status"] == "excluded"
    assert trade["exclusion_reason"] == "unsupported_symbol"
    assert trade["symbol"] is None


def test_curated_semantic_fingerprint_format_is_allowed() -> None:
    lifecycle = _lifecycle(parameter_fingerprint="BTCUSDT|long|100,101-90")
    trade = _simulate([lifecycle], [_bar(0), _bar(1), _bar(2, high=111)])["trades"][0]
    assert trade["status"] == "closed"


def test_fingerprint_only_identity_is_hashed_before_publication() -> None:
    lifecycle = _lifecycle(parameter_fingerprint="BTCUSDT|long|100,101-90")
    lifecycle.pop("lifecycle_id")
    lifecycle["open_message_id"] = None

    trade = _simulate([lifecycle], [_bar(0), _bar(1), _bar(2, high=111)])[
        "trades"
    ][0]

    assert re.fullmatch(r"[0-9a-f]{64}", str(trade["lifecycle_id"]))
    assert trade["lifecycle_id"] != lifecycle["parameter_fingerprint"]


@pytest.mark.parametrize(
    ("bars", "match"),
    [
        ([_bar(0), _bar(2)], "gap"),
        ([_bar(0), _bar(0)], "duplicate"),
        ([_bar(0), {**_bar(1), "bucket_ts": int(_bar(1)["bucket_ts"]) + 1}], "aligned"),
        ([_bar(0), _bar(1, high=98.0, low=99.0)], "OHLC"),
    ],
)
def test_bad_market_data_fails_closed(bars: list[dict[str, object]], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _simulate([_lifecycle()], bars)


def test_right_censored_and_unfilled_never_enter_win_rate_denominator() -> None:
    closed = _lifecycle("closed", tps=[105.0])
    censored = _lifecycle("censored", tps=[120.0])
    unfilled = _lifecycle("unfilled", entry_low=80.0, entry_high=85.0, sl=70.0, tps=[100.0])
    report = _simulate(
        [unfilled, censored, closed],
        [_bar(0), _bar(1, open_=100, high=104, low=95), _bar(2, high=106, low=95)],
        max_entry_wait_minutes=2,
    )

    assert report["summary"] == {
        "lifecycles": 3,
        "closed": 1,
        "wins": 1,
        "losses": 0,
        "flat": 0,
        "unfilled": 1,
        "right_censored": 1,
        "excluded": 0,
        "win_rate": 1.0,
    }
    assert report["funding"] == "unmodeled"


def test_output_is_deterministic_and_sorted_independent_of_lifecycle_order() -> None:
    bars = [_bar(0), _bar(1, open_=100, high=105, low=95), _bar(2, high=111, low=95)]
    later = _lifecycle("later", effective_at="2026-01-01T10:00:45Z")
    earlier = _lifecycle("earlier", effective_at="2026-01-01T10:00:15Z")

    first = _simulate([later, earlier], bars, fee_bps=4, slippage_bps=2)
    second = _simulate([earlier, later], bars, fee_bps=4, slippage_bps=2)

    assert first == second
    assert [trade["lifecycle_id"] for trade in first["trades"]] == ["earlier", "later"]


def _write_bar_partition(
    root: Path,
    symbol: str,
    day: str,
    rows: list[dict[str, object]],
    *,
    name: str = "part-00000.parquet",
) -> Path:
    target = root / "bars_1m" / f"symbol={symbol}" / f"date={day}" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = [{key: row[key] for key in ("bucket_ts", "open", "high", "low", "close", "volume", "vwap", "trades")} for row in rows]
    pq.write_table(pa.Table.from_pylist(payload), target)
    return target


def _files_aggregate(entries: list[dict[str, object]]) -> str:
    tuples = [[item["path"], item["size_bytes"], item["sha256"]] for item in entries]
    return hashlib.sha256(
        json.dumps(
            tuples,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _market_manifest(
    tmp_path: Path,
    root: Path,
    files: list[Path],
    *,
    start_us: int | None = None,
    end_us: int | None = None,
    mutate: dict[str, object] | None = None,
) -> tuple[Path, str, dict[str, object]]:
    entries = []
    for path in sorted(files):
        data = path.read_bytes()
        entries.append({
            "path": path.relative_to(root).as_posix(),
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    aggregate = _files_aggregate(entries)
    manifest: dict[str, object] = {
        "schema_version": "market-input-request-v1",
        "market_root": str(root.absolute()),
        "requested_start_us": int(_bar(1)["bucket_ts"]) if start_us is None else start_us,
        "requested_end_us": int(_bar(3)["bucket_ts"]) if end_us is None else end_us,
        "files": entries,
        "files_aggregate_sha256": aggregate,
    }
    if mutate:
        manifest.update(mutate)
    manifest_path = tmp_path / "market-input-manifest.json"
    raw = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    manifest_path.write_bytes(raw)
    return manifest_path, hashlib.sha256(raw).hexdigest(), manifest


def _rewrite_manifest(path: Path, manifest: dict[str, object]) -> str:
    raw = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _manifest_cli_args(path: Path, sha256: str) -> list[str]:
    return [
        "--market-input-manifest", str(path),
        "--expected-market-input-sha256", sha256,
    ]


def _run_bound_cli(
    tmp_path: Path,
    root: Path,
    manifest_path: Path,
    manifest_sha: str,
) -> dict[str, object]:
    source = tmp_path / "bound-lifecycles.json"
    source.write_text(json.dumps([_lifecycle()]), encoding="utf-8")
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        rc = main([
            "--lifecycles", str(source),
            "--market-root", str(root),
            *_manifest_cli_args(manifest_path, manifest_sha),
            "--market-start", "2026-01-01T10:01:00Z",
            "--bar-end", "2026-01-01T10:03:00Z",
            "--fee-bps", "0",
            "--slippage-bps", "0",
        ])
    assert rc == 0
    return json.loads(stdout.getvalue())


def _bound_market(
    tmp_path: Path,
) -> tuple[Path, Path, Path, str, dict[str, object]]:
    root = tmp_path / "market"
    partition = _write_bar_partition(
        root,
        "BTCUSDT",
        "2026-01-01",
        [_bar(0), _bar(1), _bar(2, high=111)],
    )
    manifest_path, manifest_sha, manifest = _market_manifest(
        tmp_path, root, [partition]
    )
    return root, partition, manifest_path, manifest_sha, manifest


def test_parquet_reader_only_returns_fully_closed_bars(tmp_path: Path) -> None:
    root = tmp_path / "market"
    _write_bar_partition(root, "BTCUSDT", "2026-01-01", [_bar(0), _bar(1), _bar(2)])

    rows = read_closed_1m_bars(
        market_root=root,
        symbols=["BTCUSDT"],
        start_us=int(_bar(0)["bucket_ts"]),
        bar_end_us=int(_bar(1)["bucket_ts"]),
    )

    assert [row["bucket_ts"] for row in rows] == [_bar(0)["bucket_ts"]]
    assert rows[0]["symbol"] == "BTCUSDT"


@pytest.mark.parametrize(
    ("rows", "start_minute", "end_minute"),
    [
        ([_bar(1), _bar(2)], 0, 3),
        ([_bar(0), _bar(1)], 0, 3),
    ],
)
def test_parquet_reader_requires_exact_first_and_last_window_boundaries(
    tmp_path: Path,
    rows: list[dict[str, object]],
    start_minute: int,
    end_minute: int,
) -> None:
    root = tmp_path / "market"
    _write_bar_partition(root, "BTCUSDT", "2026-01-01", rows)
    with pytest.raises(ValueError, match="boundary"):
        read_closed_1m_bars(
            market_root=root,
            symbols=["BTCUSDT"],
            start_us=int(_bar(start_minute)["bucket_ts"]),
            bar_end_us=int(_bar(end_minute)["bucket_ts"]),
        )


def test_parquet_reader_rejects_incomplete_schema(tmp_path: Path) -> None:
    root = tmp_path / "market"
    target = root / "bars_1m" / "symbol=BTCUSDT" / "date=2026-01-01" / "part.parquet"
    target.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([{"bucket_ts": _bar(0)["bucket_ts"], "open": 100.0}]), target)
    with pytest.raises(ValueError, match="schema"):
        read_closed_1m_bars(
            market_root=root,
            symbols=["BTCUSDT"],
            start_us=int(_bar(0)["bucket_ts"]),
            bar_end_us=int(_bar(1)["bucket_ts"]),
        )


def test_parquet_reader_rejects_symlinked_partition_file(tmp_path: Path) -> None:
    root = tmp_path / "market"
    outside = tmp_path / "outside.parquet"
    pq.write_table(pa.Table.from_pylist([{key: _bar(0)[key] for key in ("bucket_ts", "open", "high", "low", "close", "volume", "vwap", "trades")}]), outside)
    target = root / "bars_1m" / "symbol=BTCUSDT" / "date=2026-01-01" / "part.parquet"
    target.parent.mkdir(parents=True)
    target.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        read_closed_1m_bars(
            market_root=root,
            symbols=["BTCUSDT"],
            start_us=int(_bar(0)["bucket_ts"]),
            bar_end_us=int(_bar(2)["bucket_ts"]),
        )


def test_json_cli_accepts_curated_jsonl_shape_and_emits_only_json(tmp_path: Path) -> None:
    root = tmp_path / "market"
    partition = _write_bar_partition(
        root, "BTCUSDT", "2026-01-01", [_bar(0), _bar(1), _bar(2, high=111)]
    )
    manifest_path, manifest_sha, manifest = _market_manifest(tmp_path, root, [partition])
    lifecycle = _lifecycle(
        entry=100.0,
        entry_low=None,
        entry_high=None,
        terminal_status="open_or_unreported",
        open_message_id="999",
        parameter_fingerprint="b" * 64,
        confidence="high",
        explicit_reference_ids=["998"],
        link_basis="verified_body_message_reference",
    )
    lifecycle.pop("lifecycle_id")
    source = tmp_path / "lifecycles.jsonl"
    source.write_text(json.dumps(lifecycle) + "\n", encoding="utf-8")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    stdout = io.StringIO()

    with contextlib.redirect_stdout(stdout):
        rc = main(
            [
                "--lifecycles", str(source),
                "--market-root", str(root),
                    *_manifest_cli_args(manifest_path, manifest_sha),
                    "--bar-end", "2026-01-01T10:03:00Z",
                    "--market-start", "2026-01-01T10:01:00Z",
                    "--expected-input-sha256", source_sha,
                    "--expected-input-count", "1",
                "--fee-bps", "0",
                "--slippage-bps", "0",
            ]
        )

    assert rc == 0
    report = json.loads(stdout.getvalue())
    assert report["trades"][0]["lifecycle_id"] == "999"
    assert report["trades"][0]["status"] == "closed"
    assert math.isfinite(report["trades"][0]["net_return_pct"])
    assert report["input_binding"] == {"count": 1, "sha256": source_sha}
    assert report["market_input_binding"] == {
        "manifest_sha256": manifest_sha,
        "file_count": 1,
        "files_aggregate_sha256": manifest["files_aggregate_sha256"],
    }
    assert report["runtime_binding"] == {
        "python_version": discord_backtest.platform.python_version(),
        "pyarrow_version": pa.__version__,
        "implementation_sha256": hashlib.sha256(
            Path(discord_backtest.__file__).read_bytes()
        ).hexdigest(),
    }
    assert report["trades"][0]["slippage_pct"] == 0.0
    assert report["market_window"] == {
        "requested_start_us": _us("2026-01-01T10:01:00Z"),
        "requested_end_us": _us("2026-01-01T10:03:00Z"),
        "symbols": {
            "BTCUSDT": {
                "bar_count": 2,
                "first_bucket_ts": _us("2026-01-01T10:01:00Z"),
                "last_bucket_ts": _us("2026-01-01T10:02:00Z"),
            }
        },
    }
    assert report["parameters"] == {
        "entry_bar_take_profit": "ignored",
        "fee_model": "per_side_notional_double_sided",
        "fee_bps": 0.0,
        "intrabar_conflict": "stop_first",
        "max_entry_wait_minutes": 1440,
        "slippage_model": "adverse_per_side",
        "slippage_bps": 0.0,
        "tp_allocation": "equal_when_explicit_targets_without_weights",
    }


def test_runtime_binding_is_frozen_when_the_module_is_loaded(tmp_path: Path) -> None:
    original = Path(discord_backtest.__file__).read_bytes()
    copied_module = tmp_path / "copied_discord_backtest.py"
    copied_module.write_bytes(original)
    spec = importlib.util.spec_from_file_location(
        "copied_discord_backtest_for_binding_test", copied_module
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    copied_module.write_bytes(b"# replaced after module load\n")

    assert module._runtime_binding()["implementation_sha256"] == hashlib.sha256(
        original
    ).hexdigest()


def test_json_cli_excludes_unsupported_symbol_without_market_read(tmp_path: Path) -> None:
    root = tmp_path / "market"
    root.mkdir()
    lifecycle = _lifecycle(symbol="SOLUSDT")
    source = tmp_path / "lifecycles.json"
    source.write_text(json.dumps([lifecycle]), encoding="utf-8")
    manifest_path, manifest_sha, _ = _market_manifest(tmp_path, root, [])
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        rc = main([
            "--lifecycles", str(source), "--market-root", str(root),
            *_manifest_cli_args(manifest_path, manifest_sha),
            "--bar-end", "2026-01-01T10:03:00Z", "--fee-bps", "0", "--slippage-bps", "0",
        ])
    assert rc == 0
    trade = json.loads(stdout.getvalue())["trades"][0]
    assert trade["exclusion_reason"] == "unsupported_symbol"
    assert trade["symbol"] is None


def test_market_manifest_expected_hash_is_a_hard_gate(tmp_path: Path) -> None:
    root, _, manifest_path, _, _ = _bound_market(tmp_path)
    with pytest.raises(ValueError, match="manifest SHA-256 mismatch"):
        _run_bound_cli(tmp_path, root, manifest_path, "0" * 64)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"schema_version": "unknown"}, "schema_version"),
        ({"unexpected": "field"}, "strict schema"),
        ({"files_aggregate_sha256": "0" * 64}, "aggregate"),
        ({"requested_end_us": int(_bar(4)["bucket_ts"])}, "requested market window"),
    ],
)
def test_market_manifest_strict_schema_fails_closed(
    tmp_path: Path,
    mutation: dict[str, object],
    match: str,
) -> None:
    root, _, manifest_path, _, manifest = _bound_market(tmp_path)
    manifest.update(mutation)
    manifest_sha = _rewrite_manifest(manifest_path, manifest)
    with pytest.raises(ValueError, match=match):
        _run_bound_cli(tmp_path, root, manifest_path, manifest_sha)


def test_market_manifest_files_must_be_sorted_and_unique(tmp_path: Path) -> None:
    root = tmp_path / "market"
    first = _write_bar_partition(
        root, "BTCUSDT", "2026-01-01", [_bar(0), _bar(1)], name="a.parquet"
    )
    second = _write_bar_partition(
        root, "BTCUSDT", "2026-01-01", [_bar(2, high=111)], name="b.parquet"
    )
    manifest_path, _, manifest = _market_manifest(tmp_path, root, [first, second])
    files = list(reversed(manifest["files"]))
    manifest["files"] = files
    manifest["files_aggregate_sha256"] = _files_aggregate(files)
    manifest_sha = _rewrite_manifest(manifest_path, manifest)

    with pytest.raises(ValueError, match="sorted"):
        _run_bound_cli(tmp_path, root, manifest_path, manifest_sha)


def test_market_manifest_missing_explicit_file_fails(tmp_path: Path) -> None:
    root, partition, manifest_path, manifest_sha, _ = _bound_market(tmp_path)
    partition.unlink()
    with pytest.raises(ValueError, match="could not be opened safely"):
        _run_bound_cli(tmp_path, root, manifest_path, manifest_sha)


def test_market_manifest_rejects_symlinked_explicit_file(tmp_path: Path) -> None:
    root, partition, manifest_path, manifest_sha, _ = _bound_market(tmp_path)
    outside = tmp_path / "outside.parquet"
    partition.rename(outside)
    partition.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink|safely"):
        _run_bound_cli(tmp_path, root, manifest_path, manifest_sha)


def test_market_manifest_hashes_before_parsing_and_rejects_changed_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, partition, manifest_path, manifest_sha, _ = _bound_market(tmp_path)
    data = partition.read_bytes()
    partition.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    called = False

    def forbidden_parse(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("hash mismatch must fail before Arrow parsing")

    monkeypatch.setattr(pq, "ParquetFile", forbidden_parse)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _run_bound_cli(tmp_path, root, manifest_path, manifest_sha)
    assert called is False


def test_market_manifest_parses_the_verified_bytes_not_a_reopened_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, partition, manifest_path, manifest_sha, _ = _bound_market(tmp_path)
    original = pq.ParquetFile
    saw_buffer = False

    def replace_after_verification(source, *args, **kwargs):
        nonlocal saw_buffer
        saw_buffer = isinstance(source, pa.BufferReader)
        partition.write_bytes(b"replacement that is not parquet")
        return original(source, *args, **kwargs)

    monkeypatch.setattr(pq, "ParquetFile", replace_after_verification)
    report = _run_bound_cli(tmp_path, root, manifest_path, manifest_sha)
    assert saw_buffer is True
    assert report["summary"]["closed"] == 1


def test_market_manifest_ignores_unlisted_extra_disk_file(tmp_path: Path) -> None:
    root, partition, manifest_path, manifest_sha, _ = _bound_market(tmp_path)
    partition.with_name("unlisted.parquet").write_bytes(b"not a parquet file")
    report = _run_bound_cli(tmp_path, root, manifest_path, manifest_sha)
    assert report["market_input_binding"]["file_count"] == 1
    assert report["summary"]["closed"] == 1


def test_market_manifest_rejects_extra_symbol_file(tmp_path: Path) -> None:
    root, partition, _, _, _ = _bound_market(tmp_path)
    extra = _write_bar_partition(
        root,
        "ETHUSDT",
        "2026-01-01",
        [_bar(0, symbol="ETHUSDT"), _bar(1, symbol="ETHUSDT")],
    )
    manifest_path, manifest_sha, _ = _market_manifest(
        tmp_path, root, [partition, extra]
    )
    with pytest.raises(ValueError, match="unexpected symbol"):
        _run_bound_cli(tmp_path, root, manifest_path, manifest_sha)


def test_market_manifest_rejects_path_traversal(tmp_path: Path) -> None:
    root, _, manifest_path, _, manifest = _bound_market(tmp_path)
    files = manifest["files"]
    files[0]["path"] = "../outside.parquet"
    manifest["files_aggregate_sha256"] = _files_aggregate(files)
    manifest_sha = _rewrite_manifest(manifest_path, manifest)
    with pytest.raises(ValueError, match="relative path"):
        _run_bound_cli(tmp_path, root, manifest_path, manifest_sha)
