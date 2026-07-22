"""Hash-bound publication seam for conservative Discord blogger backtests.

The stdlib-only main repository never imports the quant runtime.  It validates
the manually curated lifecycle envelope, freezes the exact Parquet inventory,
invokes the pinned quant interpreter, and publishes a complete derivative
directory with an atomic no-clobber rename.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import stat
import statistics
import subprocess
import tempfile
from typing import Mapping, Sequence

from .discord_blogger_results import (
    _create_stage,
    _fsync_stage,
    _quarantine_name,
    _remove_stage,
    _verify_stage,
    _write_at,
)
from .discord_reference_sidecar import _RootAnchor
from .discord_sharding import _rename_directory_noreplace_at


CURATION_MANIFEST_SHA256 = (
    "18aa1a96c8956bd0c74bc53d0d9355858bc080c8b74873b49bf6fe04eaf863c1"
)
QUANT_PYTHON = Path("/Users/hzh/opt/anaconda3/envs/quant/bin/python")
_QUANT_MODULE = "quant.discord_backtest"
_SCHEMA_VERSION = "discord-blogger-backtest-v1"
_MINUTE_US = 60_000_000
_SUPPORTED_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT"})
_PROFILE_COUNTS = {
    "always-win-trader": 15,
    "analyst-nick": 2,
    "coin-chief-v1": 58,
    "shuqin-v1": 141,
}
_CURATION_KEYS = frozenset({
    "schema_version", "methodology", "asof", "market_bar_end_exclusive",
    "event_manifest", "sources", "selection", "limitations",
})
_SELECTION_KEYS = frozenset({
    "evaluable", "confidence", "duplicate_of", "exclusion_reason",
    "excluded_terminal_prefixes", "input_row_count", "selected_row_count",
    "selected_profile_counts",
})
_SOURCE_FIELDS = frozenset({
    "confidence", "direction", "duplicate_of", "effective_at", "entry",
    "entry_high", "entry_low", "evaluable", "exclusion_reason",
    "explicit_reference_ids", "link_basis", "open_message_id",
    "parameter_fingerprint", "profile", "sl", "symbol", "tps",
})
_OPTIONAL_SOURCE_FIELDS = frozenset({"terminal_status"})
_EVENT_FILES = frozenset({
    "latest-calls.json", "latest-calls.md", "message-decisions.jsonl",
    "trade-events.jsonl", "trade-lifecycles.jsonl",
})
_EVENT_MANIFEST_KEYS = frozenset({
    "artifact_kind", "decision_count", "event_count", "files",
    "latest_call_count", "lifecycle_count", "provenance",
})
_CORE_KEYS = frozenset({
    "schema_version", "bar_interval", "funding", "input_binding",
    "market_input_binding", "market_window", "parameters", "runtime_binding",
    "summary", "trades",
})
_TRADE_REQUIRED_KEYS = frozenset({
    "lifecycle_id", "profile", "symbol", "direction", "signal_at_us",
    "status", "outcome", "exclusion_reason", "entry_bucket_ts",
    "entry_price", "exit_bucket_ts", "exit_reason", "tp_fills",
    "remaining_fraction", "gross_return_pct", "fees_pct", "slippage_pct",
    "net_return_pct",
})
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_FINGERPRINT = re.compile(r"[A-Za-z0-9,|-]{1,128}\Z")
_SAFE_REASON = re.compile(r"[A-Za-z0-9_.:-]{1,192}\Z")
_SENSITIVE_TEXT = re.compile(
    r"(?:https?|ftp)://|(?:bearer|bot)\s+[A-Za-z0-9._~-]+|"
    r"(?:access[_-]?token|authorization|x-amz-signature|signature)\s*[=:]",
    re.IGNORECASE,
)
_SENSITIVE_KEYS = frozenset({
    "authorization", "bot_token", "content", "cookie", "logical_key",
    "message_body", "proxy_url", "raw_url", "signed_url", "token",
})
_ARROW_SANDBOX_STDERR = re.compile(
    r"^/.*/arrow/cpp/src/arrow/util/cpu_info\.cc:\d+: IOError: "
    r"sysctlbyname failed for 'hw\.(?:l1dcachesize|l2cachesize|l3cachesize|optional\.neon)'\. "
    r"Detail: \[errno 1\] Operation not permitted$"
)


def run_quant_blogger_backtest(
    *,
    curation_manifest: Path,
    curation_manifest_sha256: str,
    market_root: Path,
    output_dir: Path,
    fee_bps: float,
    slippage_bps: float,
    max_entry_wait_minutes: int = 1440,
    timeout_seconds: int = 300,
) -> dict[str, object]:
    """Run the pinned quant subprocess and atomically publish bound results."""

    expected_sha = _require_sha(curation_manifest_sha256, "curation manifest commitment")
    fee = _cost_bps(fee_bps, "fee_bps")
    slippage = _cost_bps(slippage_bps, "slippage_bps")
    if isinstance(max_entry_wait_minutes, bool) or not isinstance(max_entry_wait_minutes, int):
        raise ValueError("max_entry_wait_minutes must be a positive integer")
    if max_entry_wait_minutes <= 0 or max_entry_wait_minutes > 1_000_000:
        raise ValueError("max_entry_wait_minutes must be a positive integer")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
        raise ValueError("timeout_seconds must be a positive integer")
    if timeout_seconds <= 0 or timeout_seconds > 86_400:
        raise ValueError("timeout_seconds must be a positive integer")

    curation_path = _absolute(curation_manifest)
    curation_bytes = _read_regular(curation_path, "curation manifest")
    if hashlib.sha256(curation_bytes).hexdigest() != expected_sha:
        raise ValueError("curation manifest commitment mismatch")
    curation = _load_json(curation_bytes, "curation manifest")
    (
        selected,
        curation_rows,
        source_bindings,
        event_binding,
        end_us,
        input_profile_counts,
        curation_exclusions,
    ) = _validate_curation(curation=curation, curation_path=curation_path)
    curation_input_bytes = b"".join(_canonical(row) + b"\n" for row in curation_rows)
    lifecycle_bytes = b"".join(_canonical(row) + b"\n" for row in selected)
    lifecycle_sha = hashlib.sha256(lifecycle_bytes).hexdigest()
    start_us = min(_next_minute(_parse_instant(row["effective_at"], "effective_at")) for row in selected)
    if start_us >= end_us:
        raise ValueError("curation market window is empty")
    symbols = sorted({str(row["symbol"]) for row in selected if row["symbol"] in _SUPPORTED_SYMBOLS})
    if symbols != ["BTCUSDT", "ETHUSDT"]:
        raise ValueError("curation selected symbols must be BTCUSDT and ETHUSDT")

    market_path = _absolute(market_root)
    market_before = _market_inventory(
        market_root=market_path,
        symbols=symbols,
        start_us=start_us,
        end_us=end_us,
    )
    market_request = {
        "schema_version": "market-input-request-v1",
        "market_root": str(market_path),
        "requested_start_us": start_us,
        "requested_end_us": end_us,
        "files": market_before["files"],
        "files_aggregate_sha256": market_before["files_aggregate_sha256"],
    }
    market_request_bytes = _canonical(market_request) + b"\n"
    market_request_sha = hashlib.sha256(market_request_bytes).hexdigest()
    repository = Path(__file__).resolve().parents[2]
    wrapper_path = _absolute(Path(__file__))
    quant_path = repository / "agent-harness" / "quant" / "quant" / "discord_backtest.py"
    wrapper_bytes = _read_regular(wrapper_path, "backtest wrapper implementation")
    quant_bytes = _read_regular(quant_path, "quant implementation")
    wrapper_sha = hashlib.sha256(wrapper_bytes).hexdigest()
    quant_sha = hashlib.sha256(quant_bytes).hexdigest()
    with tempfile.TemporaryDirectory(prefix="discord-backtest-") as temporary_directory:
        lifecycle_path = Path(temporary_directory) / "curated-lifecycles.jsonl"
        market_manifest_path = Path(temporary_directory) / "market-input-manifest.json"
        _write_private_file(lifecycle_path, lifecycle_bytes)
        _write_private_file(market_manifest_path, market_request_bytes)
        core = _run_quant(
            lifecycle_path=lifecycle_path,
            lifecycle_sha=lifecycle_sha,
            lifecycle_count=len(selected),
            market_root=market_path,
            market_input_manifest=market_manifest_path,
            market_input_sha=market_request_sha,
            market_start_us=start_us,
            market_end_us=end_us,
            fee_bps=fee,
            slippage_bps=slippage,
            max_entry_wait_minutes=max_entry_wait_minutes,
            timeout_seconds=timeout_seconds,
        )

    curation_after = _read_regular(curation_path, "curation manifest")
    if curation_after != curation_bytes:
        raise ValueError("curation manifest changed during quant execution")
    (
        selected_after,
        curation_rows_after,
        sources_after,
        event_after,
        end_after,
        input_counts_after,
        curation_exclusions_after,
    ) = _validate_curation(
        curation=_load_json(curation_after, "curation manifest"),
        curation_path=curation_path,
    )
    if (
        _canonical(selected_after) != _canonical(selected)
        or _canonical(curation_rows_after) != _canonical(curation_rows)
        or _canonical(sources_after) != _canonical(source_bindings)
        or event_after != event_binding
        or end_after != end_us
        or input_counts_after != input_profile_counts
        or curation_exclusions_after != curation_exclusions
    ):
        raise ValueError("curation inputs changed during quant execution")
    market_after = _market_inventory(
        market_root=market_path,
        symbols=symbols,
        start_us=start_us,
        end_us=end_us,
    )
    if _canonical(market_after) != _canonical(market_before):
        raise ValueError("market input changed during quant execution")
    if _read_regular(wrapper_path, "backtest wrapper implementation") != wrapper_bytes:
        raise ValueError("backtest wrapper implementation changed during quant execution")
    if _read_regular(quant_path, "quant implementation") != quant_bytes:
        raise ValueError("quant implementation changed during quant execution")
    trades = _validate_core_result(
        core,
        selected=selected,
        lifecycle_sha=lifecycle_sha,
        market_input_sha=market_request_sha,
        market_file_count=len(market_before["files"]),  # type: ignore[arg-type]
        market_aggregate_sha=str(market_before["files_aggregate_sha256"]),
        quant_implementation_sha=quant_sha,
        start_us=start_us,
        end_us=end_us,
        fee_bps=fee,
        slippage_bps=slippage,
        max_entry_wait_minutes=max_entry_wait_minutes,
    )
    market_validation = _market_validation(core)
    report = _build_report(
        selected=selected,
        trades=trades,
        fee_bps=fee,
        slippage_bps=slippage,
        ttl=max_entry_wait_minutes,
        start_us=start_us,
        end_us=end_us,
        input_profile_counts=input_profile_counts,
        curation_exclusions=curation_exclusions,
    )
    artifacts = {
        "curation-input.jsonl": curation_input_bytes,
        "curated-lifecycles.jsonl": lifecycle_bytes,
        "trades.jsonl": b"".join(_canonical(row) + b"\n" for row in trades),
        "backtest-report.json": _canonical(report) + b"\n",
        "backtest-report.md": _render_report(report).encode("utf-8"),
        "market-input-manifest.json": market_request_bytes,
    }
    runtime_binding = core["runtime_binding"]
    assert isinstance(runtime_binding, dict)
    manifest = {
        "artifact_kind": _SCHEMA_VERSION,
        "curation_manifest": {"path": str(curation_path), "sha256": expected_sha},
        "event_manifest": event_binding,
        "sources": source_bindings,
        "curation_input_binding": {
            "sha256": hashlib.sha256(curation_input_bytes).hexdigest(),
            "count": len(curation_rows),
        },
        "input_binding": {"sha256": lifecycle_sha, "count": len(selected)},
        "market_input_binding": {
            "manifest_sha256": market_request_sha,
            "file_count": len(market_before["files"]),  # type: ignore[arg-type]
            "files_aggregate_sha256": market_before["files_aggregate_sha256"],
        },
        "market_validation": market_validation,
        "implementation": {
            "wrapper_sha256": wrapper_sha,
            "quant_sha256": quant_sha,
        },
        "runtime": {
            "wrapper": {
                "python_version": platform.python_version(),
                "python_implementation": platform.python_implementation(),
            },
            "quant": runtime_binding,
        },
        "parameters": {
            "fee_bps": fee,
            "slippage_bps": slippage,
            "max_entry_wait_minutes": max_entry_wait_minutes,
            "timeout_seconds": timeout_seconds,
        },
        "files": {
            name: hashlib.sha256(content).hexdigest()
            for name, content in sorted(artifacts.items())
        },
    }
    _scan_sensitive({
        **manifest,
        "report": report,
        "trades": trades,
        "market": market_request,
    })
    artifacts["backtest-manifest.json"] = _canonical(manifest) + b"\n"
    output_path = _absolute(output_dir)
    _publish(output_path, artifacts)
    return {
        "output_dir": str(output_path),
        "manifest_path": str(output_path / "backtest-manifest.json"),
        "selected_lifecycle_count": len(selected),
        "closed_trade_count": int(core["summary"]["closed"]),  # type: ignore[index]
    }


def _absolute(path: Path | str) -> Path:
    value = Path(path).expanduser()
    absolute = value if value.is_absolute() else (Path.cwd() / value).absolute()
    # Canonicalize platform aliases in ancestors (macOS /var -> /private/var)
    # while deliberately preserving the final component so a caller-supplied
    # file/directory symlink is still rejected by the dirfd walk.
    return absolute.parent.resolve(strict=False) / absolute.name


def _root_relative(path: Path) -> Path:
    try:
        return path.relative_to(Path(path.anchor))
    except ValueError as exc:  # pragma: no cover - defensive on unusual platforms
        raise ValueError("path must be absolute") from exc


def _read_regular(path: Path, label: str) -> bytes:
    if not path.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    anchor = _RootAnchor.open(Path(path.anchor))
    try:
        return anchor.read_regular(_root_relative(path), label)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} must be a stable regular non-symlink file") from exc
    finally:
        anchor.close()


def _resolve_bound_path(raw: object, *, base: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw or "\x00" in raw or "://" in raw:
        raise ValueError(f"{label} path is invalid")
    path = Path(raw).expanduser()
    return _absolute(path if path.is_absolute() else base / path)


def _load_json(content: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _validate_curation(
    *, curation: Mapping[str, object], curation_path: Path,
) -> tuple[
    list[dict[str, object]], list[dict[str, object]], list[dict[str, object]],
    dict[str, str], int, dict[str, int], dict[str, dict[str, int]],
]:
    if set(curation) != _CURATION_KEYS:
        raise ValueError("curation manifest schema is invalid")
    if curation.get("schema_version") != "discord-blogger-curation-v1":
        raise ValueError("curation manifest schema version is invalid")
    if curation.get("methodology") != "initial_plan_levels_only":
        raise ValueError("curation methodology is invalid")
    _parse_instant(curation.get("asof"), "curation asof")
    end_us = _parse_instant(
        curation.get("market_bar_end_exclusive"), "market_bar_end_exclusive"
    )
    if end_us % _MINUTE_US:
        raise ValueError("market_bar_end_exclusive must be minute aligned")
    base = curation_path.parent
    event_binding = _validate_event_manifest(curation.get("event_manifest"), base=base)
    sources = curation.get("sources")
    if not isinstance(sources, list) or len(sources) != 2:
        raise ValueError("curation sources must contain exactly two descriptors")
    rows: list[dict[str, object]] = []
    bindings: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, dict) or set(source) != {"path", "sha256", "row_count"}:
            raise ValueError("curation source descriptor schema is invalid")
        path = _resolve_bound_path(source.get("path"), base=base, label="curation source")
        if str(path) in seen_paths:
            raise ValueError("curation source paths are duplicated")
        seen_paths.add(str(path))
        digest = _require_sha(source.get("sha256"), "curation source")
        content = _read_regular(path, "curation source")
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("curation source hash mismatch")
        source_rows = _load_jsonl(content, f"curation source {index}")
        count = source.get("row_count")
        if isinstance(count, bool) or not isinstance(count, int) or count != len(source_rows):
            raise ValueError("curation source row count mismatch")
        for row in source_rows:
            fields = set(row)
            if fields not in (_SOURCE_FIELDS, _SOURCE_FIELDS | _OPTIONAL_SOURCE_FIELDS):
                raise ValueError("curation source row schema is invalid")
            _validate_source_row(row)
        rows.extend(source_rows)
        bindings.append({"path": str(path), "sha256": digest, "row_count": count})
    selection = curation.get("selection")
    if not isinstance(selection, dict) or set(selection) != _SELECTION_KEYS:
        raise ValueError("curation selection schema is invalid")
    expected_selection = {
        "evaluable": True,
        "confidence": "high",
        "duplicate_of": None,
        "exclusion_reason": None,
        "excluded_terminal_prefixes": ["cancelled_", "expired_"],
        "input_row_count": 324,
        "selected_row_count": 216,
        "selected_profile_counts": _PROFILE_COUNTS,
    }
    if _canonical(selection) != _canonical(expected_selection) or len(rows) != 324:
        raise ValueError("curation selection contract is invalid")
    selected = [row for row in rows if _selected(row)]
    if len(selected) != 216:
        raise ValueError("curation selected row count mismatch")
    profiles = Counter(str(row["profile"]) for row in selected)
    if dict(sorted(profiles.items())) != dict(sorted(_PROFILE_COUNTS.items())):
        raise ValueError("curation selected profile counts mismatch")
    identifiers = [str(row["open_message_id"]) for row in selected]
    fingerprints = [str(row["parameter_fingerprint"]) for row in selected]
    if len(set(identifiers)) != len(identifiers) or len(set(fingerprints)) != len(fingerprints):
        raise ValueError("curation selected lifecycle identifiers are duplicated")
    limitations = curation.get("limitations")
    if not isinstance(limitations, list) or not limitations or not all(
        isinstance(value, str) and value for value in limitations
    ):
        raise ValueError("curation limitations are invalid")
    if "funding_unmodeled" not in limitations:
        raise ValueError("curation must disclose unmodeled funding")
    _scan_sensitive(curation)
    input_profile_counts = Counter(str(row["profile"]) for row in rows)
    curation_exclusions: dict[str, Counter[str]] = {
        profile: Counter() for profile in _PROFILE_COUNTS
    }
    for row in rows:
        if _selected(row):
            continue
        reason = _curation_exclusion_reason(row)
        curation_exclusions[str(row["profile"])][reason] += 1
    excluded_count = sum(sum(counts.values()) for counts in curation_exclusions.values())
    if excluded_count != len(rows) - len(selected) or excluded_count != 108:
        raise ValueError("curation exclusion classification is not conservative")
    return (
        selected,
        rows,
        bindings,
        event_binding,
        end_us,
        dict(sorted(input_profile_counts.items())),
        {
            profile: dict(sorted(counts.items()))
            for profile, counts in sorted(curation_exclusions.items())
        },
    )


def _validate_event_manifest(value: object, *, base: Path) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError("event manifest binding schema is invalid")
    path = _resolve_bound_path(value.get("path"), base=base, label="event manifest")
    expected = _require_sha(value.get("sha256"), "event manifest")
    content = _read_regular(path, "event manifest")
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("event manifest hash mismatch")
    manifest = _load_json(content, "event manifest")
    if set(manifest) != _EVENT_MANIFEST_KEYS or manifest.get("artifact_kind") != "discord-blogger-events-v1":
        raise ValueError("event manifest schema is invalid")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != _EVENT_FILES:
        raise ValueError("event manifest file inventory is invalid")
    for name in sorted(_EVENT_FILES):
        digest = _require_sha(files.get(name), "event artifact")
        artifact = path.parent / name
        if hashlib.sha256(_read_regular(artifact, "event artifact")).hexdigest() != digest:
            raise ValueError("event artifact hash mismatch")
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict) or set(provenance) != {
        "asof", "closure_audit", "corpus_commitment", "corpus_message_count",
        "parser_implementation_sha256", "profiles",
    }:
        raise ValueError("event manifest provenance is invalid")
    _parse_instant(provenance.get("asof"), "event asof")
    _require_sha(provenance.get("corpus_commitment"), "event corpus commitment")
    _require_sha(provenance.get("parser_implementation_sha256"), "event parser commitment")
    closure = provenance.get("closure_audit")
    if not isinstance(closure, dict) or set(closure) != {"path", "sha256", "input_file_sha256"}:
        raise ValueError("event closure provenance is invalid")
    _require_sha(closure.get("sha256"), "event closure")
    inputs = closure.get("input_file_sha256")
    if not isinstance(inputs, dict) or set(inputs) != {"census", "head_catchup", "merge_audit"}:
        raise ValueError("event closure inputs are invalid")
    for digest in inputs.values():
        _require_sha(digest, "event closure input")
    return {"path": str(path), "sha256": expected}


def _load_jsonl(content: bytes, label: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} is not UTF-8") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} is invalid JSONL") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{label} rows must be objects")
        rows.append(row)
    return rows


def _validate_source_row(row: Mapping[str, object]) -> None:
    _scan_sensitive(row)
    if row.get("profile") not in _PROFILE_COUNTS:
        raise ValueError("curation source row profile is invalid")
    if row.get("symbol") not in _SUPPORTED_SYMBOLS or row.get("direction") not in {"long", "short"}:
        raise ValueError("curation source row market identity is invalid")
    if row.get("confidence") not in {"high", "medium", "low"}:
        raise ValueError("curation source row confidence is invalid")
    if row.get("evaluable") not in {True, False}:
        raise ValueError("curation source row evaluable is invalid")
    for field in ("exclusion_reason", "terminal_status"):
        value = row.get(field)
        if value is not None and (
            not isinstance(value, str) or _SAFE_REASON.fullmatch(value) is None
        ):
            raise ValueError("curation source row classification is invalid")
    _parse_instant(row.get("effective_at"), "source effective_at")
    if not isinstance(row.get("open_message_id"), str) or not str(row["open_message_id"]).isdigit():
        raise ValueError("curation source row message identity is invalid")
    fingerprint = row.get("parameter_fingerprint")
    if not isinstance(fingerprint, str) or _SAFE_FINGERPRINT.fullmatch(fingerprint) is None:
        raise ValueError("curation source row parameter fingerprint is invalid")
    references = row.get("explicit_reference_ids")
    if not isinstance(references, list) or not all(isinstance(item, str) and item.isdigit() for item in references):
        raise ValueError("curation source row references are invalid")
    if not isinstance(row.get("tps"), list):
        raise ValueError("curation source row targets are invalid")
    for field in ("entry", "entry_low", "entry_high", "sl"):
        value = row.get(field)
        if value is not None and not _finite(value):
            raise ValueError("curation source row price is invalid")
    if any(not _finite(value) for value in row["tps"]):  # type: ignore[index]
        raise ValueError("curation source row target is invalid")


def _selected(row: Mapping[str, object]) -> bool:
    terminal = row.get("terminal_status")
    return (
        row.get("evaluable") is True
        and row.get("confidence") == "high"
        and row.get("duplicate_of") is None
        and row.get("exclusion_reason") is None
        and not (
            isinstance(terminal, str)
            and terminal.startswith(("cancelled_", "expired_"))
        )
    )


def _curation_exclusion_reason(row: Mapping[str, object]) -> str:
    reason = row.get("exclusion_reason")
    if isinstance(reason, str) and reason:
        return f"source:{reason}"
    terminal = row.get("terminal_status")
    if isinstance(terminal, str) and terminal.startswith(("cancelled_", "expired_")):
        return f"terminal:{terminal}"
    if row.get("duplicate_of") is not None:
        return "duplicate"
    if row.get("evaluable") is not True:
        return "not_evaluable"
    confidence = row.get("confidence")
    if confidence != "high":
        return f"confidence:{confidence}"
    raise ValueError("curation excluded row has no deterministic reason")


def _market_inventory(
    *, market_root: Path, symbols: Sequence[str], start_us: int, end_us: int,
) -> dict[str, object]:
    root = Path(market_root.anchor)
    anchor = _RootAnchor.open(root)
    market_relative = _root_relative(market_root)
    start_day = datetime.fromtimestamp(start_us / 1_000_000, tz=UTC).date().isoformat()
    end_day = datetime.fromtimestamp((end_us - 1) / 1_000_000, tz=UTC).date().isoformat()
    files: list[dict[str, object]] = []
    try:
        with anchor.directory(market_relative, create=False) as market:
            market.verify()
            for symbol in symbols:
                symbol_relative = Path("bars_1m", f"symbol={symbol}")
                with anchor.directory(market_relative / symbol_relative, create=False) as symbol_dir:
                    symbol_dir.verify()
                    for date_name in sorted(os.listdir(symbol_dir.fd)):
                        if not date_name.startswith("date="):
                            continue
                        day = date_name.removeprefix("date=")
                        if day < start_day or day > end_day:
                            continue
                        with anchor.directory(
                            market_relative / symbol_relative / date_name,
                            create=False,
                        ) as date_dir:
                            date_dir.verify()
                            for name in sorted(os.listdir(date_dir.fd)):
                                if Path(name).suffix != ".parquet":
                                    continue
                                flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                                try:
                                    descriptor = os.open(name, flags, dir_fd=date_dir.fd)
                                except OSError as exc:
                                    raise ValueError("market parquet symlink or unsafe file") from exc
                                try:
                                    before = os.fstat(descriptor)
                                    if not stat.S_ISREG(before.st_mode):
                                        raise ValueError("market parquet must be a regular non-symlink file")
                                    digest = hashlib.sha256()
                                    size = 0
                                    while True:
                                        chunk = os.read(descriptor, 1024 * 1024)
                                        if not chunk:
                                            break
                                        digest.update(chunk)
                                        size += len(chunk)
                                    after = os.fstat(descriptor)
                                    if (
                                        (before.st_dev, before.st_ino, before.st_size)
                                        != (after.st_dev, after.st_ino, after.st_size)
                                        or size != after.st_size
                                    ):
                                        raise ValueError("market parquet changed during hashing")
                                    files.append({
                                        "path": Path("bars_1m", f"symbol={symbol}", date_name, name).as_posix(),
                                        "size_bytes": size,
                                        "sha256": digest.hexdigest(),
                                    })
                                finally:
                                    os.close(descriptor)
                            date_dir.verify()
                    symbol_dir.verify()
            market.verify()
    except (OSError, ValueError) as exc:
        message = str(exc).casefold()
        if "symlink" in message:
            raise ValueError("market input contains a symlink") from exc
        raise ValueError("market input inventory is unsafe") from exc
    finally:
        anchor.close()
    if not files:
        raise ValueError("market input inventory is empty")
    files.sort(key=lambda item: str(item["path"]))
    aggregate_rows = [
        [row["path"], row["size_bytes"], row["sha256"]]
        for row in files
    ]
    return {
        "files": files,
        "files_aggregate_sha256": hashlib.sha256(_canonical(aggregate_rows)).hexdigest(),
    }


def _run_quant(
    *, lifecycle_path: Path, lifecycle_sha: str, lifecycle_count: int,
    market_root: Path, market_input_manifest: Path, market_input_sha: str,
    market_start_us: int, market_end_us: int,
    fee_bps: float, slippage_bps: float, max_entry_wait_minutes: int,
    timeout_seconds: int,
) -> dict[str, object]:
    repository = Path(__file__).resolve().parents[2]
    quant_root = repository / "agent-harness" / "quant"
    command = [
        str(QUANT_PYTHON), "-m", _QUANT_MODULE,
        "--lifecycles", str(lifecycle_path),
        "--market-root", str(market_root),
        "--market-input-manifest", str(market_input_manifest),
        "--expected-market-input-sha256", market_input_sha,
        "--market-start", _instant_text(market_start_us),
        "--bar-end", _instant_text(market_end_us),
        "--expected-input-sha256", lifecycle_sha,
        "--expected-input-count", str(lifecycle_count),
        "--fee-bps", str(fee_bps),
        "--slippage-bps", str(slippage_bps),
        "--max-entry-wait-minutes", str(max_entry_wait_minutes),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=quant_root,
            env={
                "HOME": os.environ.get("HOME", ""),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": os.defpath,
                "PYTHONHASHSEED": "0",
            },
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("quant backtest subprocess timed out") from exc
    except OSError as exc:
        raise RuntimeError("quant backtest subprocess could not start") from exc
    if completed.returncode != 0:
        raise RuntimeError("quant backtest subprocess failed")
    stderr_lines = [line for line in completed.stderr.splitlines() if line.strip()]
    if any(_ARROW_SANDBOX_STDERR.fullmatch(line) is None for line in stderr_lines):
        raise RuntimeError("quant backtest subprocess emitted unexpected stderr")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("quant backtest subprocess returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("quant backtest subprocess returned invalid JSON")
    return value


def _validate_core_result(
    core: Mapping[str, object], *, selected: Sequence[Mapping[str, object]],
    lifecycle_sha: str, market_input_sha: str, market_file_count: int,
    market_aggregate_sha: str, quant_implementation_sha: str,
    start_us: int, end_us: int, fee_bps: float, slippage_bps: float,
    max_entry_wait_minutes: int,
) -> list[dict[str, object]]:
    _scan_sensitive(core)
    if set(core) != _CORE_KEYS or core.get("schema_version") != "discord-backtest-v1":
        raise ValueError("quant result schema is invalid")
    if core.get("bar_interval") != "1m" or core.get("funding") != "unmodeled":
        raise ValueError("quant methodology binding is invalid")
    binding = core.get("input_binding")
    if binding != {"sha256": lifecycle_sha, "count": len(selected)}:
        raise ValueError("quant input binding mismatch")
    market_binding = core.get("market_input_binding")
    if market_binding != {
        "manifest_sha256": market_input_sha,
        "file_count": market_file_count,
        "files_aggregate_sha256": market_aggregate_sha,
    }:
        raise ValueError("quant market input binding mismatch")
    runtime = core.get("runtime_binding")
    if not isinstance(runtime, dict) or set(runtime) != {
        "python_version", "pyarrow_version", "implementation_sha256",
    }:
        raise ValueError("quant runtime binding is invalid")
    if (
        not isinstance(runtime.get("python_version"), str)
        or not runtime["python_version"]
        or not isinstance(runtime.get("pyarrow_version"), str)
        or not runtime["pyarrow_version"]
        or runtime.get("implementation_sha256") != quant_implementation_sha
    ):
        raise ValueError("quant runtime binding mismatch")
    parameters = core.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("quant parameters are invalid")
    if (
        parameters.get("fee_bps") != fee_bps
        or parameters.get("slippage_bps") != slippage_bps
        or parameters.get("max_entry_wait_minutes") != max_entry_wait_minutes
    ):
        raise ValueError("quant parameter binding mismatch")
    window = core.get("market_window")
    if not isinstance(window, dict) or set(window) != {"requested_start_us", "requested_end_us", "symbols"}:
        raise ValueError("quant market window is invalid")
    if window.get("requested_start_us") != start_us or window.get("requested_end_us") != end_us:
        raise ValueError("quant market window mismatch")
    symbols = window.get("symbols")
    if not isinstance(symbols, dict) or set(symbols) != _SUPPORTED_SYMBOLS:
        raise ValueError("quant market symbol window is invalid")
    for symbol, descriptor in symbols.items():
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "bar_count", "first_bucket_ts", "last_bucket_ts",
        }:
            raise ValueError("quant market symbol descriptor is invalid")
        count = descriptor.get("bar_count")
        first = descriptor.get("first_bucket_ts")
        last = descriptor.get("last_bucket_ts")
        expected_count = (end_us - start_us) // _MINUTE_US
        if count != expected_count or first != start_us or last != end_us - _MINUTE_US:
            raise ValueError("quant market window is not complete and contiguous")
    raw_trades = core.get("trades")
    if not isinstance(raw_trades, list) or len(raw_trades) != len(selected):
        raise ValueError("quant trade inventory differs from selected lifecycles")
    expected = {str(row["open_message_id"]): row for row in selected}
    trades: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in raw_trades:
        if not isinstance(raw, dict) or set(raw) != _TRADE_REQUIRED_KEYS:
            raise ValueError("quant trade row is invalid")
        identifier = raw.get("lifecycle_id")
        if not isinstance(identifier, str) or identifier in seen or identifier not in expected:
            raise ValueError("quant trade row is invalid")
        seen.add(identifier)
        source = expected[identifier]
        _validate_trade_row(raw, source=source, start_us=start_us, end_us=end_us)
        trades.append(dict(raw))
    if seen != set(expected):
        raise ValueError("quant trade inventory differs from selected lifecycles")
    summary = core.get("summary")
    if not isinstance(summary, dict) or set(summary) != {
        "lifecycles", "closed", "wins", "losses", "flat", "unfilled",
        "right_censored", "excluded", "win_rate",
    }:
        raise ValueError("quant summary schema is invalid")
    actual = Counter(str(row.get("status")) for row in trades)
    if set(actual) - {"closed", "unfilled", "right_censored", "excluded"}:
        raise ValueError("quant summary does not match trade rows")
    outcome = Counter(str(row.get("outcome")) for row in trades if row.get("status") == "closed")
    expected_summary = {
        "lifecycles": len(trades),
        "closed": actual["closed"],
        "wins": outcome["win"],
        "losses": outcome["loss"],
        "flat": outcome["flat"],
        "unfilled": actual["unfilled"],
        "right_censored": actual["right_censored"],
        "excluded": actual["excluded"],
    }
    for key, value in expected_summary.items():
        if summary.get(key) != value:
            raise ValueError("quant summary does not match trade rows")
    denominator = outcome["win"] + outcome["loss"]
    rate = outcome["win"] / denominator if denominator else None
    if summary.get("win_rate") != rate:
        raise ValueError("quant win rate denominator is invalid")
    trades.sort(key=lambda row: (str(row["profile"]), str(row["lifecycle_id"])))
    return trades


def _validate_trade_row(
    raw: Mapping[str, object], *, source: Mapping[str, object], start_us: int, end_us: int,
) -> None:
    """Validate a quant row without reflecting untrusted values in errors."""

    invalid = ValueError("quant trade row is invalid")
    if (
        raw.get("profile") != source.get("profile")
        or raw.get("symbol") != source.get("symbol")
        or raw.get("direction") != source.get("direction")
    ):
        raise invalid
    try:
        signal_us = _parse_instant(source.get("effective_at"), "effective_at")
    except ValueError as exc:  # pragma: no cover - source was already validated
        raise invalid from exc
    if (
        raw.get("signal_at_us") != signal_us
        or signal_us < start_us - _MINUTE_US
        or signal_us >= end_us
    ):
        raise invalid
    status = raw.get("status")
    if status not in {"closed", "unfilled", "right_censored", "excluded"}:
        raise invalid
    reason = raw.get("exclusion_reason")
    if reason is not None and (
        not isinstance(reason, str) or _SAFE_REASON.fullmatch(reason) is None
    ):
        raise invalid
    try:
        remaining = _trade_number(raw.get("remaining_fraction"))
    except ValueError as exc:
        raise invalid from exc
    if remaining < 0 or remaining > 1:
        raise invalid
    fills = raw.get("tp_fills")
    if not isinstance(fills, list):
        raise invalid
    source_targets = source.get("tps")
    if not isinstance(source_targets, list):  # pragma: no cover - source validation
        raise invalid
    validated_fills: list[tuple[float, float, int]] = []
    for fill in fills:
        if not isinstance(fill, dict) or set(fill) != {
            "target", "fraction", "exit_price", "bucket_ts",
        }:
            raise invalid
        try:
            target = _trade_number(fill.get("target"))
            fraction = _trade_number(fill.get("fraction"))
            exit_price = _trade_number(fill.get("exit_price"))
        except ValueError as exc:
            raise invalid from exc
        bucket = fill.get("bucket_ts")
        if (
            target <= 0
            or fraction <= 0
            or fraction > 1
            or exit_price <= 0
            or not _valid_bucket(bucket, start_us=start_us, end_us=end_us)
            or not any(_numbers_close(target, candidate) for candidate in source_targets)
        ):
            raise invalid
        validated_fills.append((target, fraction, int(bucket)))
    if len({target for target, _, _ in validated_fills}) != len(validated_fills):
        raise invalid
    if [bucket for _, _, bucket in validated_fills] != sorted(
        bucket for _, _, bucket in validated_fills
    ):
        raise invalid
    filled_fraction = sum(fraction for _, fraction, _ in validated_fills)
    if filled_fraction > 1 + 1e-9:
        raise invalid

    entry_bucket = raw.get("entry_bucket_ts")
    entry_price = raw.get("entry_price")
    exit_bucket = raw.get("exit_bucket_ts")
    exit_reason = raw.get("exit_reason")
    gross = raw.get("gross_return_pct")
    fees = raw.get("fees_pct")
    slippage = raw.get("slippage_pct")
    net = raw.get("net_return_pct")
    outcome = raw.get("outcome")

    no_execution = (
        entry_bucket is None
        and entry_price is None
        and exit_bucket is None
        and exit_reason is None
        and not validated_fills
        and _numbers_close(remaining, 1.0)
        and gross is None
        and fees is None
        and slippage is None
        and net is None
    )
    if status in {"unfilled", "excluded"}:
        if outcome is not None or reason is None or not no_execution:
            raise invalid
        return
    if status == "right_censored" and no_execution:
        if outcome is not None or reason is None:
            raise invalid
        return

    if (
        not _valid_bucket(entry_bucket, start_us=start_us, end_us=end_us)
        or int(entry_bucket) < _next_minute(signal_us)
    ):
        raise invalid
    try:
        entry_price_number = _trade_number(entry_price)
        gross_number = _trade_number(gross)
        fees_number = _trade_number(fees)
        slippage_number = _trade_number(slippage)
        net_number = _trade_number(net)
    except ValueError as exc:
        raise invalid from exc
    if entry_price_number <= 0 or fees_number < 0 or slippage_number < 0:
        raise invalid
    if not _numbers_close(net_number, gross_number - fees_number):
        raise invalid
    if any(bucket <= int(entry_bucket) for _, _, bucket in validated_fills):
        raise invalid

    if status == "right_censored":
        if (
            outcome is not None
            or reason is None
            or exit_bucket is not None
            or exit_reason is not None
            or remaining <= 0
            or not _numbers_close(remaining + filled_fraction, 1.0)
        ):
            raise invalid
        return

    if (
        status != "closed"
        or reason is not None
        or outcome not in {"win", "loss", "flat"}
        or exit_reason not in {"take_profit", "stop_loss"}
        or not _valid_bucket(exit_bucket, start_us=start_us, end_us=end_us)
        or int(exit_bucket) < int(entry_bucket)
        or not _numbers_close(remaining, 0.0)
    ):
        raise invalid
    if validated_fills and validated_fills[-1][2] > int(exit_bucket):
        raise invalid
    if exit_reason == "take_profit" and (
        not validated_fills
        or not _numbers_close(filled_fraction, 1.0)
        or validated_fills[-1][2] != int(exit_bucket)
    ):
        raise invalid
    if exit_reason == "stop_loss" and filled_fraction >= 1 - 1e-9:
        raise invalid
    if (
        (outcome == "win" and net_number <= 1e-12)
        or (outcome == "loss" and net_number >= -1e-12)
        or (outcome == "flat" and abs(net_number) > 1e-12)
    ):
        raise invalid


def _market_validation(core: Mapping[str, object]) -> dict[str, object]:
    window = core["market_window"]
    assert isinstance(window, dict) and isinstance(window["symbols"], dict)
    total = sum(int(value["bar_count"]) for value in window["symbols"].values())
    return {
        "interval": "1m",
        "symbols": window["symbols"],
        "bar_count": total,
        "validation": {
            "gap_count": 0,
            "duplicate_count": 0,
            "misaligned_count": 0,
            "invalid_count": 0,
        },
    }


def _build_report(
    *, selected: Sequence[Mapping[str, object]], trades: Sequence[Mapping[str, object]],
    fee_bps: float, slippage_bps: float, ttl: int, start_us: int, end_us: int,
    input_profile_counts: Mapping[str, int],
    curation_exclusions: Mapping[str, Mapping[str, int]],
) -> dict[str, object]:
    selected_counts = Counter(str(row["profile"]) for row in selected)
    profile_rows: dict[str, dict[str, object]] = {}
    for profile in sorted(_PROFILE_COUNTS):
        rows = [row for row in trades if row.get("profile") == profile]
        closed = [row for row in rows if row.get("status") == "closed"]
        wins = [row for row in closed if row.get("outcome") == "win"]
        losses = [row for row in closed if row.get("outcome") == "loss"]
        flats = [row for row in closed if row.get("outcome") == "flat"]
        returns = [float(row["net_return_pct"]) for row in closed]
        positive = sum(value for value in returns if value > 0)
        negative = abs(sum(value for value in returns if value < 0))
        holding = [
            (int(row["exit_bucket_ts"]) - int(row["entry_bucket_ts"])) / _MINUTE_US
            for row in closed
            if isinstance(row.get("entry_bucket_ts"), int)
            and isinstance(row.get("exit_bucket_ts"), int)
        ]
        denominator = len(wins) + len(losses)
        exclusion_reasons = dict(sorted(curation_exclusions.get(profile, {}).items()))
        curated_excluded = sum(exclusion_reasons.values())
        unfilled = sum(row.get("status") == "unfilled" for row in rows)
        right_censored = sum(row.get("status") == "right_censored" for row in rows)
        quant_excluded = sum(row.get("status") == "excluded" for row in rows)
        input_rows = int(input_profile_counts.get(profile, 0))
        if (
            input_rows != curated_excluded + selected_counts[profile]
            or selected_counts[profile] != len(rows)
            or len(rows) != len(closed) + unfilled + right_censored + quant_excluded
        ):
            raise ValueError("backtest report conservation failed")
        profile_rows[profile] = {
            "input_rows": input_rows,
            "curation_excluded": curated_excluded,
            "curation_excluded_by_reason": exclusion_reasons,
            "selected": selected_counts[profile],
            "simulated": len(rows),
            "closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "flat": len(flats),
            "win_rate_denominator": denominator,
            "win_rate": len(wins) / denominator if denominator else None,
            "mean_net_return_pct": statistics.fmean(returns) if returns else None,
            "median_net_return_pct": statistics.median(returns) if returns else None,
            "profit_factor": positive / negative if negative else None,
            "take_profit_exits": sum(row.get("exit_reason") == "take_profit" for row in closed),
            "stop_loss_exits": sum(row.get("exit_reason") == "stop_loss" for row in closed),
            "mean_holding_minutes": statistics.fmean(holding) if holding else None,
            "median_holding_minutes": statistics.median(holding) if holding else None,
            "fees_pct_total": sum(float(row.get("fees_pct") or 0.0) for row in rows),
            "slippage_bps_assumption": slippage_bps,
            "unfilled": unfilled,
            "right_censored": right_censored,
            "quant_excluded": quant_excluded,
        }
    conservation = {
        "input_rows": sum(int(row["input_rows"]) for row in profile_rows.values()),
        "curation_excluded": sum(
            int(row["curation_excluded"]) for row in profile_rows.values()
        ),
        "simulated": sum(int(row["simulated"]) for row in profile_rows.values()),
        "closed": sum(int(row["closed"]) for row in profile_rows.values()),
        "unfilled": sum(int(row["unfilled"]) for row in profile_rows.values()),
        "right_censored": sum(
            int(row["right_censored"]) for row in profile_rows.values()
        ),
        "quant_excluded": sum(
            int(row["quant_excluded"]) for row in profile_rows.values()
        ),
    }
    if (
        conservation["input_rows"]
        != conservation["curation_excluded"] + conservation["simulated"]
        or conservation["simulated"]
        != conservation["closed"]
        + conservation["unfilled"]
        + conservation["right_censored"]
        + conservation["quant_excluded"]
    ):
        raise ValueError("backtest report conservation failed")
    return {
        "report_kind": _SCHEMA_VERSION,
        "market_window": {"start_us": start_us, "end_exclusive_us": end_us},
        "methodology": {
            "bar_interval": "1m",
            "entry": "next_bar_or_limit_touch",
            "intrabar_conflict": "stop_first",
            "fee_bps": fee_bps,
            "slippage_bps": slippage_bps,
            "funding": "unmodeled",
            "ttl_minutes": ttl,
            "entry_wait_anchor": "effective_at",
            "deadline_bar_policy": "full_bar_must_close_by_deadline",
        },
        "conservation": conservation,
        "selected_lifecycle_count": len(selected),
        "profiles": profile_rows,
    }


def _render_report(report: Mapping[str, object]) -> str:
    lines = [
        "# Discord blogger conservative backtest", "",
        "Funding: unmodeled", "",
        "| Profile | Input | Curated excluded | Selected | Closed | Unfilled | Right-censored | Quant excluded | Win | Loss | Flat | Win rate | Mean net % | Median net % | PF | TP | SL |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    profiles = report["profiles"]
    assert isinstance(profiles, dict)
    for profile, values in sorted(profiles.items()):
        assert isinstance(values, dict)
        lines.append(
            f"| {profile} | {values['input_rows']} | {values['curation_excluded']} | "
            f"{values['selected']} | {values['closed']} | {values['unfilled']} | "
            f"{values['right_censored']} | {values['quant_excluded']} | {values['wins']} | "
            f"{values['losses']} | {values['flat']} | {values['win_rate']} | "
            f"{values['mean_net_return_pct']} | {values['median_net_return_pct']} | "
            f"{values['profit_factor']} | {values['take_profit_exits']} | {values['stop_loss_exits']} |"
        )
    lines.extend(["", "## Curation exclusions", ""])
    for profile, values in sorted(profiles.items()):
        assert isinstance(values, dict)
        reasons = values["curation_excluded_by_reason"]
        assert isinstance(reasons, dict)
        summary = ", ".join(f"{reason}={count}" for reason, count in sorted(reasons.items()))
        lines.append(f"- {profile}: {summary or 'none'}")
    lines.extend(["", "Assumptions: next-bar execution, limit touch only, stop-first conflicts; funding is unmodeled.", ""])
    return "\n".join(lines)


def _publish(output_dir: Path, artifacts: Mapping[str, bytes]) -> None:
    if output_dir == Path(output_dir.anchor) or not output_dir.name:
        raise ValueError("backtest output directory is unsafe")
    root = Path(output_dir.anchor)
    relative = _root_relative(output_dir)
    parent_relative = Path(*relative.parts[:-1])
    anchor = _RootAnchor.open(root)
    published = False
    try:
        with anchor.directory(parent_relative, create=True) as parent:
            parent.verify()
            stage_name, stage_fd, stage_identity = _create_stage(parent.fd, relative.name)
            try:
                commitments = {
                    name: _write_at(stage_fd, name, content)
                    for name, content in sorted(artifacts.items())
                }
                _fsync_stage(stage_fd)
                parent.verify()
                _verify_stage(
                    parent_fd=parent.fd,
                    stage_name=stage_name,
                    stage_fd=stage_fd,
                    stage_identity=stage_identity,
                    commitments=commitments,
                )
                _rename_directory_noreplace_at(stage_name, relative.name, parent.fd)
                published = True
                try:
                    _verify_stage(
                        parent_fd=parent.fd,
                        stage_name=relative.name,
                        stage_fd=stage_fd,
                        stage_identity=stage_identity,
                        commitments=commitments,
                    )
                    os.fsync(parent.fd)
                    parent.verify()
                except Exception as verification_error:
                    try:
                        _quarantine_name(parent.fd, relative.name, relative.name)
                    except Exception as quarantine_error:
                        quarantine_error.add_note(
                            "post-publication verification also failed"
                        )
                        raise quarantine_error from verification_error
                    raise
            except Exception:
                if not published:
                    _remove_stage(parent.fd, stage_name, stage_fd, stage_identity)
                raise
            finally:
                os.close(stage_fd)
    finally:
        anchor.close()


def _scan_sensitive(value: object, *, depth: int = 0) -> None:
    if depth > 24:
        raise ValueError("backtest structure exceeds safe depth")
    if isinstance(value, str):
        if _SENSITIVE_TEXT.search(value):
            raise ValueError("backtest data contains a prohibited sensitive string")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _SENSITIVE_KEYS:
                raise ValueError("backtest data contains a prohibited sensitive field")
            _scan_sensitive(child, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _scan_sensitive(child, depth=depth + 1)


def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or _HEX_SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} SHA-256 is invalid")
    return value


def _finite(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) > 0


def _trade_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("quant trade row is invalid")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("quant trade row is invalid")
    return number


def _numbers_close(left: object, right: object) -> bool:
    try:
        left_number = _trade_number(left)
        right_number = _trade_number(right)
    except ValueError:
        return False
    return math.isclose(left_number, right_number, rel_tol=1e-9, abs_tol=1e-9)


def _valid_bucket(value: object, *, start_us: int, end_us: int) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and start_us <= value < end_us
        and value % _MINUTE_US == 0
    )


def _cost_bps(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number >= 10_000:
        raise ValueError(f"{field} must be in [0, 10000)")
    return number


def _parse_instant(value: object, field: str) -> int:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a timezone-aware instant")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a timezone-aware instant") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware instant")
    return int(parsed.astimezone(UTC).timestamp() * 1_000_000)


def _next_minute(value: int) -> int:
    return value - value % _MINUTE_US + _MINUTE_US


def _instant_text(value: int) -> str:
    return datetime.fromtimestamp(value / 1_000_000, tz=UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("backtest lifecycle write made no progress")
        offset += written


def _write_private_file(path: Path, content: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        _write_all(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
