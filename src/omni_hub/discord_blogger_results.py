"""Redacted reports and atomic publication for blogger trade-event decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Mapping, Sequence

from .discord_reference_sidecar import _RootAnchor
from .discord_sharding import _relative_path, _rename_directory_noreplace_at
from .discord_trade_events import (
    PARSER_IMPLEMENTATION_SHA256,
    PROFILE_CONFIG_SHA256,
    PROFILE_CHANNELS,
    MessageDecision,
    TradeEvent,
    link_trade_lifecycles,
)


_STAGE_DIRECTORY_MODE = 0o700
_STAGE_FILE_MODE = 0o600
_REQUIRED_CLOSURE_INPUTS = frozenset({"merge_audit", "head_catchup", "census"})


def build_latest_calls_report(*, decisions: Sequence[MessageDecision], asof: datetime) -> dict[str, object]:
    if asof.tzinfo is None or asof.utcoffset() is None:
        raise ValueError("Discord report asof must be timezone-aware")
    eligible = tuple(
        decision for decision in decisions if _parse_time(decision.effective_at) <= asof
    )
    event_by_id = {event.event_id: event for decision in eligible for event in decision.events}
    decision_by_event = {event.event_id: decision for decision in eligible for event in decision.events}
    calls: list[dict[str, object]] = []
    unresolved_lifecycle_count = 0
    for lifecycle in link_trade_lifecycles(eligible):
        events = [event_by_id[event_id] for event_id in lifecycle.event_ids]
        if lifecycle.unresolved_event_ids:
            unresolved_lifecycle_count += 1
        if not events or _parse_time(events[0].effective_at) > asof or lifecycle.status != "open" or lifecycle.unresolved_event_ids:
            continue
        values = _call_values(events)
        latest = events[-1]
        decision = decision_by_event[latest.event_id]
        calls.append({
            "blogger": lifecycle.blogger,
            "profile": lifecycle.profile,
            "symbol": lifecycle.symbol,
            "direction": lifecycle.direction,
            "entry": values["entry"],
            "entry_low": values["entry_low"],
            "entry_high": values["entry_high"],
            "tp": values["tp"],
            "tps": values["tps"],
            "sl": values["sl"],
            "status": lifecycle.status,
            "effective_at": latest.effective_at,
            "message_id": latest.message_id,
            "author_id": decision.author_id,
            "evidence_ref": decision.evidence_ref,
            "lifecycle_id": lifecycle.lifecycle_id,
        })
    calls.sort(key=lambda call: (str(call["profile"]), str(call["symbol"]), str(call["lifecycle_id"])))
    return {"report_kind": "discord-latest-calls-v1", "asof": asof.isoformat(), "calls": calls, "unresolved_lifecycle_count": unresolved_lifecycle_count}


def publish_blogger_event_artifacts(
    *,
    workspace: Path,
    output_dir: Path,
    source_manifest: Mapping[str, object],
    closure_audit_path: Path,
    closure_audit_bytes: bytes,
) -> dict[str, object]:
    """Publish a complete derivative directory once; no existing directory is overwritten."""

    workspace_root = Path(workspace)
    output_relative = _relative_path(output_dir, "blogger output")
    parent_relative = Path(*output_relative.parts[:-1])
    decisions = source_manifest.get("decisions")
    lifecycles = source_manifest.get("lifecycles")
    report = source_manifest.get("latest_calls")
    provenance = source_manifest.get("provenance")
    if (
        not isinstance(decisions, list)
        or not isinstance(lifecycles, list)
        or not isinstance(report, dict)
        or not isinstance(provenance, dict)
    ):
        raise ValueError("Discord blogger source manifest is incomplete")
    cleaned_decisions = [_decision_row(value) for value in decisions]
    typed_decisions = tuple(_typed_decision(row) for row in cleaned_decisions)
    canonical_lifecycles = [
        lifecycle.to_dict() for lifecycle in link_trade_lifecycles(typed_decisions)
    ]
    if _canonical(lifecycles) != _canonical(canonical_lifecycles):
        raise ValueError("Discord blogger lifecycles differ from canonical decisions")
    _validate_provenance(
        provenance,
        cleaned_decisions,
        report,
        closure_audit_path=closure_audit_path,
        closure_audit_bytes=closure_audit_bytes,
    )
    asof = _parse_time(str(provenance["asof"]))
    canonical_report = build_latest_calls_report(
        decisions=typed_decisions,
        asof=asof,
    )
    if _canonical(report) != _canonical(canonical_report):
        raise ValueError("Discord blogger latest calls differ from canonical decisions")
    events = _event_rows(cleaned_decisions, canonical_lifecycles)
    _reject_sensitive(source_manifest)
    published = False
    anchor = _RootAnchor.open(workspace_root)
    try:
        with anchor.directory(parent_relative, create=True) as parent:
            parent.verify()
            stage_name, stage_fd, stage_identity = _create_stage(
                parent.fd, output_relative.name
            )
            try:
                artifacts = {
                    "message-decisions.jsonl": b"".join(
                        _canonical(row) + b"\n" for row in cleaned_decisions
                    ),
                    "trade-events.jsonl": b"".join(_canonical(row) + b"\n" for row in events),
                    "trade-lifecycles.jsonl": b"".join(
                        _canonical(row) + b"\n" for row in canonical_lifecycles
                    ),
                    "latest-calls.json": _canonical(canonical_report) + b"\n",
                    "latest-calls.md": _render_report(canonical_report).encode("utf-8"),
                }
                commitments: dict[str, _StagedFileCommitment] = {}
                for name, content in artifacts.items():
                    commitments[name] = _write_at(stage_fd, name, content)
                manifest = {
                    "artifact_kind": "discord-blogger-events-v1",
                    "provenance": provenance,
                    "decision_count": len(cleaned_decisions),
                    "event_count": len(events),
                    "lifecycle_count": len(canonical_lifecycles),
                    "latest_call_count": len(canonical_report.get("calls", [])),
                    "files": {
                        name: hashlib.sha256(content).hexdigest()
                        for name, content in sorted(artifacts.items())
                    },
                }
                manifest_content = _canonical(manifest) + b"\n"
                commitments["event-manifest.json"] = _write_at(
                    stage_fd, "event-manifest.json", manifest_content
                )
                _fsync_stage(stage_fd)
                parent.verify()
                _verify_stage(
                    parent_fd=parent.fd,
                    stage_name=stage_name,
                    stage_fd=stage_fd,
                    stage_identity=stage_identity,
                    commitments=commitments,
                )
                _rename_directory_noreplace_at(stage_name, output_relative.name, parent.fd)
                published = True
                try:
                    _verify_stage(
                        parent_fd=parent.fd,
                        stage_name=output_relative.name,
                        stage_fd=stage_fd,
                        stage_identity=stage_identity,
                        commitments=commitments,
                    )
                except Exception as verification_error:
                    try:
                        _quarantine_name(
                            parent.fd,
                            output_relative.name,
                            output_relative.name,
                        )
                    except Exception as quarantine_error:
                        quarantine_error.add_note(
                            f"post-publication verification also failed: {verification_error}"
                        )
                        raise quarantine_error from verification_error
                    raise
                os.fsync(parent.fd)
                parent.verify()
            except Exception:
                if not published:
                    _remove_stage(parent.fd, stage_name, stage_fd, stage_identity)
                raise
            finally:
                os.close(stage_fd)
    finally:
        anchor.close()
    return {
        "output_dir": output_relative.as_posix(),
        "manifest_path": (output_relative / "event-manifest.json").as_posix(),
        "decision_count": len(cleaned_decisions),
        "event_count": len(events),
    }


def _call_values(events: Sequence[TradeEvent]) -> dict[str, object]:
    values: dict[str, object] = {"entry": None, "entry_low": None, "entry_high": None, "tp": None, "tps": [], "sl": None}
    for event in events:
        for field in ("entry", "entry_low", "entry_high", "tp", "sl"):
            value = getattr(event, field)
            if value is not None:
                values[field] = value
        if event.tps:
            values["tps"] = list(event.tps)
    return values


def _decision_row(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Discord decision row is invalid")
    allowed = {"decision_id", "profile", "profile_version", "blogger", "message_id", "channel_id", "author_id", "snapshot_sha256", "effective_at", "classification", "exclusion_reason", "evidence_ref", "reply_message_id", "events"}
    if set(value) != allowed or not isinstance(value["events"], list):
        raise ValueError("Discord decision row is invalid")
    return dict(value)


def _typed_decision(row: Mapping[str, object]) -> MessageDecision:
    event_fields = {
        "event_id", "event_type", "profile", "message_id", "symbol", "direction",
        "effective_at", "entry", "entry_low", "entry_high", "tp", "tps", "sl",
        "evidence_ref",
    }
    typed_events: list[TradeEvent] = []
    raw_events = row["events"]
    assert isinstance(raw_events, list)
    for raw_event in raw_events:
        if (
            not isinstance(raw_event, dict)
            or set(raw_event) != event_fields
            or not isinstance(raw_event["tps"], list)
        ):
            raise ValueError("Discord event row is invalid")
        values = dict(raw_event)
        values["tps"] = tuple(raw_event["tps"])
        try:
            event = TradeEvent(**values)
        except TypeError as exc:
            raise ValueError("Discord event row is invalid") from exc
        if event.to_dict() != raw_event:
            raise ValueError("Discord event row is invalid")
        typed_events.append(event)
    values = dict(row)
    values["events"] = tuple(typed_events)
    try:
        decision = MessageDecision(**values)
    except TypeError as exc:
        raise ValueError("Discord decision row is invalid") from exc
    if decision.to_dict() != row:
        raise ValueError("Discord decision row is invalid")
    return decision


def _event_rows(decisions: Sequence[dict[str, object]], lifecycles: Sequence[object]) -> list[dict[str, object]]:
    resolved: dict[str, str] = {}
    unresolved: set[str] = set()
    for lifecycle in lifecycles:
        if not isinstance(lifecycle, dict):
            raise ValueError("Discord lifecycle row is invalid")
        lifecycle_id = lifecycle.get("lifecycle_id")
        event_ids = lifecycle.get("event_ids")
        unresolved_ids = lifecycle.get("unresolved_event_ids")
        if not isinstance(lifecycle_id, str) or not isinstance(event_ids, list) or not isinstance(unresolved_ids, list):
            raise ValueError("Discord lifecycle row is invalid")
        for event_id in event_ids:
            if not isinstance(event_id, str) or event_id in resolved:
                raise ValueError("Discord lifecycle event binding is ambiguous")
            resolved[event_id] = lifecycle_id
        for event_id in unresolved_ids:
            if not isinstance(event_id, str):
                raise ValueError("Discord lifecycle unresolved binding is invalid")
            unresolved.add(event_id)
    rows: list[dict[str, object]] = []
    for decision in decisions:
        for event in decision["events"]:
            if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
                raise ValueError("Discord event row is invalid")
            row = dict(event)
            event_id = event["event_id"]
            row["lifecycle_id"] = None if event_id in unresolved else resolved.get(event_id)
            row["link_status"] = "unresolved" if event_id in unresolved else ("resolved" if event_id in resolved else "unresolved")
            rows.append(row)
    return rows


def _validate_provenance(
    provenance: Mapping[str, object],
    decisions: Sequence[dict[str, object]],
    report: Mapping[str, object],
    *,
    closure_audit_path: Path,
    closure_audit_bytes: bytes,
) -> None:
    required = {
        "closure_audit", "asof", "parser_implementation_sha256", "profiles",
        "corpus_message_count", "corpus_commitment",
    }
    if set(provenance) != required:
        raise ValueError("Discord blogger provenance fields are invalid")
    closure = provenance["closure_audit"]
    if not isinstance(closure, dict) or set(closure) != {
        "path", "sha256", "input_file_sha256"
    }:
        raise ValueError("Discord blogger closure provenance is invalid")
    try:
        bindings = validated_closure_input_bindings(closure["input_file_sha256"])
    except ValueError as exc:
        raise ValueError("Discord blogger closure provenance is invalid") from exc
    closure_relative = _relative_path(closure_audit_path, "closure audit")
    if not isinstance(closure_audit_bytes, bytes):
        raise ValueError("Discord blogger frozen closure provenance is invalid")
    try:
        frozen_closure = json.loads(closure_audit_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Discord blogger frozen closure provenance is invalid") from exc
    if not isinstance(frozen_closure, dict):
        raise ValueError("Discord blogger frozen closure provenance is invalid")
    try:
        frozen_bindings = validated_closure_input_bindings(
            frozen_closure.get("input_file_sha256")
        )
    except ValueError as exc:
        raise ValueError("Discord blogger closure provenance is invalid") from exc
    if (
        closure.get("path") != closure_relative.as_posix()
        or closure.get("sha256") != hashlib.sha256(closure_audit_bytes).hexdigest()
        or frozen_closure.get("audit_kind") != "discord-parent-family-closure-v1"
        or frozen_bindings != bindings
    ):
        raise ValueError("Discord blogger closure provenance is invalid")
    if (
        provenance["parser_implementation_sha256"] != PARSER_IMPLEMENTATION_SHA256
        or not isinstance(provenance["asof"], str)
        or report.get("asof") != provenance["asof"]
        or not isinstance(provenance["corpus_message_count"], int)
        or provenance["corpus_message_count"] != len(decisions)
        or not _sha256(provenance["corpus_commitment"])
    ):
        raise ValueError("Discord blogger provenance commitment is invalid")
    _parse_time(provenance["asof"])
    rows = sorted(
        (
            decision["message_id"], decision["channel_id"], decision["author_id"],
            decision["snapshot_sha256"], decision["decision_id"],
        )
        for decision in decisions
    )
    expected_commitment = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if provenance["corpus_commitment"] != expected_commitment:
        raise ValueError("Discord blogger corpus commitment is invalid")
    profiles = provenance["profiles"]
    if not isinstance(profiles, list):
        raise ValueError("Discord blogger profile provenance is invalid")
    actual_profiles = {str(decision["profile"]) for decision in decisions}
    seen: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, dict) or set(profile) != {
            "profile", "version", "channel_id", "config_sha256"
        }:
            raise ValueError("Discord blogger profile provenance is invalid")
        name = profile["profile"]
        if (
            not isinstance(name, str)
            or name in seen
            or profile["version"] != "v1"
            or PROFILE_CHANNELS.get(name) != profile["channel_id"]
            or PROFILE_CONFIG_SHA256.get(name) != profile["config_sha256"]
        ):
            raise ValueError("Discord blogger profile provenance is invalid")
        seen.add(name)
    if not actual_profiles <= seen:
        raise ValueError("Discord blogger profile provenance does not cover the corpus")


def _sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def validated_closure_input_bindings(value: object) -> dict[str, str]:
    if (
        not isinstance(value, dict)
        or not all(isinstance(key, str) for key in value)
        or not _REQUIRED_CLOSURE_INPUTS <= set(value)
    ):
        raise ValueError("Discord blogger closure input bindings are invalid")
    normalized: dict[str, str] = {}
    for key in sorted(value):
        binding = value[key]
        if (
            not isinstance(key, str)
            or not key
            or not key.isascii()
            or not key[0].isalpha()
            or not all(character.islower() or character.isdigit() or character == "_" for character in key)
            or not _sha256(binding)
        ):
            raise ValueError("Discord blogger closure input bindings are invalid")
        normalized[key] = binding
    return normalized


def _reject_sensitive(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.casefold() in {"content", "token", "authorization", "logical_key", "url", "raw_url", "signed_url"}:
                raise ValueError("Discord derivative contains a sensitive field")
            _reject_sensitive(item)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive(item)
    elif isinstance(value, str) and ("http://" in value or "https://" in value):
        raise ValueError("Discord derivative contains a URL")


def _render_report(report: Mapping[str, object]) -> str:
    lines = ["# Latest blogger calls", "", f"As of: {report['asof']}", "", "| Blogger | Symbol | Direction | Entry | TP | SL | Status | UTC | Message | Evidence |", "| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |"]
    calls = report.get("calls", [])
    if not isinstance(calls, list):
        raise ValueError("Discord latest calls report is invalid")
    for call in calls:
        if not isinstance(call, dict):
            raise ValueError("Discord latest call is invalid")
        lines.append("| {blogger} | {symbol} | {direction} | {entry} | {tp} | {sl} | {status} | {effective_at} | {message_id} | {evidence_ref} |".format(**call))
    return "\n".join(lines) + "\n"


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, slots=True)
class _StagedFileCommitment:
    identity: tuple[int, int]
    size: int
    sha256: str


def _create_stage(parent_fd: int, output_name: str) -> tuple[str, int, tuple[int, int]]:
    for _ in range(32):
        name = f".{output_name}.stage-{secrets.token_hex(12)}"
        try:
            os.mkdir(name, _STAGE_DIRECTORY_MODE, dir_fd=parent_fd)
        except FileExistsError:
            continue
        descriptor: int | None = None
        named_identity: tuple[int, int] | None = None
        ownership_confirmed = False
        try:
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(named.st_mode):
                raise ValueError("Discord blogger staging directory identity changed")
            named_identity = (named.st_dev, named.st_ino)
            if stat.S_IMODE(named.st_mode) != _STAGE_DIRECTORY_MODE:
                raise ValueError("Discord blogger staging directory mode differs")
            descriptor = _open_stage(parent_fd, name)
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != named_identity:
                raise ValueError("Discord blogger staging directory identity changed")
            if stat.S_IMODE(opened.st_mode) != _STAGE_DIRECTORY_MODE:
                raise ValueError("Discord blogger staging directory mode differs")
            ownership_confirmed = True
            os.fsync(parent_fd)
        except BaseException as exc:
            cleanup_error: BaseException | None = None
            try:
                if descriptor is not None and ownership_confirmed and named_identity is not None:
                    _remove_stage(parent_fd, name, descriptor, named_identity)
                elif named_identity is not None:
                    _remove_stage_name_if_owned(parent_fd, name, named_identity)
                else:
                    _quarantine_name(parent_fd, name, output_name)
            except BaseException as cleanup_exc:
                cleanup_error = cleanup_exc
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            if cleanup_error is not None:
                cleanup_error.add_note(f"stage initialization also failed: {exc}")
                raise cleanup_error from exc
            raise
        assert descriptor is not None and named_identity is not None
        return name, descriptor, named_identity
    raise FileExistsError("Discord blogger staging name collision")


def _open_stage(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise ValueError("Discord blogger staging directory cannot be opened safely") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("Discord blogger staging directory is unsafe")
    return descriptor


def _write_at(stage_fd: int, name: str, content: bytes) -> _StagedFileCommitment:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, _STAGE_FILE_MODE, dir_fd=stage_fd)
    except OSError as exc:
        raise OSError(exc.errno, "Discord blogger staged file cannot be opened safely", name) from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Discord blogger staged file is unsafe")
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("Discord blogger staged file write made no progress")
            offset += written
        os.fsync(descriptor)
        written_stat = os.fstat(descriptor)
        if not stat.S_ISREG(written_stat.st_mode) or written_stat.st_size != len(content):
            raise ValueError("Discord blogger staged file commitment changed")
        if stat.S_IMODE(written_stat.st_mode) != _STAGE_FILE_MODE:
            raise ValueError("Discord blogger staging file mode differs")
        return _StagedFileCommitment(
            identity=(written_stat.st_dev, written_stat.st_ino),
            size=written_stat.st_size,
            sha256=hashlib.sha256(content).hexdigest(),
        )
    finally:
        os.close(descriptor)


def _fsync_stage(stage_fd: int) -> None:
    os.fsync(stage_fd)


def _verify_stage(
    *,
    parent_fd: int,
    stage_name: str,
    stage_fd: int,
    stage_identity: tuple[int, int],
    commitments: Mapping[str, _StagedFileCommitment],
) -> None:
    opened_stage = os.fstat(stage_fd)
    if (
        not stat.S_ISDIR(opened_stage.st_mode)
        or (opened_stage.st_dev, opened_stage.st_ino) != stage_identity
    ):
        raise ValueError("Discord blogger staging directory identity changed")
    if stat.S_IMODE(opened_stage.st_mode) != _STAGE_DIRECTORY_MODE:
        raise ValueError("Discord blogger staging directory mode differs")
    _verify_stage_name(parent_fd, stage_name, stage_identity)
    names = set(os.listdir(stage_fd))
    if names != set(commitments):
        raise ValueError("Discord blogger staging inventory differs")
    for name, commitment in commitments.items():
        named = os.stat(name, dir_fd=stage_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(named.st_mode)
            or (named.st_dev, named.st_ino) != commitment.identity
            or named.st_size != commitment.size
        ):
            raise ValueError("Discord blogger staging inventory differs")
        if stat.S_IMODE(named.st_mode) != _STAGE_FILE_MODE:
            raise ValueError("Discord blogger staging file mode differs")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(name, flags, dir_fd=stage_fd)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != commitment.identity
                or opened.st_size != commitment.size
            ):
                raise ValueError("Discord blogger staging inventory differs")
            if stat.S_IMODE(opened.st_mode) != _STAGE_FILE_MODE:
                raise ValueError("Discord blogger staging file mode differs")
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
            final = os.fstat(descriptor)
            if (
                (final.st_dev, final.st_ino) != commitment.identity
                or final.st_size != commitment.size
                or size != commitment.size
                or digest.hexdigest() != commitment.sha256
            ):
                raise ValueError("Discord blogger staging inventory differs")
            if stat.S_IMODE(final.st_mode) != _STAGE_FILE_MODE:
                raise ValueError("Discord blogger staging file mode differs")
        finally:
            os.close(descriptor)
    _verify_stage_name(parent_fd, stage_name, stage_identity)


def _verify_stage_name(
    parent_fd: int,
    stage_name: str,
    stage_identity: tuple[int, int],
) -> None:
    try:
        named = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise ValueError("Discord blogger staging directory identity changed") from exc
    if (
        not stat.S_ISDIR(named.st_mode)
        or (named.st_dev, named.st_ino) != stage_identity
    ):
        raise ValueError("Discord blogger staging directory identity changed")
    if stat.S_IMODE(named.st_mode) != _STAGE_DIRECTORY_MODE:
        raise ValueError("Discord blogger staging directory mode differs")


def _quarantine_name(parent_fd: int, name: str, output_name: str) -> str:
    """Move an untrusted named entry aside without following or deleting it."""

    for _ in range(32):
        quarantine = f".{output_name}.quarantine-{secrets.token_hex(12)}"
        try:
            _rename_directory_noreplace_at(name, quarantine, parent_fd)
        except FileExistsError:
            continue
        os.fsync(parent_fd)
        return quarantine
    raise FileExistsError("Discord blogger quarantine name collision")


def _remove_stage(
    parent_fd: int,
    name: str,
    stage_fd: int,
    stage_identity: tuple[int, int],
) -> None:
    errors: list[BaseException] = []
    try:
        for child in os.listdir(stage_fd):
            mode = os.stat(child, dir_fd=stage_fd, follow_symlinks=False).st_mode
            if not stat.S_ISREG(mode):
                raise ValueError("Discord blogger staging entry is unsafe")
            os.unlink(child, dir_fd=stage_fd)
    except BaseException as exc:
        errors.append(exc)
    try:
        os.fsync(stage_fd)
    except BaseException as exc:
        errors.append(exc)
    try:
        _remove_stage_name_if_owned(parent_fd, name, stage_identity)
    except BaseException as exc:
        errors.append(exc)
    if errors:
        for secondary in errors[1:]:
            errors[0].add_note(f"additional stage cleanup failure: {secondary}")
        raise errors[0]


def _remove_stage_name_if_owned(
    parent_fd: int,
    name: str,
    stage_identity: tuple[int, int] | None,
) -> None:
    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        stage_identity is None
        or not stat.S_ISDIR(named.st_mode)
        or (named.st_dev, named.st_ino) != stage_identity
    ):
        raise ValueError("Discord blogger staging directory identity changed")
    os.rmdir(name, dir_fd=parent_fd)
    os.fsync(parent_fd)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
