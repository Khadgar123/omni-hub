"""Deterministic, conservative 1-minute simulation for Discord trade calls.

This module is the dependency-owning side of the main repository's subprocess
seam.  It deliberately models only what a 1-minute OHLC bar can prove.  In
particular, every signal starts on the following minute, limit orders never
fall back to market, and an ambiguous stop/target bar is resolved stop-first.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import sys
from typing import Any


def _freeze_implementation_source() -> tuple[bytes, str]:
    """Pin the implementation file before any runtime work can replace it."""

    path = Path(__file__)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeError("backtest implementation could not be opened safely") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("backtest implementation must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("backtest implementation could not be opened safely") from exc
    with os.fdopen(fd, "rb", closefd=True) as handle:
        before = os.fstat(handle.fileno())
        source = handle.read()
        after = os.fstat(handle.fileno())
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    if identity(after) != identity(before):
        raise RuntimeError("backtest implementation changed while being loaded")
    return source, hashlib.sha256(source).hexdigest()


_FROZEN_IMPLEMENTATION_BYTES, _FROZEN_IMPLEMENTATION_SHA256 = (
    _freeze_implementation_source()
)


MINUTE_US = 60_000_000
SUPPORTED_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT"})
SCHEMA_VERSION = "discord-backtest-v1"
MARKET_INPUT_SCHEMA_VERSION = "market-input-request-v1"
_BAR_COLUMNS = ("bucket_ts", "open", "high", "low", "close", "volume", "vwap", "trades")
_MARKET_MANIFEST_FIELDS = frozenset({
    "schema_version",
    "market_root",
    "requested_start_us",
    "requested_end_us",
    "files",
    "files_aggregate_sha256",
})
_MARKET_FILE_FIELDS = frozenset({"path", "size_bytes", "sha256"})
_MARKET_RELATIVE_PATH = re.compile(
    r"bars_1m/symbol=(BTCUSDT|ETHUSDT)/date=(\d{4}-\d{2}-\d{2})/"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\.parquet\Z"
)
_ALLOWED_PROFILES = frozenset({
    "coin-chief-v1",
    "shuqin-v1",
    "always-win-trader",
    "always-win-trader-v1",
    "analyst-nick",
    "analyst-nick-v1",
})
_ALLOWED_CONFIDENCE = frozenset({"high", "medium", "low"})
_ALLOWED_EXCLUSION_REASONS = frozenset({
    "cancel_after_fill_exit_semantics_ambiguous",
    "duplicate_parameter_fingerprint",
    "entry_and_sl_changed_by_multiple_explicit_edits",
    "exact_duplicate_open",
    "insufficient_post_signal_kline_horizon",
    "multiple_or_ambiguous_parameter_blocks",
    "outside_local_common_kline_coverage",
    "overlapping_unlinked_reissue",
    "source_snapshot_contains_open_and_manual_cancel_conflict",
    "terminal_event_conflict",
    "unresolved_duplicate_open",
})
_ALLOWED_LINK_BASIS = frozenset({
    "body_explicit_message_reference",
    "exact_parameter_near_duplicate_only",
    "exact_parameter_time_duplicate",
    "self_contained_parameters",
    "standalone_open",
    "standalone_overlapping_reissue",
    "standalone_reissue_after_explicit_cancel",
    "verified_body_message_reference",
    "verified_body_message_reference_after_declared_validity",
    "verified_body_message_reference_chain",
    "verified_continuation_reference_then_verified_close_reference",
    "verified_reissue_after_parent_validity_end",
})
_ALLOWED_TERMINAL_STATUS = frozenset({
    "cancel_after_fill_ambiguous",
    "cancelled_unfilled",
    "cancelled_unfilled_or_unconfirmed",
    "closed_manual_small_profit",
    "closed_sl",
    "closed_tp_all_targets",
    "conflicting_edit_chain",
    "conflicting_manual_close_and_cancel",
    "current_open_unconfirmed",
    "duplicate_unresolved",
    "exact_duplicate",
    "exact_duplicate_of_conflicted_open",
    "exact_duplicate_of_unresolved_open",
    "expired_before_referenced_reissue",
    "expired_unreported",
    "open_or_unreported",
    "open_or_unreported_after_entry_amend",
    "open_or_unreported_after_fill",
    "open_or_unreported_after_fill_and_sl_amend",
    "partial_tp1_then_expired_before_reissue",
    "partial_tp2_open_remainder",
    "partial_tp3_open_remainder",
    "right_censored_at_common_kline_end",
    "source_self_cancel_conflicts_with_later_fill_tp_and_all_targets",
    "unresolved_overlapping_open",
})
_LIFECYCLE_FIELDS = frozenset({
    "lifecycle_id",
    "open_message_id",
    "parameter_fingerprint",
    "profile",
    "symbol",
    "direction",
    "effective_at",
    "entry",
    "entry_low",
    "entry_high",
    "sl",
    "tps",
    "evaluable",
    "confidence",
    "duplicate_of",
    "exclusion_reason",
    "terminal_status",
    "explicit_reference_ids",
    "link_basis",
    "cancel_effective_at",
})
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SAFE_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9-]{1,19}\Z")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_FINGERPRINT = re.compile(r"[A-Za-z0-9,|-]{1,128}\Z")
_SENSITIVE_TEXT = re.compile(
    r"(?:https?|ftp)://|(?:bearer|bot)\s+[A-Za-z0-9._~-]+|"
    r"(?:access[_-]?token|authorization|x-amz-signature|signature)\s*[=:]|"
    r"(?:mfa\.)?[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}|"
    r"(?:^|[?&])(?:ex|is|hm)=[0-9a-f]{6,}",
    re.IGNORECASE,
)
_SENSITIVE_KEYS = frozenset({
    "authorization",
    "bot_token",
    "content",
    "cookie",
    "logical_key",
    "message_body",
    "proxy_url",
    "raw_url",
    "signed_url",
    "token",
})
def _finite_number(value: object, *, field: str, positive: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{field} must be finite and positive")
    return number


def _optional_number(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, field=field)


def _parse_instant_us(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a UTC instant")
    if isinstance(value, int):
        # Store and simulator timestamps are epoch microseconds.
        if value <= 0:
            raise ValueError(f"{field} must be a positive epoch-microsecond instant")
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or value <= 0 or not value.is_integer():
            raise ValueError(f"{field} must be an exact epoch-microsecond instant")
        return int(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a timezone-aware ISO instant")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be a timezone-aware ISO instant") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return int(parsed.astimezone(UTC).timestamp() * 1_000_000)


def _next_minute_us(signal_us: int) -> int:
    return signal_us - signal_us % MINUTE_US + MINUTE_US


def _scan_sensitive(value: object, *, output: bool = False, _depth: int = 0) -> None:
    if _depth > 20:
        raise ValueError("lifecycle structure exceeds the safe depth")
    if isinstance(value, str):
        if _SENSITIVE_TEXT.search(value):
            label = "output" if output else "lifecycle"
            raise ValueError(f"{label} contains a prohibited sensitive string")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                label = "output" if output else "lifecycle"
                raise ValueError(f"{label} mapping keys must be strings")
            if key.casefold() in _SENSITIVE_KEYS:
                label = "output" if output else "lifecycle"
                raise ValueError(f"{label} contains a prohibited sensitive field")
            _scan_sensitive(child, output=output, _depth=_depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _scan_sensitive(child, output=output, _depth=_depth + 1)


def _require_safe_id(value: object, *, field: str, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"lifecycle {field} has an invalid safe identifier")


def _validate_lifecycle_schema(source: Mapping[str, object], *, index: int) -> None:
    _scan_sensitive(source)
    if set(source) - _LIFECYCLE_FIELDS:
        raise ValueError("lifecycle contains fields outside the strict schema")
    if source.get("profile") not in _ALLOWED_PROFILES:
        raise ValueError("lifecycle profile is outside the allowed enum")
    if not isinstance(source.get("evaluable"), bool):
        raise ValueError("lifecycle evaluable must be boolean")
    if source.get("confidence") not in _ALLOWED_CONFIDENCE:
        raise ValueError("lifecycle confidence is outside the allowed enum")
    _require_safe_id(source.get("lifecycle_id"), field="lifecycle_id", optional=True)
    _require_safe_id(source.get("open_message_id"), field="open_message_id", optional=True)
    _require_safe_id(source.get("duplicate_of"), field="duplicate_of", optional=True)
    fingerprint = source.get("parameter_fingerprint")
    if fingerprint is not None and (
        not isinstance(fingerprint, str) or _SAFE_FINGERPRINT.fullmatch(fingerprint) is None
    ):
        raise ValueError("lifecycle parameter_fingerprint has invalid format")
    references = source.get("explicit_reference_ids")
    if references is not None:
        if not isinstance(references, list):
            raise ValueError("lifecycle explicit_reference_ids must be a list")
        for reference in references:
            _require_safe_id(reference, field="explicit_reference_ids")
    reason = source.get("exclusion_reason")
    if reason is not None and reason not in _ALLOWED_EXCLUSION_REASONS:
        raise ValueError("lifecycle exclusion_reason is outside the allowed enum")
    terminal = source.get("terminal_status")
    if terminal is not None and terminal not in _ALLOWED_TERMINAL_STATUS:
        raise ValueError("lifecycle terminal_status is outside the allowed enum")
    link_basis = source.get("link_basis")
    if link_basis is not None and link_basis not in _ALLOWED_LINK_BASIS:
        raise ValueError("lifecycle link_basis is outside the allowed enum")
    symbol = source.get("symbol")
    if not isinstance(symbol, str) or _SAFE_SYMBOL.fullmatch(symbol) is None:
        raise ValueError("lifecycle symbol has invalid format")
    if source.get("direction") not in ("long", "short"):
        raise ValueError("lifecycle direction is outside the allowed enum")
    _parse_instant_us(source.get("effective_at"), field=f"lifecycle {index} effective_at")
    if source.get("cancel_effective_at") is not None:
        _parse_instant_us(source.get("cancel_effective_at"), field=f"lifecycle {index} cancel_effective_at")


def _validate_cost_bps(value: float, *, field: str) -> float:
    number = _finite_number(value, field=field, positive=False)
    if number < 0 or number >= 10_000:
        raise ValueError(f"{field} must be in [0, 10000)")
    return number


def _validate_max_entry_wait(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("max_entry_wait_minutes must be a positive integer")
    if value > 1_000_000:
        raise ValueError("max_entry_wait_minutes is unreasonably large")
    return value


def _validate_bars(bars: Sequence[Mapping[str, object]]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for index, source in enumerate(bars):
        if not isinstance(source, Mapping):
            raise ValueError(f"bar {index} must be a mapping")
        symbol = source.get("symbol")
        if not isinstance(symbol, str) or symbol not in SUPPORTED_SYMBOLS:
            raise ValueError(f"bar {index} has unsupported symbol")
        timestamp = source.get("bucket_ts")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise ValueError(f"bar {index} bucket_ts must be integer epoch microseconds")
        if timestamp % MINUTE_US:
            raise ValueError(f"bar {index} bucket_ts is not 1m aligned")
        open_ = _finite_number(source.get("open"), field=f"bar {index} open")
        high = _finite_number(source.get("high"), field=f"bar {index} high")
        low = _finite_number(source.get("low"), field=f"bar {index} low")
        close = _finite_number(source.get("close"), field=f"bar {index} close")
        if low > min(open_, close) or high < max(open_, close) or low > high:
            raise ValueError(f"bar {index} has invalid OHLC geometry")
        row = dict(source)
        row.update({
            "symbol": symbol,
            "bucket_ts": timestamp,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        })
        grouped.setdefault(symbol, []).append(row)

    for symbol, rows in grouped.items():
        previous: int | None = None
        for row in rows:
            timestamp = int(row["bucket_ts"])
            if previous is not None:
                if timestamp == previous:
                    raise ValueError(f"duplicate 1m bar for {symbol}")
                if timestamp < previous:
                    raise ValueError(f"1m bars for {symbol} are not sorted")
                if timestamp - previous != MINUTE_US:
                    raise ValueError(f"1m bar gap for {symbol}")
            previous = timestamp
    return grouped


def _lifecycle_id(source: Mapping[str, object], index: int) -> str:
    for key in ("lifecycle_id", "open_message_id"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    fingerprint = source.get("parameter_fingerprint")
    if isinstance(fingerprint, str) and fingerprint.strip():
        return hashlib.sha256(
            f"discord-lifecycle-fingerprint-v1:{fingerprint.strip()}".encode("utf-8")
        ).hexdigest()
    raise ValueError(f"lifecycle {index} is missing a stable identifier")


def _base_trade(
    *, source: Mapping[str, object], lifecycle_id: str, signal_us: int | None,
) -> dict[str, object]:
    symbol = source.get("symbol")
    direction = source.get("direction")
    return {
        "lifecycle_id": lifecycle_id,
        "profile": source["profile"],
        "symbol": symbol if symbol in SUPPORTED_SYMBOLS else None,
        "direction": direction if direction in ("long", "short") else None,
        "signal_at_us": signal_us,
        "status": "excluded",
        "outcome": None,
        "exclusion_reason": None,
        "entry_bucket_ts": None,
        "entry_price": None,
        "exit_bucket_ts": None,
        "exit_reason": None,
        "tp_fills": [],
        "remaining_fraction": 1.0,
        "gross_return_pct": None,
        "fees_pct": None,
        "slippage_pct": None,
        "net_return_pct": None,
    }


def _excluded(base: dict[str, object], reason: str) -> dict[str, object]:
    base["status"] = "excluded"
    base["exclusion_reason"] = reason
    return base


def _unfilled(base: dict[str, object], reason: str) -> dict[str, object]:
    base["status"] = "unfilled"
    base["exclusion_reason"] = reason
    return base


def _adverse_entry(price: float, direction: str, slippage_rate: float) -> float:
    return price * (1 + slippage_rate if direction == "long" else 1 - slippage_rate)


def _adverse_exit(price: float, direction: str, slippage_rate: float) -> float:
    return price * (1 - slippage_rate if direction == "long" else 1 + slippage_rate)


def _stop_touched(row: Mapping[str, object], *, direction: str, stop: float) -> bool:
    return float(row["low"]) <= stop if direction == "long" else float(row["high"]) >= stop


def _target_touched(row: Mapping[str, object], *, direction: str, target: float) -> bool:
    return float(row["high"]) >= target if direction == "long" else float(row["low"]) <= target


def _stop_reference_price(row: Mapping[str, object], *, direction: str, stop: float) -> float:
    open_ = float(row["open"])
    if direction == "long" and open_ < stop:
        return open_
    if direction == "short" and open_ > stop:
        return open_
    return stop


def _zone_raw_fill(
    row: Mapping[str, object], *, direction: str, low: float, high: float,
) -> float | None:
    bar_low = float(row["low"])
    bar_high = float(row["high"])
    intersection_low = max(bar_low, low)
    intersection_high = min(bar_high, high)
    if intersection_low > intersection_high:
        return None
    # A long pays the highest provable intersecting price; a short receives the
    # lowest.  This is deliberately adverse and makes no intrabar path claim.
    return intersection_high if direction == "long" else intersection_low


def _price_geometry_valid(
    *, direction: str, stop: float, targets: Sequence[float], zone: tuple[float, float] | None,
    market_entry: float | None = None,
) -> bool:
    if zone is not None:
        low, high = zone
    elif market_entry is not None:
        low = high = market_entry
    else:
        return True
    if direction == "long":
        return stop < low and all(target > high for target in targets)
    return stop > high and all(target < low for target in targets)


def _simulate_one(
    source: Mapping[str, object], *, lifecycle_id: str,
    bars_by_symbol: Mapping[str, list[dict[str, object]]],
    fee_rate: float, slippage_rate: float, max_entry_wait_minutes: int,
) -> dict[str, object]:
    signal_us: int | None = None
    try:
        signal_us = _parse_instant_us(source.get("effective_at"), field="effective_at")
    except ValueError:
        # An invalid evidence timestamp is corruption, not a modelling
        # exclusion that callers may safely aggregate.
        raise
    base = _base_trade(source=source, lifecycle_id=lifecycle_id, signal_us=signal_us)

    if source.get("duplicate_of") is not None:
        return _excluded(base, "excluded_duplicate")
    reason = source.get("exclusion_reason")
    if reason is not None:
        return _excluded(base, reason if isinstance(reason, str) and reason else "source_exclusion")
    if source.get("evaluable") is not True:
        return _excluded(base, "source_not_evaluable")
    confidence = source.get("confidence")
    if not isinstance(confidence, str) or confidence.strip().casefold() != "high":
        return _excluded(base, "excluded_low_confidence")

    symbol = source.get("symbol")
    if symbol not in SUPPORTED_SYMBOLS:
        return _excluded(base, "unsupported_symbol")
    direction = source.get("direction")
    if direction not in ("long", "short"):
        return _excluded(base, "unsupported_direction")

    terminal_status = source.get("terminal_status")
    if isinstance(terminal_status, str) and terminal_status.startswith(("cancelled_", "expired_")):
        return _excluded(base, "excluded_known_cancel_or_expiry")
    if isinstance(terminal_status, str) and terminal_status.startswith("closed_manual"):
        return _excluded(base, "manual_close_unmodeled")

    stop = _optional_number(source.get("sl"), field="sl")
    raw_targets = source.get("tps")
    if stop is None:
        return _excluded(base, "missing_stop_loss")
    if not isinstance(raw_targets, (list, tuple)) or not raw_targets:
        return _excluded(base, "missing_explicit_take_profits")
    try:
        targets = [_finite_number(value, field="take profit") for value in raw_targets]
    except ValueError:
        return _excluded(base, "invalid_price_geometry")
    if len(set(targets)) != len(targets):
        return _excluded(base, "invalid_price_geometry")
    targets.sort(reverse=direction == "short")

    entry = _optional_number(source.get("entry"), field="entry")
    entry_low = _optional_number(source.get("entry_low"), field="entry_low")
    entry_high = _optional_number(source.get("entry_high"), field="entry_high")
    if (entry_low is None) != (entry_high is None):
        return _excluded(base, "invalid_price_geometry")
    if entry_low is not None and entry_high is not None:
        if entry_low > entry_high or (entry is not None and not entry_low <= entry <= entry_high):
            return _excluded(base, "invalid_price_geometry")
        zone: tuple[float, float] | None = (entry_low, entry_high)
    elif entry is not None:
        zone = (entry, entry)
    else:
        zone = None
    if zone is not None and not _price_geometry_valid(
        direction=direction, stop=stop, targets=targets, zone=zone
    ):
        return _excluded(base, "invalid_price_geometry")

    cancel_us = None
    if source.get("cancel_effective_at") is not None:
        cancel_us = _parse_instant_us(source.get("cancel_effective_at"), field="cancel_effective_at")
        if cancel_us < signal_us:
            return _excluded(base, "cancel_precedes_open")

    symbol_bars = bars_by_symbol.get(str(symbol))
    if symbol_bars is None:
        raise ValueError(f"no 1m bars for {symbol}")
    eligible_at = _next_minute_us(signal_us)
    first_bar_us = int(symbol_bars[0]["bucket_ts"])
    last_bar_us = int(symbol_bars[-1]["bucket_ts"])
    observed_until = last_bar_us + MINUTE_US
    if eligible_at < first_bar_us:
        return _excluded(base, "market_data_gap_at_entry_start")
    if eligible_at > last_bar_us:
        base["status"] = "right_censored"
        base["exclusion_reason"] = "no_post_signal_full_bar"
        return base
    eligible_index = (eligible_at - first_bar_us) // MINUTE_US
    if (
        eligible_index < 0
        or eligible_index >= len(symbol_bars)
        or int(symbol_bars[eligible_index]["bucket_ts"]) != eligible_at
    ):
        return _excluded(base, "market_data_gap_at_entry_start")
    eligible = symbol_bars[eligible_index:]

    entry_row_index: int | None = None
    raw_fill: float | None = None
    entry_deadline = signal_us + max_entry_wait_minutes * MINUTE_US
    cancelled_before_fill = False
    for index, row in enumerate(eligible):
        row_start = int(row["bucket_ts"])
        row_end = row_start + MINUTE_US
        if row_end > entry_deadline:
            break
        if zone is None:
            # A market order executes at the exact next bar open.  A cancel
            # observed later in that bar cannot travel backwards in time.
            if cancel_us is not None and cancel_us <= row_start:
                cancelled_before_fill = True
                break
            raw_fill = float(row["open"])
        else:
            if cancel_us is not None and cancel_us <= row_start:
                cancelled_before_fill = True
                break
            raw_fill = _zone_raw_fill(
                row, direction=str(direction), low=zone[0], high=zone[1]
            )
            if cancel_us is not None and row_start < cancel_us < row_end:
                if raw_fill is not None:
                    return _excluded(base, "limit_touch_cancel_intrabar_ambiguous")
                cancelled_before_fill = True
                break
        if raw_fill is not None:
            entry_row_index = index
            break

    if entry_row_index is None or raw_fill is None:
        if observed_until >= entry_deadline and (
            cancel_us is None or cancel_us > entry_deadline
        ):
            return _unfilled(base, "unfilled_expired_assumption")
        if cancelled_before_fill or (
            cancel_us is not None and cancel_us <= min(observed_until, entry_deadline)
        ):
            return _unfilled(base, "cancelled_before_fill")
        base["status"] = "right_censored"
        base["exclusion_reason"] = "entry_window_right_censored"
        return base

    if zone is None and not _price_geometry_valid(
        direction=str(direction), stop=stop, targets=targets, zone=None, market_entry=raw_fill
    ):
        return _excluded(base, "invalid_price_geometry")

    entry_price = _adverse_entry(raw_fill, str(direction), slippage_rate)
    if entry_price <= 0:
        return _excluded(base, "invalid_price_geometry")
    entry_row = eligible[entry_row_index]
    base["entry_bucket_ts"] = int(entry_row["bucket_ts"])
    base["entry_price"] = entry_price

    realized_pnl = 0.0
    fees = entry_price * fee_rate
    slippage_cost = abs(entry_price - raw_fill)
    remaining = 1.0
    tp_fills: list[dict[str, object]] = []
    target_fraction = 1.0 / len(targets)
    next_target = 0
    exit_bucket: int | None = None
    exit_reason: str | None = None

    def close_stop(row: Mapping[str, object], *, use_gap_open: bool = True) -> None:
        nonlocal realized_pnl, fees, slippage_cost, remaining, exit_bucket, exit_reason
        reference = _stop_reference_price(row, direction=str(direction), stop=stop) if use_gap_open else stop
        exit_price = _adverse_exit(reference, str(direction), slippage_rate)
        pnl = (exit_price - entry_price) if direction == "long" else (entry_price - exit_price)
        realized_pnl += pnl * remaining
        fees += exit_price * remaining * fee_rate
        slippage_cost += abs(reference - exit_price) * remaining
        remaining = 0.0
        exit_bucket = int(row["bucket_ts"])
        exit_reason = "stop_loss"

    # On the fill bar the intrabar path is unknowable: a touched stop is
    # honoured, while targets are deliberately ignored.
    if _stop_touched(entry_row, direction=str(direction), stop=stop):
        # A resting limit cannot fill until price reaches its zone, so a bar
        # opening beyond the stop does not prove an open-gap exit before that
        # later fill.  Market entries do execute at that adverse open.
        close_stop(entry_row, use_gap_open=zone is None)
    else:
        for row in eligible[entry_row_index + 1:]:
            if _stop_touched(row, direction=str(direction), stop=stop):
                close_stop(row)
                break
            while next_target < len(targets) and _target_touched(
                row, direction=str(direction), target=targets[next_target]
            ):
                target = targets[next_target]
                fraction = target_fraction if next_target < len(targets) - 1 else remaining
                exit_price = _adverse_exit(target, str(direction), slippage_rate)
                pnl = (exit_price - entry_price) if direction == "long" else (entry_price - exit_price)
                realized_pnl += pnl * fraction
                fees += exit_price * fraction * fee_rate
                slippage_cost += abs(target - exit_price) * fraction
                remaining = max(0.0, remaining - fraction)
                tp_fills.append({
                    "target": target,
                    "fraction": fraction,
                    "exit_price": exit_price,
                    "bucket_ts": int(row["bucket_ts"]),
                })
                next_target += 1
            if next_target == len(targets):
                remaining = 0.0
                exit_bucket = int(row["bucket_ts"])
                exit_reason = "take_profit"
                break

    gross_pct = realized_pnl / entry_price * 100
    fees_pct = fees / entry_price * 100
    slippage_pct = slippage_cost / entry_price * 100
    net_pct = gross_pct - fees_pct
    base.update({
        "exit_bucket_ts": exit_bucket,
        "exit_reason": exit_reason,
        "tp_fills": tp_fills,
        "remaining_fraction": remaining,
        "gross_return_pct": gross_pct,
        "fees_pct": fees_pct,
        "slippage_pct": slippage_pct,
        "net_return_pct": net_pct,
    })
    if remaining > 1e-12:
        base["status"] = "right_censored"
        base["exclusion_reason"] = "position_open_at_bar_end"
        return base

    base["status"] = "closed"
    if net_pct > 1e-12:
        base["outcome"] = "win"
    elif net_pct < -1e-12:
        base["outcome"] = "loss"
    else:
        base["outcome"] = "flat"
    return base


def simulate_lifecycles(
    *, lifecycles: Sequence[Mapping[str, object]], bars: Sequence[Mapping[str, object]],
    fee_bps: float, slippage_bps: float, max_entry_wait_minutes: int = 1440,
    expected_market_start_us: int | None = None,
    expected_market_end_us: int | None = None,
) -> dict[str, object]:
    """Simulate redacted lifecycle parameters against validated closed 1m bars.

    ``bars`` must be complete, contiguous and ordered independently for each
    symbol.  Corrupt market data raises ``ValueError`` rather than producing a
    partial statistic.
    """

    fee = _validate_cost_bps(fee_bps, field="fee_bps")
    slippage = _validate_cost_bps(slippage_bps, field="slippage_bps")
    max_wait = _validate_max_entry_wait(max_entry_wait_minutes)
    bars_by_symbol = _validate_bars(bars)
    if (expected_market_start_us is None) != (expected_market_end_us is None):
        raise ValueError("expected market start/end must be supplied together")
    if expected_market_start_us is not None and expected_market_end_us is not None:
        if (
            isinstance(expected_market_start_us, bool)
            or isinstance(expected_market_end_us, bool)
            or not isinstance(expected_market_start_us, int)
            or not isinstance(expected_market_end_us, int)
            or expected_market_start_us % MINUTE_US
            or expected_market_end_us % MINUTE_US
            or expected_market_end_us <= expected_market_start_us
        ):
            raise ValueError("expected market window must be an aligned non-empty interval")
        for symbol, symbol_bars in bars_by_symbol.items():
            if (
                int(symbol_bars[0]["bucket_ts"]) != expected_market_start_us
                or int(symbol_bars[-1]["bucket_ts"]) + MINUTE_US != expected_market_end_us
            ):
                raise ValueError(f"expected market window boundary is incomplete for {symbol}")

    prepared: list[tuple[int, str, Mapping[str, object]]] = []
    identifiers: set[str] = set()
    for index, source in enumerate(lifecycles):
        if not isinstance(source, Mapping):
            raise ValueError(f"lifecycle {index} must be a mapping")
        _validate_lifecycle_schema(source, index=index)
        identifier = _lifecycle_id(source, index)
        if identifier in identifiers:
            raise ValueError("duplicate lifecycle identifier")
        identifiers.add(identifier)
        signal_us = _parse_instant_us(source.get("effective_at"), field="effective_at")
        prepared.append((signal_us, identifier, source))
    prepared.sort(key=lambda item: (item[0], item[1]))

    trades = [
        _simulate_one(
            source,
            lifecycle_id=identifier,
            bars_by_symbol=bars_by_symbol,
            fee_rate=fee / 10_000,
            slippage_rate=slippage / 10_000,
            max_entry_wait_minutes=max_wait,
        )
        for _, identifier, source in prepared
    ]
    closed = [trade for trade in trades if trade["status"] == "closed"]
    wins = sum(trade["outcome"] == "win" for trade in closed)
    losses = sum(trade["outcome"] == "loss" for trade in closed)
    flat = sum(trade["outcome"] == "flat" for trade in closed)
    denominator = wins + losses
    summary = {
        "lifecycles": len(trades),
        "closed": len(closed),
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "unfilled": sum(trade["status"] == "unfilled" for trade in trades),
        "right_censored": sum(trade["status"] == "right_censored" for trade in trades),
        "excluded": sum(trade["status"] == "excluded" for trade in trades),
        "win_rate": wins / denominator if denominator else None,
    }
    market_symbols = {
        symbol: {
            "bar_count": len(symbol_bars),
            "first_bucket_ts": int(symbol_bars[0]["bucket_ts"]),
            "last_bucket_ts": int(symbol_bars[-1]["bucket_ts"]),
        }
        for symbol, symbol_bars in sorted(bars_by_symbol.items())
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "bar_interval": "1m",
        "funding": "unmodeled",
        "parameters": {
            "fee_bps": fee,
            "slippage_bps": slippage,
            "max_entry_wait_minutes": max_wait,
            "tp_allocation": "equal_when_explicit_targets_without_weights",
            "fee_model": "per_side_notional_double_sided",
            "slippage_model": "adverse_per_side",
            "entry_bar_take_profit": "ignored",
            "intrabar_conflict": "stop_first",
        },
        "market_window": {
            "requested_start_us": expected_market_start_us,
            "requested_end_us": expected_market_end_us,
            "symbols": market_symbols,
        },
        "summary": summary,
        "trades": trades,
    }
    _scan_sensitive(result, output=True)
    return result


def _assert_directory_no_symlink(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"{label} does not exist") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"{label} must not be a symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be a directory")


def _read_parquet_file_no_symlink(path: Path) -> list[dict[str, object]]:
    import pyarrow.parquet as pq

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if path.is_symlink():
            raise ValueError("bar parquet file must not be a symlink") from exc
        raise ValueError("bar parquet file could not be opened safely") from exc
    with os.fdopen(fd, "rb", closefd=True) as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("bar parquet input must be a regular file")
        parquet = pq.ParquetFile(handle)
        if not set(_BAR_COLUMNS).issubset(parquet.schema_arrow.names):
            raise ValueError("bar parquet schema is missing required columns")
        table = parquet.read(columns=list(_BAR_COLUMNS))
        after = os.fstat(handle.fileno())
        identity = lambda item: (
            item.st_dev, item.st_ino, item.st_mode, item.st_size,
            item.st_mtime_ns, item.st_ctime_ns,
        )
        if identity(after) != identity(metadata):
            raise ValueError("bar parquet file changed while being read")
    return table.to_pylist()


def read_closed_1m_bars(
    *, market_root: Path | str, symbols: Sequence[str], start_us: int, bar_end_us: int,
) -> list[dict[str, object]]:
    """Read only bars whose full ``[open, open+1m)`` interval has closed.

    Partition paths and Parquet files must be real directories/regular files;
    symlinks are rejected before any Arrow read.
    """

    if isinstance(start_us, bool) or not isinstance(start_us, int):
        raise ValueError("start_us must be integer epoch microseconds")
    if isinstance(bar_end_us, bool) or not isinstance(bar_end_us, int) or bar_end_us <= start_us:
        raise ValueError("bar_end_us must be after start_us")
    if start_us % MINUTE_US or bar_end_us % MINUTE_US:
        raise ValueError("requested market window boundaries must be 1m aligned")
    root = Path(market_root).expanduser().absolute()
    _assert_directory_no_symlink(root, label="market root")
    table_root = root / "bars_1m"
    _assert_directory_no_symlink(table_root, label="bars_1m root")
    start_date = datetime.fromtimestamp(start_us / 1_000_000, tz=UTC).date().isoformat()
    end_date = datetime.fromtimestamp((bar_end_us - 1) / 1_000_000, tz=UTC).date().isoformat()
    output: list[dict[str, object]] = []
    seen_symbols: set[str] = set()

    for symbol in sorted(set(symbols)):
        if symbol not in SUPPORTED_SYMBOLS:
            raise ValueError("unsupported symbol")
        symbol_root = table_root / f"symbol={symbol}"
        _assert_directory_no_symlink(symbol_root, label=f"bar symbol partition {symbol}")
        symbol_rows = 0
        for date_root in sorted(symbol_root.iterdir(), key=lambda item: item.name):
            if re.fullmatch(r"date=\d{4}-\d{2}-\d{2}", date_root.name) is None:
                raise ValueError("bar partition enumeration contains an unexpected entry")
            if date_root.is_symlink():
                raise ValueError("bar date partition must not be a symlink")
            day = date_root.name.removeprefix("date=")
            if day < start_date or day > end_date:
                continue
            _assert_directory_no_symlink(date_root, label="bar date partition")
            selected_files = 0
            for file_path in sorted(date_root.iterdir(), key=lambda item: item.name):
                if file_path.suffix != ".parquet":
                    raise ValueError("bar partition enumeration contains a non-Parquet entry")
                if file_path.is_symlink():
                    raise ValueError("bar parquet file must not be a symlink")
                selected_files += 1
                for row in _read_parquet_file_no_symlink(file_path):
                    timestamp = row.get("bucket_ts")
                    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
                        raise ValueError("bar parquet bucket_ts must be integer epoch microseconds")
                    if start_us <= timestamp and timestamp + MINUTE_US <= bar_end_us:
                        materialized = dict(row)
                        materialized["symbol"] = symbol
                        output.append(materialized)
                        symbol_rows += 1
            if selected_files == 0:
                raise ValueError("bar date partition contains no Parquet files")
        if symbol_rows:
            seen_symbols.add(symbol)
    missing = set(symbols) - seen_symbols
    if missing:
        raise ValueError(f"no fully closed 1m bars for {','.join(sorted(missing))}")
    output.sort(key=lambda row: (str(row["symbol"]), int(row["bucket_ts"])))
    # Reuse the exact validation used by the simulator before returning data.
    grouped = _validate_bars(output)
    for symbol in sorted(set(symbols)):
        rows = grouped[symbol]
        if (
            int(rows[0]["bucket_ts"]) != start_us
            or int(rows[-1]["bucket_ts"]) + MINUTE_US != bar_end_us
        ):
            raise ValueError(f"requested market window boundary is incomplete for {symbol}")
    return output


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_regular_bytes(path: Path, *, label: str = "lifecycle input") -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} could not be opened safely") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label} could not be opened safely") from exc
    with os.fdopen(fd, "rb", closefd=True) as handle:
        before = os.fstat(handle.fileno())
        data = handle.read()
        if _stat_identity(os.fstat(handle.fileno())) != _stat_identity(before):
            raise ValueError(f"{label} changed while being read")
    return data


def _canonical_market_root(value: Path | str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))


def _decode_strict_json_object(data: bytes, *, label: str) -> dict[str, object]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be UTF-8") from exc

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains a duplicate key")
            result[key] = value
        return result

    try:
        parsed = json.loads(text, object_pairs_hook=reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return parsed


def _market_files_aggregate(files: Sequence[Mapping[str, object]]) -> str:
    """Hash canonical ``[path,size_bytes,sha256]`` tuples in file order."""

    tuples = [
        [item["path"], item["size_bytes"], item["sha256"]]
        for item in files
    ]
    payload = json.dumps(
        tuples,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_market_manifest(
    manifest: Mapping[str, object],
    *,
    market_root: Path | str,
    requested_end_us: int,
    symbols: Sequence[str],
) -> tuple[Path, int, list[dict[str, object]]]:
    if set(manifest) != _MARKET_MANIFEST_FIELDS:
        raise ValueError("market input manifest violates the strict schema")
    if manifest.get("schema_version") != MARKET_INPUT_SCHEMA_VERSION:
        raise ValueError("market input manifest schema_version is unsupported")

    root = _canonical_market_root(market_root)
    manifest_root = manifest.get("market_root")
    if (
        not isinstance(manifest_root, str)
        or not Path(manifest_root).is_absolute()
        or str(_canonical_market_root(manifest_root)) != manifest_root
        or manifest_root != str(root)
    ):
        raise ValueError("market input manifest market_root mismatch")

    start_us = manifest.get("requested_start_us")
    end_us = manifest.get("requested_end_us")
    if (
        isinstance(start_us, bool)
        or isinstance(end_us, bool)
        or not isinstance(start_us, int)
        or not isinstance(end_us, int)
        or start_us % MINUTE_US
        or end_us % MINUTE_US
        or end_us <= start_us
        or end_us != requested_end_us
    ):
        raise ValueError("market input manifest requested market window mismatch")

    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise ValueError("market input manifest files must be a list")
    files: list[dict[str, object]] = []
    paths: list[str] = []
    file_symbols: set[str] = set()
    start_date = datetime.fromtimestamp(start_us / 1_000_000, tz=UTC).date().isoformat()
    end_date = datetime.fromtimestamp((end_us - 1) / 1_000_000, tz=UTC).date().isoformat()
    for item in raw_files:
        if not isinstance(item, dict) or set(item) != _MARKET_FILE_FIELDS:
            raise ValueError("market input manifest file violates the strict schema")
        relative = item.get("path")
        if (
            not isinstance(relative, str)
            or not relative
            or "\\" in relative
            or PurePosixPath(relative).is_absolute()
            or PurePosixPath(relative).as_posix() != relative
            or any(part in ("", ".", "..") for part in PurePosixPath(relative).parts)
        ):
            raise ValueError("market input manifest file has an invalid relative path")
        match = _MARKET_RELATIVE_PATH.fullmatch(relative)
        if match is None:
            raise ValueError("market input manifest file has an invalid relative path")
        symbol, day = match.group(1), match.group(2)
        try:
            canonical_day = datetime.strptime(day, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise ValueError("market input manifest file date is invalid") from exc
        if canonical_day != day or not start_date <= day <= end_date:
            raise ValueError("market input manifest contains an out-of-window file")
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ValueError("market input manifest file size_bytes is invalid")
        if not isinstance(digest, str) or _HEX_SHA256.fullmatch(digest) is None:
            raise ValueError("market input manifest file sha256 is invalid")
        files.append({"path": relative, "size_bytes": size, "sha256": digest})
        paths.append(relative)
        file_symbols.add(symbol)

    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("market input manifest files must be strictly sorted and unique")
    expected_symbols = set(symbols)
    if file_symbols - expected_symbols:
        raise ValueError("market input manifest contains an unexpected symbol")
    if expected_symbols - file_symbols:
        raise ValueError("market input manifest is missing an expected symbol")
    aggregate = manifest.get("files_aggregate_sha256")
    if (
        not isinstance(aggregate, str)
        or _HEX_SHA256.fullmatch(aggregate) is None
        or aggregate != _market_files_aggregate(files)
    ):
        raise ValueError("market input manifest files aggregate mismatch")
    return root, start_us, files


def _open_absolute_directory(path: Path) -> int:
    if not path.is_absolute():
        raise ValueError("market root must be absolute")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        current = os.open("/", flags)
        for component in path.parts[1:]:
            try:
                child = os.open(component, flags, dir_fd=current)
            finally:
                os.close(current)
            current = child
        return current
    except OSError as exc:
        raise ValueError("market root could not be opened safely") from exc


def _read_bound_market_file(
    *, root: Path, relative: str, expected_size: int, expected_sha256: str,
) -> bytes:
    components = PurePosixPath(relative).parts
    directory_fd = _open_absolute_directory(root)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_fd: int | None = None
    try:
        for component in components[:-1]:
            child = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child
        file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(components[-1], file_flags, dir_fd=directory_fd)
    except OSError as exc:
        if file_fd is not None:
            os.close(file_fd)
        raise ValueError("market parquet file could not be opened safely") from exc
    finally:
        os.close(directory_fd)

    with os.fdopen(file_fd, "rb", closefd=True) as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("market parquet input must be a regular file")
        if before.st_size != expected_size:
            raise ValueError("market parquet size mismatch")
        data = handle.read()
        after = os.fstat(handle.fileno())
        if _stat_identity(after) != _stat_identity(before) or len(data) != expected_size:
            raise ValueError("market parquet file changed while being read")
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("market parquet SHA-256 mismatch")
    return data


def _parse_bound_market_file(data: bytes) -> list[dict[str, object]]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    buffer = pa.BufferReader(data)
    parquet = pq.ParquetFile(buffer)
    if not set(_BAR_COLUMNS).issubset(parquet.schema_arrow.names):
        raise ValueError("bar parquet schema is missing required columns")
    return parquet.read(columns=list(_BAR_COLUMNS)).to_pylist()


def _read_manifest_closed_bars(
    *,
    root: Path,
    files: Sequence[Mapping[str, object]],
    symbols: Sequence[str],
    start_us: int,
    bar_end_us: int,
) -> list[dict[str, object]]:
    # Open the root even for an empty manifest so a symlinked/non-directory
    # root cannot hide behind an unsupported-symbol-only run.
    root_fd = _open_absolute_directory(root)
    os.close(root_fd)
    output: list[dict[str, object]] = []
    for item in files:
        relative = str(item["path"])
        match = _MARKET_RELATIVE_PATH.fullmatch(relative)
        if match is None:  # already validated; defensive against internal misuse
            raise ValueError("market input manifest file has an invalid relative path")
        symbol = match.group(1)
        data = _read_bound_market_file(
            root=root,
            relative=relative,
            expected_size=int(item["size_bytes"]),
            expected_sha256=str(item["sha256"]),
        )
        for row in _parse_bound_market_file(data):
            timestamp = row.get("bucket_ts")
            if isinstance(timestamp, bool) or not isinstance(timestamp, int):
                raise ValueError("bar parquet bucket_ts must be integer epoch microseconds")
            if start_us <= timestamp and timestamp + MINUTE_US <= bar_end_us:
                materialized = dict(row)
                materialized["symbol"] = symbol
                output.append(materialized)

    output.sort(key=lambda row: (str(row["symbol"]), int(row["bucket_ts"])))
    if not symbols:
        if output:
            raise ValueError("market input manifest yielded unexpected bars")
        return []
    grouped = _validate_bars(output)
    for symbol in symbols:
        rows = grouped.get(symbol)
        if not rows:
            raise ValueError("no fully closed 1m bars for an expected symbol")
        if (
            int(rows[0]["bucket_ts"]) != start_us
            or int(rows[-1]["bucket_ts"]) + MINUTE_US != bar_end_us
        ):
            raise ValueError(f"requested market window boundary is incomplete for {symbol}")
    return output


def _load_market_manifest(
    *,
    path: Path,
    expected_sha256: str,
    market_root: Path | str,
    requested_end_us: int,
    symbols: Sequence[str],
) -> tuple[Path, int, list[dict[str, object]], dict[str, object]]:
    if _HEX_SHA256.fullmatch(expected_sha256) is None:
        raise ValueError("expected market input manifest SHA-256 has invalid format")
    data = _read_regular_bytes(path, label="market input manifest")
    manifest_sha256 = hashlib.sha256(data).hexdigest()
    if manifest_sha256 != expected_sha256:
        raise ValueError("market input manifest SHA-256 mismatch")
    manifest = _decode_strict_json_object(data, label="market input manifest")
    root, start_us, files = _validate_market_manifest(
        manifest,
        market_root=market_root,
        requested_end_us=requested_end_us,
        symbols=symbols,
    )
    binding = {
        "manifest_sha256": manifest_sha256,
        "file_count": len(files),
        "files_aggregate_sha256": manifest["files_aggregate_sha256"],
    }
    return root, start_us, files, binding


def _runtime_binding() -> dict[str, str]:
    import pyarrow as pa

    return {
        "python_version": platform.python_version(),
        "pyarrow_version": pa.__version__,
        "implementation_sha256": _FROZEN_IMPLEMENTATION_SHA256,
    }


def _decode_lifecycles(data: bytes) -> list[Mapping[str, object]]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("lifecycle input must be UTF-8") from exc
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            raise ValueError("lifecycle input is not valid JSON or JSONL") from exc
    if isinstance(parsed, dict):
        if "lifecycles" in parsed:
            if set(parsed) != {"lifecycles"}:
                raise ValueError("lifecycle envelope contains fields outside the strict schema")
            parsed = parsed.get("lifecycles")
        else:
            # A one-record JSONL file is also valid JSON as a whole.
            parsed = [parsed]
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise ValueError("lifecycle input must contain a JSON array of objects")
    return parsed


def _load_lifecycles_with_binding(path: Path) -> tuple[list[Mapping[str, object]], str]:
    data = _read_regular_bytes(path)
    return _decode_lifecycles(data), hashlib.sha256(data).hexdigest()


def _load_lifecycles(path: Path) -> list[Mapping[str, object]]:
    return _load_lifecycles_with_binding(path)[0]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Conservative Discord 1m lifecycle backtest")
    parser.add_argument("--lifecycles", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--market-input-manifest", type=Path, required=True)
    parser.add_argument("--expected-market-input-sha256", required=True)
    parser.add_argument("--bar-end", required=True, help="exclusive evidence horizon; timezone-aware ISO instant")
    parser.add_argument("--market-start", help="inclusive, aligned market window start")
    parser.add_argument("--expected-input-sha256")
    parser.add_argument("--expected-input-count", type=int)
    parser.add_argument("--fee-bps", type=float, required=True)
    parser.add_argument("--slippage-bps", type=float, required=True)
    parser.add_argument("--max-entry-wait-minutes", type=int, default=1440)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    lifecycles, input_sha256 = _load_lifecycles_with_binding(args.lifecycles)
    if args.expected_input_sha256 is not None:
        if _HEX_SHA256.fullmatch(args.expected_input_sha256) is None:
            raise ValueError("expected lifecycle input SHA-256 has invalid format")
        if args.expected_input_sha256 != input_sha256:
            raise ValueError("lifecycle input SHA-256 mismatch")
    if args.expected_input_count is not None:
        if args.expected_input_count < 0 or args.expected_input_count != len(lifecycles):
            raise ValueError("lifecycle input count mismatch")
    for index, row in enumerate(lifecycles):
        _validate_lifecycle_schema(row, index=index)
    bar_end_us = _parse_instant_us(args.bar_end, field="bar_end")
    active = [
        row for row in lifecycles
        if row.get("evaluable") is True
        and isinstance(row.get("confidence"), str)
        and str(row["confidence"]).strip().casefold() == "high"
        and row.get("duplicate_of") is None
        and row.get("exclusion_reason") is None
        and not (
            isinstance(row.get("terminal_status"), str)
            and str(row["terminal_status"]).startswith(("cancelled_", "expired_"))
        )
    ]
    symbols = sorted({str(row.get("symbol")) for row in active if row.get("symbol") in SUPPORTED_SYMBOLS})
    root, manifest_start_us, market_files, market_binding = _load_market_manifest(
        path=args.market_input_manifest,
        expected_sha256=args.expected_market_input_sha256,
        market_root=args.market_root,
        requested_end_us=bar_end_us,
        symbols=symbols,
    )
    if args.market_start is not None:
        cli_start_us = _parse_instant_us(args.market_start, field="market_start")
        if cli_start_us != manifest_start_us:
            raise ValueError("market_start does not match the market input manifest")
    start_us = manifest_start_us
    if symbols:
        derived_start = min(
            _next_minute_us(_parse_instant_us(row.get("effective_at"), field="effective_at"))
            for row in active if row.get("symbol") in SUPPORTED_SYMBOLS
        )
        if start_us > derived_start:
            raise ValueError("requested market start omits an active lifecycle")
    bars = _read_manifest_closed_bars(
        root=root,
        files=market_files,
        symbols=symbols,
        start_us=start_us,
        bar_end_us=bar_end_us,
    )
    result = simulate_lifecycles(
        lifecycles=lifecycles,
        bars=bars,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        max_entry_wait_minutes=args.max_entry_wait_minutes,
        expected_market_start_us=start_us if symbols else None,
        expected_market_end_us=bar_end_us if symbols else None,
    )
    result["input_binding"] = {"count": len(lifecycles), "sha256": input_sha256}
    result["market_input_binding"] = market_binding
    result["runtime_binding"] = _runtime_binding()
    _scan_sensitive(result, output=True)
    json.dump(result, sys.stdout, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through ``main`` tests
    raise SystemExit(main())
