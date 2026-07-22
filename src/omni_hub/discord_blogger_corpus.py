"""Read a bounded blogger corpus from published Discord evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import stat
from typing import Iterator, Sequence

from .discord_reference_sidecar import _RootAnchor
from .discord_sharding import _read_regular_file_bytes, canonical_json_sha256


@dataclass(frozen=True, slots=True)
class BloggerMessage:
    message_id: str
    channel_id: str
    author_id: str | None
    timestamp: str
    edited_timestamp: str | None
    content: str
    reply_message_id: str | None
    snapshot_ref: str
    snapshot_sha256: str
    media_occurrence_refs: tuple[str, ...]


def authorized_blogger_message_target_ids(
    merge: dict[str, object],
    *,
    closure: dict[str, object] | None = None,
    head: dict[str, object] | None = None,
) -> tuple[str, ...]:
    """Return the exact closure target set for the unique authorized corpus."""

    static = _id_set(merge.get("static_target_ids"), "merge static targets")
    message_bearing = _id_set(
        merge.get("message_bearing_static_target_ids"),
        "merge message-bearing static targets",
    )
    if not message_bearing <= static:
        raise ValueError("Discord message-bearing targets are outside static scope")
    discovered = merge.get("discovered_threads")
    if not isinstance(discovered, list):
        raise ValueError("Discord discovered Threads are invalid")
    discovered_ids: set[str] = set()
    for value in discovered:
        if not isinstance(value, dict):
            raise ValueError("Discord discovered Thread is invalid")
        thread_id = value.get("id")
        parent_id = value.get("parent_id")
        owner_index = value.get("owner_index")
        if (
            not _snowflake(thread_id)
            or not _snowflake(parent_id)
            or parent_id not in static
            or isinstance(owner_index, bool)
            or not isinstance(owner_index, int)
            or owner_index <= 0
            or thread_id in discovered_ids
        ):
            raise ValueError("Discord discovered Thread is invalid")
        discovered_ids.add(thread_id)
    required = _id_set(
        merge.get("required_head_catchup_target_ids"),
        "merge required head catch-up targets",
    )
    if required != message_bearing | discovered_ids:
        raise ValueError("Discord authorized message target set is inconsistent")
    if (closure is None) is not (head is None):
        raise ValueError("Discord closure-aware target scope is incomplete")
    if closure is not None and head is not None:
        census_delta = closure.get("census_delta")
        head_delta = closure.get("head_catchup_delta")
        if not isinstance(census_delta, dict) or not isinstance(head_delta, dict):
            raise ValueError("Discord closure Thread delta is invalid")
        for container, field in (
            (census_delta, "missing_from_merge"),
            (census_delta, "missing_from_census"),
            (head_delta, "new_thread_ids"),
            (head_delta, "new_thread_target_ids"),
        ):
            if _id_set(container.get(field), f"closure {field}"):
                raise ValueError(
                    "Discord closure contains new Threads outside the frozen merge scope"
                )
        head_rows = head.get("targets")
        if not isinstance(head_rows, list):
            raise ValueError("Discord head catch-up targets are invalid")
        head_ids: list[str] = []
        for row in head_rows:
            if not isinstance(row, dict) or not _snowflake(row.get("id")):
                raise ValueError("Discord head catch-up target is invalid")
            head_ids.append(row["id"])
        if len(head_ids) != len(set(head_ids)) or set(head_ids) != required:
            raise ValueError(
                "Discord head catch-up targets differ from the frozen merge scope"
            )
    return tuple(sorted(required, key=int))


def iter_verified_blogger_messages(
    *,
    export_root: Path,
    closure_audit_path: Path,
    target_ids: Sequence[str],
    expected_closure_sha256: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    scope: str = "static",
) -> Iterator[BloggerMessage]:
    """Yield baseline plus explicit closure IDs, with closure snapshots first."""

    root = _root(export_root)
    targets = _targets(target_ids)
    _check_range(start, end)
    closure_relative = _relative(closure_audit_path, "closure audit")
    closure_bytes, closure_sha = read_blogger_closure_bytes(
        export_root=root,
        closure_audit_path=closure_relative,
    )
    try:
        closure = json.loads(closure_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Discord closure audit is unreadable") from exc
    if expected_closure_sha256 is not None:
        if not _sha(expected_closure_sha256):
            raise ValueError("Discord closure audit hash commitment is invalid")
        if closure_sha != expected_closure_sha256:
            raise ValueError("Discord closure audit hash commitment changed")
    if not isinstance(closure, dict) or closure.get("audit_kind") != "discord-parent-family-closure-v1":
        raise ValueError("Discord closure audit kind is invalid")
    bindings = closure.get("input_file_sha256")
    canonical = closure.get("input_canonical_sha256")
    if not isinstance(bindings, dict) or not isinstance(canonical, dict):
        raise ValueError("Discord closure input bindings are invalid")
    capture_dir = closure_relative.parent
    namespace_dir = capture_dir.parent
    merge_path = namespace_dir / _relative("merge-audit.json", "merge audit")
    request_path = namespace_dir / _relative("merge-request.json", "merge request")
    head_path = capture_dir / _relative("head-catchup.json", "head catch-up")
    head, head_sha = _json_file(root, head_path, "head catch-up")
    merge, merge_sha = _json_file(root, merge_path, "merge audit")
    if bindings.get("merge_audit") != merge_sha or bindings.get("head_catchup") != head_sha:
        raise ValueError("Discord closure file hash binding is invalid")
    if canonical.get("merge_audit") != canonical_json_sha256(merge) or canonical.get("head_catchup") != canonical_json_sha256(head):
        raise ValueError("Discord closure canonical hash binding is invalid")
    if not isinstance(merge, dict) or merge.get("audit_kind") != "discord-parent-family-merge-v1":
        raise ValueError("Discord merge audit kind is invalid")
    if not isinstance(head, dict):
        raise ValueError("Discord head catch-up is invalid")
    _verify_scope(merge, closure, head, targets, scope=scope)
    _verify_closure_delta(closure, head)

    selected: dict[str, BloggerMessage] = {}
    for message in _baseline_messages(root, merge, request_path, targets):
        selected.setdefault(message.message_id, message)
    for message in _closure_messages(root, head, targets):
        selected[message.message_id] = message
    for message in sorted(selected.values(), key=lambda item: (_parse_time(item.timestamp), int(item.message_id))):
        if message.channel_id not in targets:
            continue
        when = _parse_time(message.timestamp)
        if start is not None and when < start:
            continue
        if end is not None and when >= end:
            continue
        yield message


def _verify_scope(
    merge: dict[str, object],
    closure: dict[str, object],
    head: dict[str, object],
    targets: set[str],
    *,
    scope: str,
) -> None:
    if merge.get("validation_errors") != []:
        raise ValueError("Discord merge validation errors block message corpus")
    static_scope = merge.get("static_scope")
    if not isinstance(static_scope, dict) or static_scope.get("exact_union") is not True or static_scope.get("pairwise_disjoint") is not True:
        raise ValueError("Discord merge static scope is not exact and disjoint")
    static = _id_set(merge.get("static_target_ids"), "merge static targets")
    if scope == "static":
        if not targets <= static:
            raise ValueError("Discord targets are outside the authorized merge scope")
    elif scope == "authorized_messages":
        if targets != set(
            authorized_blogger_message_target_ids(
                merge,
                closure=closure,
                head=head,
            )
        ):
            raise ValueError("Discord targets differ from the authorized message scope")
    else:
        raise ValueError("Discord blogger corpus scope is invalid")
    for field in ("non_private_incomplete_streams", "failed_streams", "truncated_streams", "message_reference_incomplete_shards"):
        if merge.get(field) != []:
            raise ValueError(f"Discord merge {field} blocks message corpus")
    hashes = merge.get("artifact_hashes")
    verified = merge.get("artifact_hash_verification")
    if not isinstance(hashes, dict) or not isinstance(verified, dict) or not hashes or set(hashes) != set(verified):
        raise ValueError("Discord merge artifact verification is invalid")
    for index, entries in hashes.items():
        flags = verified.get(index)
        if not isinstance(entries, dict) or not isinstance(flags, dict) or set(entries) != set(flags):
            raise ValueError("Discord merge artifact verification is invalid")
        for name, record in entries.items():
            if not isinstance(record, dict) or record.get("verified") is not True or flags.get(name) is not True:
                raise ValueError("Discord merge artifact verification failed")
    if closure.get("validation_errors") != []:
        raise ValueError("Discord closure validation errors block message corpus")
    unresolved = closure.get("unresolved")
    if not isinstance(unresolved, dict):
        raise ValueError("Discord closure unresolved state is invalid")
    for field in ("target_ids", "missing_target_ids", "unexpected_target_ids", "invalid_delta_target_ids", "unverified_evidence_target_ids", "non_private_incomplete_streams", "message_reference_incomplete_shards"):
        if unresolved.get(field) != []:
            raise ValueError(f"Discord closure {field} blocks message corpus")


def _verify_closure_delta(closure: dict[str, object], head: dict[str, object]) -> None:
    captured = closure.get("captured_delta")
    target_rows = head.get("targets")
    if not isinstance(captured, dict) or not isinstance(target_rows, list):
        raise ValueError("Discord closure delta is invalid")
    expected: set[str] = set()
    for target in target_rows:
        if not isinstance(target, dict):
            raise ValueError("Discord head catch-up target is invalid")
        expected.update(_id_set(target.get("new_message_ids"), "head target message IDs"))
    reported = _id_set(captured.get("message_ids"), "closure message IDs")
    if reported != expected:
        raise ValueError("Discord closure delta does not match head catch-up")


def _baseline_messages(
    root: Path,
    merge: dict[str, object],
    request_path: Path,
    targets: set[str],
) -> list[BloggerMessage]:
    request, request_sha = _json_file(root, request_path, "merge request")
    if not isinstance(request, dict) or request_sha != merge.get("merge_request_sha256"):
        raise ValueError("Discord merge request hash binding is invalid")
    shards = request.get("shards")
    if not isinstance(shards, list) or not shards:
        raise ValueError("Discord merge request shards are invalid")
    validated: list[tuple[Path, dict[str, object]]] = []
    for shard in shards:
        if not isinstance(shard, dict) or not isinstance(shard.get("index"), int) or not isinstance(shard.get("run_root"), str):
            raise ValueError("Discord merge request shard is invalid")
        index = str(shard["index"])
        run_root = _relative(shard["run_root"], "run root")
        _verify_run_hashes(root, run_root, shard, merge, index)
        checkpoint, _ = _json_file(root, run_root / "checkpoint.json", "checkpoint")
        if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("streams"), dict):
            raise ValueError("Discord checkpoint streams are invalid")
        validated.append((run_root, checkpoint))
    all_messages: list[BloggerMessage] = []
    for run_root, checkpoint in validated:
        for stream, state in checkpoint["streams"].items():
            if not isinstance(stream, str) or not stream.startswith("messages_"):
                continue
            channel_id = stream.removeprefix("messages_")
            if channel_id not in targets:
                continue
            if not isinstance(state, dict) or not isinstance(state.get("page_hashes"), list) or not isinstance(state.get("page_states"), list):
                raise ValueError("Discord checkpoint page ledger is invalid")
            if len(state["page_hashes"]) != len(state["page_states"]):
                raise ValueError("Discord checkpoint page ledger is invalid")
            for number, (raw_sha, page_state) in enumerate(zip(state["page_hashes"], state["page_states"], strict=True), start=1):
                descriptor = page_state.get("message_evidence") if isinstance(page_state, dict) else None
                all_messages.extend(_baseline_page(root, run_root, stream, number, raw_sha, descriptor))
    return all_messages


def _verify_run_hashes(root: Path, run_root: Path, shard: dict[str, object], merge: dict[str, object], index: str) -> None:
    hashes = merge["artifact_hashes"][index]
    assert isinstance(hashes, dict)
    names = {"request": "request.json", "manifest": "manifest.json", "checkpoint": "checkpoint.json", "targets_inventory": "inventory/targets.json"}
    for name, filename in names.items():
        expected = shard.get(f"{name}_sha256")
        record = hashes.get(name)
        _, actual = _json_file(root, run_root / filename, name)
        if not isinstance(record, dict) or record.get("expected") != expected or record.get("actual") != expected or expected != actual:
            raise ValueError("Discord merge artifact file hash changed")


def _baseline_page(root: Path, run_root: Path, stream: str, number: int, raw_sha: object, descriptor: object) -> list[BloggerMessage]:
    if not _sha(raw_sha) or not isinstance(descriptor, dict) or descriptor.get("schema_version") not in {1, 2}:
        raise ValueError("Discord message evidence descriptor is invalid")
    raw_relative = run_root / "pages" / stream / f"{number:06d}.json"
    raw, actual_raw_sha = _json_file(root, raw_relative, "raw page")
    if actual_raw_sha != raw_sha or not isinstance(raw, dict) or not isinstance(raw.get("payload"), list):
        raise ValueError("Discord raw page binding is invalid")
    evidence_value = descriptor.get("path")
    evidence_relative = run_root / _relative(evidence_value, "message evidence")
    evidence_path = _safe_file(root, evidence_relative, "message evidence")
    evidence_bytes = evidence_path.read_bytes()
    if hashlib.sha256(evidence_bytes).hexdigest() != descriptor.get("sha256") or descriptor.get("raw_page_sha256") != raw_sha or descriptor.get("raw_page_path") != (Path("pages") / stream / f"{number:06d}.json").as_posix():
        raise ValueError("Discord message evidence hash binding is invalid")
    rows = _jsonl(evidence_bytes)
    if len(rows) != len(raw["payload"]):
        raise ValueError("Discord raw/evidence row count differs")
    output: list[BloggerMessage] = []
    for line, (message, row) in enumerate(zip(raw["payload"], rows, strict=True), start=1):
        output.append(_baseline_message(message, row, stream, number, raw_relative, evidence_relative, actual_raw_sha, line))
    return output


def _baseline_message(message: object, row: object, stream: str, number: int, raw_relative: Path, evidence_relative: Path, raw_sha: str, line: int) -> BloggerMessage:
    if not isinstance(message, dict) or not isinstance(row, dict) or row.get("schema_version") != 2 or row.get("stream") != stream or row.get("channel_id") != message.get("channel_id") or row.get("page_number") != number:
        raise ValueError("Discord message evidence row is invalid")
    pointer = row.get("message_json_pointer")
    nodes = row.get("nodes")
    if not isinstance(pointer, str) or not isinstance(nodes, list) or not nodes or not isinstance(nodes[0], dict):
        raise ValueError("Discord message evidence root is invalid")
    root_node = nodes[0]
    if root_node.get("kind") != "root" or root_node.get("message_id") != message.get("id") or root_node.get("channel_id") != message.get("channel_id") or root_node.get("json_pointer") != pointer:
        raise ValueError("Discord message evidence root does not bind raw message")
    media = row.get("media")
    if not isinstance(media, list):
        raise ValueError("Discord message evidence media is invalid")
    for occurrence in media:
        source = occurrence.get("source") if isinstance(occurrence, dict) else None
        if not isinstance(source, dict) or source.get("stream") != stream or source.get("evidence_path") != (Path("pages") / stream / f"{number:06d}.json").as_posix() or source.get("evidence_sha256") != raw_sha:
            raise ValueError("Discord media source binding is invalid")
    return _message(message, f"{raw_relative.as_posix()}#{pointer}", tuple(f"{evidence_relative.as_posix()}#/{line}/media/{index}" for index in range(len(media))))


def _closure_messages(
    root: Path,
    head: dict[str, object],
    targets: set[str],
) -> list[BloggerMessage]:
    head_targets = head.get("targets")
    if not isinstance(head_targets, list):
        raise ValueError("Discord head catch-up targets are invalid")
    output: list[BloggerMessage] = []
    for target in head_targets:
        if not isinstance(target, dict) or not _snowflake(target.get("id")):
            raise ValueError("Discord head catch-up target is invalid")
        if target["id"] not in targets:
            continue
        evidence_relative = _relative(target.get("evidence_path"), "head evidence")
        evidence, evidence_sha = _json_file(root, evidence_relative, "head evidence")
        if evidence_sha != target.get("evidence_sha256") or not isinstance(evidence, dict) or evidence.get("audit_kind") != "discord-head-catchup-target-v1" or evidence.get("target_id") != target["id"]:
            raise ValueError("Discord head evidence binding is invalid")
        allowed = _id_set(evidence.get("new_message_ids"), "head evidence message IDs")
        if target.get("new_message_ids") != evidence.get("new_message_ids") or target.get("new_message_count") != len(allowed):
            raise ValueError("Discord head target summary differs from evidence")
        observed: set[str] = set()
        raw_pages = evidence.get("raw_pages")
        if not isinstance(raw_pages, list):
            raise ValueError("Discord head evidence raw pages are invalid")
        for descriptor in raw_pages:
            if not isinstance(descriptor, dict):
                raise ValueError("Discord head raw page descriptor is invalid")
            relative = _relative(descriptor.get("path"), "head raw page")
            raw, raw_sha = _json_file(root, relative, "head raw page")
            if raw_sha != descriptor.get("sha256") or not isinstance(raw, dict):
                raise ValueError("Discord head raw page hash binding is invalid")
            response = raw.get("response")
            messages = response.get("messages") if isinstance(response, dict) else None
            if not isinstance(messages, list):
                raise ValueError("Discord head raw page response is invalid")
            for index, message in enumerate(messages):
                if not isinstance(message, dict) or message.get("channel_id") != target["id"]:
                    raise ValueError("Discord head raw message is invalid")
                message_id = message.get("id")
                if message_id in allowed:
                    observed.add(message_id)
                    output.append(_message(message, f"{relative.as_posix()}#/response/messages/{index}", ()))
        if observed != allowed:
            raise ValueError("Discord explicit closure IDs are not locatable")
    return output


def _message(value: dict[str, object], snapshot_ref: str, media: tuple[str, ...]) -> BloggerMessage:
    message_id, channel_id, timestamp, content = value.get("id"), value.get("channel_id"), value.get("timestamp"), value.get("content")
    if not _snowflake(message_id) or not _snowflake(channel_id) or not isinstance(timestamp, str) or not isinstance(content, str):
        raise ValueError("Discord message identity is invalid")
    _parse_time(timestamp)
    author = value.get("author")
    author_id = author.get("id") if isinstance(author, dict) else None
    if author_id is not None and not _snowflake(author_id):
        raise ValueError("Discord message author is invalid")
    edited = value.get("edited_timestamp")
    if edited is not None and (not isinstance(edited, str) or _parse_time(edited) is None):
        raise ValueError("Discord edited timestamp is invalid")
    reference = value.get("message_reference")
    reply = reference.get("message_id") if isinstance(reference, dict) else None
    if reply is not None and not _snowflake(reply):
        raise ValueError("Discord reply reference is invalid")
    return BloggerMessage(message_id, channel_id, author_id, timestamp, edited, content, reply, snapshot_ref, canonical_json_sha256(value), media)


def _root(value: Path) -> Path:
    root = Path(value).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Discord export root is invalid")
    return root


def _relative(value: object, label: str) -> Path:
    path = Path(str(value or ""))
    if not str(value or "") or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Discord {label} path is unsafe")
    return path


def _safe_file(root: Path, relative: Path, label: str) -> Path:
    current = root
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink() or not current.is_dir():
            raise ValueError(f"Discord {label} path is unsafe")
    path = root / relative
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        raise ValueError(f"Discord {label} is missing") from None
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ValueError(f"Discord {label} path is unsafe")
    return path


def _json_file(root: Path, relative: Path, label: str) -> tuple[object, str]:
    content = _read_regular_file_bytes(root, relative, label)
    try:
        return json.loads(content), hashlib.sha256(content).hexdigest()
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Discord {label} is unreadable") from exc


def read_blogger_closure_bytes(
    *, export_root: Path, closure_audit_path: Path
) -> tuple[bytes, str]:
    """Freeze a safely-read closure audit before deriving blogger artifacts."""

    root = Path(export_root).absolute()
    relative = _relative(closure_audit_path, "closure audit")
    anchor = _RootAnchor.open(root)
    try:
        content = anchor.read_regular(relative, "closure audit")
        json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Discord closure audit is unreadable") from exc
    finally:
        anchor.close()
    return content, hashlib.sha256(content).hexdigest()


def _jsonl(content: bytes) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in content.splitlines():
        try:
            row = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Discord message evidence is unreadable") from exc
        if not isinstance(row, dict):
            raise ValueError("Discord message evidence row is invalid")
        rows.append(row)
    return rows


def _targets(values: Sequence[str]) -> set[str]:
    if isinstance(values, (str, bytes)) or not values or len(values) != len(set(values)):
        raise ValueError("Discord targets are invalid")
    return _id_set(list(values), "targets")


def _id_set(values: object, label: str) -> set[str]:
    if not isinstance(values, list) or any(not _snowflake(value) for value in values) or len(values) != len(set(values)):
        raise ValueError(f"Discord {label} are invalid")
    return set(values)


def _snowflake(value: object) -> bool:
    return isinstance(value, str) and value.isdecimal() and int(value) > 0


def _sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _check_range(start: datetime | None, end: datetime | None) -> None:
    for label, value in (("start", start), ("end", end)):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError(f"Discord corpus {label} is not timezone-aware")
    if start is not None and end is not None and start >= end:
        raise ValueError("Discord corpus range is invalid")


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Discord timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Discord timestamp is not timezone-aware")
    return parsed
