"""Pure extraction of message attribution and nested Discord media evidence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Any, Mapping
from urllib.parse import unquote


_DISCORD_EPOCH_MS = 1_420_070_400_000
_HTTP_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_URL_TRAILING_PUNCTUATION = ".,;!?"


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    stream: str
    evidence_path: str
    evidence_sha256: str | None
    root_message_id: str | None
    root_channel_id: str | None
    node_key: str
    json_pointer: str


@dataclass(frozen=True, slots=True)
class DeliveryAttribution:
    kind: str
    author_id: str | None
    webhook_id: str | None
    application_id: str | None
    username: str | None


@dataclass(frozen=True, slots=True)
class TimestampValidation:
    status: str
    api_timestamp: str | None
    edited_timestamp: str | None
    snowflake_timestamp: str | None
    delta_ms: float | None


@dataclass(frozen=True, slots=True)
class MessageNodeEvidence:
    node_key: str
    kind: str
    json_pointer: str
    message_id: str | None
    channel_id: str | None
    parent_node_key: str | None
    attribution: DeliveryAttribution
    timestamp: TimestampValidation


@dataclass(frozen=True, slots=True)
class MediaOccurrence:
    logical_key: str
    kind: str
    field: str
    node_key: str
    json_pointer: str
    source: SourceProvenance
    url: str | None
    proxy_url: str | None
    observed_url: str | None
    attachment_id: str | None
    downloadable: bool
    resolution: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ReferenceOccurrence:
    logical_key: str
    kind: str
    node_key: str
    json_pointer: str
    source: SourceProvenance
    value: str
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True, slots=True)
class EvidenceDiagnostic:
    code: str
    severity: str
    node_key: str | None
    json_pointer: str


@dataclass(frozen=True, slots=True)
class MessageEvidence:
    status: str
    nodes: tuple[MessageNodeEvidence, ...]
    media: tuple[MediaOccurrence, ...]
    references: tuple[ReferenceOccurrence, ...]
    diagnostics: tuple[EvidenceDiagnostic, ...]


def extract_message_evidence(
    message: Mapping[str, Any],
    *,
    stream: str,
    evidence_path: str,
    evidence_sha256: str | None = None,
    json_pointer: str = "",
    max_depth: int = 8,
) -> MessageEvidence:
    """Extract immutable evidence descriptions without I/O or network access."""

    if not isinstance(message, Mapping):
        raise TypeError("Discord message evidence input must be a mapping")
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    return _EvidenceBuilder(
        stream=stream,
        evidence_path=evidence_path,
        evidence_sha256=evidence_sha256,
        max_depth=max_depth,
    ).extract(message, json_pointer=json_pointer)


class _EvidenceBuilder:
    def __init__(
        self,
        *,
        stream: str,
        evidence_path: str,
        evidence_sha256: str | None,
        max_depth: int,
    ) -> None:
        self.stream = stream
        self.evidence_path = evidence_path
        self.evidence_sha256 = evidence_sha256
        self.max_depth = max_depth
        self.nodes: list[MessageNodeEvidence] = []
        self.media: list[MediaOccurrence] = []
        self.references: list[ReferenceOccurrence] = []
        self.diagnostics: list[EvidenceDiagnostic] = []
        self.root_message_id: str | None = None
        self.root_channel_id: str | None = None
        self.root_json_pointer = ""

    def extract(
        self,
        message: Mapping[str, Any],
        *,
        json_pointer: str,
    ) -> MessageEvidence:
        self.root_message_id = _snowflake(message.get("id"))
        self.root_channel_id = _snowflake(message.get("channel_id"))
        self.root_json_pointer = json_pointer
        self._walk_message(
            message,
            kind="root",
            json_pointer=json_pointer,
            parent_node_key=None,
            depth=0,
            snapshot_reference_id=None,
            ancestors=(),
        )
        status = (
            "partial"
            if any(item.severity == "error" for item in self.diagnostics)
            else "complete"
        )
        return MessageEvidence(
            status,
            tuple(self.nodes),
            tuple(self.media),
            tuple(self.references),
            tuple(self.diagnostics),
        )

    def _walk_message(
        self,
        message: Mapping[str, Any],
        *,
        kind: str,
        json_pointer: str,
        parent_node_key: str | None,
        depth: int,
        snapshot_reference_id: str | None,
        ancestors: tuple[str, ...],
    ) -> None:
        if depth > self.max_depth:
            self.diagnostics.append(
                EvidenceDiagnostic(
                    "message_depth_exceeded",
                    "error",
                    parent_node_key,
                    json_pointer,
                )
            )
            return
        if kind == "snapshot":
            logical_pointer = _relative_pointer(
                json_pointer,
                self.root_json_pointer,
            )
            node_key = (
                f"snapshot:{self.root_channel_id}:{self.root_message_id}:"
                f"{logical_pointer}"
            )
            recursion_identity = f"snapshot-object:{id(message)}"
            message_id = None
            channel_id = None
            attribution = DeliveryAttribution(
                "snapshot_unattributed", None, None, None, None
            )
            timestamp = _validate_snapshot_timestamp(
                message,
                reference_id=snapshot_reference_id,
                node_key=node_key,
                json_pointer=json_pointer,
                diagnostics=self.diagnostics,
            )
        else:
            message_id = _snowflake(message.get("id"))
            channel_id = _snowflake(message.get("channel_id"))
            node_key = _message_node_key(channel_id, message_id, json_pointer)
            recursion_identity = (
                node_key
                if message_id is not None and channel_id is not None
                else f"message-object:{id(message)}"
            )
            attribution = _delivery_attribution(message)
            if attribution.kind == "unknown":
                self.diagnostics.append(
                    EvidenceDiagnostic(
                        "delivery_author_invalid",
                        "error",
                        node_key,
                        _pointer(json_pointer, "author"),
                    )
                )
            if (
                attribution.kind == "webhook"
                and attribution.author_id != attribution.webhook_id
            ):
                self.diagnostics.append(
                    EvidenceDiagnostic(
                        "webhook_author_id_mismatch",
                        "error",
                        node_key,
                        _pointer(json_pointer, "author", "id"),
                    )
                )
            timestamp = _validate_full_timestamp(
                message,
                node_key=node_key,
                json_pointer=json_pointer,
                diagnostics=self.diagnostics,
            )
        if recursion_identity in ancestors:
            self.diagnostics.append(
                EvidenceDiagnostic(
                    "message_cycle_detected",
                    "error",
                    node_key,
                    json_pointer,
                )
            )
            return
        descendant_ancestors = (*ancestors, recursion_identity)
        node = MessageNodeEvidence(
            node_key=node_key,
            kind=kind,
            json_pointer=json_pointer,
            message_id=message_id,
            channel_id=channel_id,
            parent_node_key=parent_node_key,
            attribution=attribution,
            timestamp=timestamp,
        )
        self.nodes.append(node)
        self._extract_node_content(message, node)

        reference = message.get("message_reference")
        reference_id = (
            _snowflake(reference.get("message_id"))
            if isinstance(reference, Mapping)
            else None
        )
        reference_channel_id = (
            _snowflake(reference.get("channel_id"))
            if isinstance(reference, Mapping)
            else None
        )
        referenced = message.get("referenced_message")
        can_resolve_reference = message.get("type") in {19, 21, 23}
        if isinstance(reference, Mapping) and can_resolve_reference:
            if "referenced_message" not in message:
                self.diagnostics.append(
                    EvidenceDiagnostic(
                        "referenced_message_unknown",
                        "error",
                        node_key,
                        _pointer(json_pointer, "referenced_message"),
                    )
                )
            elif referenced is None:
                self.diagnostics.append(
                    EvidenceDiagnostic(
                        "referenced_message_deleted",
                        "info",
                        node_key,
                        _pointer(json_pointer, "referenced_message"),
                    )
                )
            elif not isinstance(referenced, Mapping):
                self.diagnostics.append(
                    EvidenceDiagnostic(
                        "referenced_message_invalid",
                        "error",
                        node_key,
                        _pointer(json_pointer, "referenced_message"),
                    )
                )
        if isinstance(referenced, Mapping):
            referenced_id = _snowflake(referenced.get("id"))
            referenced_channel_id = _snowflake(referenced.get("channel_id"))
            if (
                (reference_id is not None and referenced_id != reference_id)
                or (
                    reference_channel_id is not None
                    and referenced_channel_id != reference_channel_id
                )
            ):
                self.diagnostics.append(
                    EvidenceDiagnostic(
                        "referenced_message_identity_mismatch",
                        "error",
                        node_key,
                        _pointer(json_pointer, "referenced_message"),
                    )
                )
            self._walk_message(
                referenced,
                kind="referenced_message",
                json_pointer=_pointer(json_pointer, "referenced_message"),
                parent_node_key=node_key,
                depth=depth + 1,
                snapshot_reference_id=None,
                ancestors=descendant_ancestors,
            )
        snapshots = message.get("message_snapshots")
        if isinstance(snapshots, list):
            for index, snapshot in enumerate(snapshots):
                snapshot_pointer = _pointer(
                    json_pointer,
                    "message_snapshots",
                    index,
                )
                snapshot_message = (
                    snapshot.get("message") if isinstance(snapshot, Mapping) else None
                )
                if not isinstance(snapshot_message, Mapping):
                    self.diagnostics.append(
                        EvidenceDiagnostic(
                            "message_snapshot_invalid",
                            "error",
                            node_key,
                            snapshot_pointer,
                        )
                    )
                    continue
                self._walk_message(
                    snapshot_message,
                    kind="snapshot",
                    json_pointer=_pointer(snapshot_pointer, "message"),
                    parent_node_key=node_key,
                    depth=depth + 1,
                    snapshot_reference_id=reference_id,
                    ancestors=descendant_ancestors,
                )

    def _extract_node_content(
        self,
        message: Mapping[str, Any],
        node: MessageNodeEvidence,
    ) -> None:
        owner = node.message_id or node.node_key
        attachments_by_id: dict[str, MediaOccurrence] = {}
        attachments_by_filename: dict[str, list[MediaOccurrence]] = {}
        attachments = message.get("attachments")
        if isinstance(attachments, list):
            for index, attachment in enumerate(attachments):
                if not isinstance(attachment, Mapping):
                    continue
                attachment_id = _snowflake(attachment.get("id"))
                observed_url = (
                    attachment.get("url")
                    if isinstance(attachment.get("url"), str)
                    else None
                )
                direct_url = _http_url(observed_url)
                proxy_url = _http_url(attachment.get("proxy_url"))
                url = direct_url or proxy_url
                if attachment_id is None or url is None:
                    continue
                pointer = _pointer(node.json_pointer, "attachments", index)
                duplicate_id = attachment_id in attachments_by_id
                if duplicate_id:
                    self.diagnostics.append(
                        EvidenceDiagnostic(
                            "attachment_id_duplicate",
                            "error",
                            node.node_key,
                            _pointer(pointer, "id"),
                        )
                    )
                occurrence = MediaOccurrence(
                    logical_key=f"{owner}:attachment:{attachment_id}",
                    kind="attachment",
                    field="attachment",
                    node_key=node.node_key,
                    json_pointer=pointer,
                    source=self._source(node.node_key, pointer),
                    url=url,
                    proxy_url=proxy_url,
                    observed_url=observed_url,
                    attachment_id=attachment_id,
                    downloadable=True,
                    resolution="direct" if direct_url is not None else "proxy_only",
                    metadata=deepcopy(dict(attachment)),
                )
                self.media.append(occurrence)
                if not duplicate_id:
                    attachments_by_id[attachment_id] = occurrence
                filename = attachment.get("filename")
                if isinstance(filename, str):
                    attachments_by_filename.setdefault(filename, []).append(occurrence)

        embeds = message.get("embeds")
        if isinstance(embeds, list):
            for index, embed in enumerate(embeds):
                if not isinstance(embed, Mapping):
                    continue
                for field in ("image", "thumbnail", "video"):
                    value = embed.get(field)
                    if not isinstance(value, Mapping):
                        continue
                    pointer = _pointer(node.json_pointer, "embeds", index, field)
                    observed_url = (
                        value.get("url") if isinstance(value.get("url"), str) else None
                    )
                    attachment_id = _snowflake(value.get("attachment_id"))
                    logical_key = f"{owner}:embed:{index}:{field}"
                    url = _http_url(observed_url)
                    proxy_url = _http_url(value.get("proxy_url"))
                    resolution = "direct" if url is not None else "proxy_only"
                    downloadable = url is not None or proxy_url is not None
                    if attachment_id is not None:
                        logical_key = f"{owner}:attachment:{attachment_id}"
                        attachment = attachments_by_id.get(attachment_id)
                        if attachment is not None:
                            resolution = "attachment_id"
                            url = attachment.url
                            proxy_url = attachment.proxy_url
                            downloadable = attachment.downloadable
                        else:
                            resolution = "attachment_id_unlisted"
                            url = _http_url(observed_url) or proxy_url
                            downloadable = url is not None
                    elif (
                        isinstance(observed_url, str)
                        and observed_url.startswith("attachment://")
                    ):
                        filename = unquote(
                            observed_url.removeprefix("attachment://")
                        )
                        matches = attachments_by_filename.get(filename, [])
                        if len(matches) == 1:
                            attachment = matches[0]
                            resolution = "attachment_filename"
                            logical_key = attachment.logical_key
                            attachment_id = attachment.attachment_id
                            url = attachment.url
                            proxy_url = attachment.proxy_url
                            downloadable = attachment.downloadable
                        elif len(matches) > 1:
                            resolution = "ambiguous_attachment_filename"
                            url = None
                            proxy_url = None
                            downloadable = False
                            self.diagnostics.append(
                                EvidenceDiagnostic(
                                    "embed_attachment_ambiguous",
                                    "error",
                                    node.node_key,
                                    pointer,
                                )
                            )
                        else:
                            resolution = "unresolved_attachment_reference"
                            url = None
                            proxy_url = None
                            downloadable = False
                            self.diagnostics.append(
                                EvidenceDiagnostic(
                                    "embed_attachment_unresolved",
                                    "error",
                                    node.node_key,
                                    pointer,
                                )
                            )
                    elif url is None:
                        url = proxy_url
                        if url is None:
                            continue
                    self.media.append(
                        MediaOccurrence(
                            logical_key=logical_key,
                            kind="embed",
                            field=field,
                            node_key=node.node_key,
                            json_pointer=pointer,
                            source=self._source(node.node_key, pointer),
                            url=url,
                            proxy_url=proxy_url,
                            observed_url=observed_url,
                            attachment_id=attachment_id,
                            downloadable=downloadable,
                            resolution=resolution,
                            metadata=deepcopy(dict(value)),
                        )
                    )
                for container_name, field in (
                    ("author", "author_icon"),
                    ("footer", "footer_icon"),
                ):
                    container = embed.get(container_name)
                    if not isinstance(container, Mapping):
                        continue
                    observed_url = (
                        container.get("icon_url")
                        if isinstance(container.get("icon_url"), str)
                        else None
                    )
                    pointer = _pointer(
                        node.json_pointer,
                        "embeds",
                        index,
                        container_name,
                        "icon_url",
                    )
                    logical_key = f"{owner}:embed:{index}:{field}"
                    url = _http_url(observed_url)
                    proxy_url = _http_url(container.get("proxy_icon_url"))
                    attachment_id = None
                    resolution = "direct" if url is not None else "proxy_only"
                    downloadable = url is not None or proxy_url is not None
                    if (
                        isinstance(observed_url, str)
                        and observed_url.startswith("attachment://")
                    ):
                        filename = unquote(
                            observed_url.removeprefix("attachment://")
                        )
                        matches = attachments_by_filename.get(filename, [])
                        if len(matches) == 1:
                            attachment = matches[0]
                            logical_key = attachment.logical_key
                            url = attachment.url
                            proxy_url = attachment.proxy_url
                            attachment_id = attachment.attachment_id
                            resolution = "attachment_filename"
                            downloadable = attachment.downloadable
                        elif len(matches) > 1:
                            url = None
                            proxy_url = None
                            resolution = "ambiguous_attachment_filename"
                            downloadable = False
                            self.diagnostics.append(
                                EvidenceDiagnostic(
                                    "embed_attachment_ambiguous",
                                    "error",
                                    node.node_key,
                                    pointer,
                                )
                            )
                        else:
                            url = None
                            proxy_url = None
                            resolution = "unresolved_attachment_reference"
                            downloadable = False
                            self.diagnostics.append(
                                EvidenceDiagnostic(
                                    "embed_attachment_unresolved",
                                    "error",
                                    node.node_key,
                                    pointer,
                                )
                            )
                    elif url is None:
                        url = proxy_url
                        if url is None:
                            continue
                    self.media.append(
                        MediaOccurrence(
                            logical_key=logical_key,
                            kind="embed",
                            field=field,
                            node_key=node.node_key,
                            json_pointer=pointer,
                            source=self._source(node.node_key, pointer),
                            url=url,
                            proxy_url=proxy_url,
                            observed_url=observed_url,
                            attachment_id=attachment_id,
                            downloadable=downloadable,
                            resolution=resolution,
                            metadata=deepcopy(dict(container)),
                        )
                    )
                for pointer_parts, value in (
                    (("embeds", index, "url"), embed.get("url")),
                    (
                        ("embeds", index, "provider", "url"),
                        _mapping_value(embed.get("provider"), "url"),
                    ),
                    (
                        ("embeds", index, "author", "url"),
                        _mapping_value(embed.get("author"), "url"),
                    ),
                ):
                    url = _http_url(value)
                    if url is not None:
                        pointer = _pointer(node.json_pointer, *pointer_parts)
                        self.references.append(
                            _reference_occurrence(
                                node.node_key,
                                pointer,
                                self._source(node.node_key, pointer),
                                "embed_link",
                                url,
                            )
                        )

        content = message.get("content")
        if isinstance(content, str):
            content_pointer = _pointer(node.json_pointer, "content")
            for url, start, end in _content_urls(content):
                self.references.append(
                    _reference_occurrence(
                        node.node_key,
                        content_pointer,
                        self._source(node.node_key, content_pointer),
                        "content_url",
                        url,
                        start=start,
                        end=end,
                    )
                )

        components = message.get("components")
        if isinstance(components, list):
            self._walk_components(
                components,
                pointer=_pointer(node.json_pointer, "components"),
                node=node,
                owner=owner,
                attachments_by_id=attachments_by_id,
                attachments_by_filename=attachments_by_filename,
                ancestors=(),
            )
        self._extract_stickers(message, node)
        self._extract_poll(message, node)

    def _extract_stickers(
        self,
        message: Mapping[str, Any],
        node: MessageNodeEvidence,
    ) -> None:
        for field_name in ("sticker_items", "stickers"):
            stickers = message.get(field_name)
            if not isinstance(stickers, list):
                continue
            for index, sticker in enumerate(stickers):
                if not isinstance(sticker, Mapping):
                    continue
                sticker_id = _snowflake(sticker.get("id"))
                format_type = sticker.get("format_type")
                if sticker_id is None or isinstance(format_type, bool):
                    continue
                url = _sticker_url(sticker_id, format_type)
                pointer = _pointer(node.json_pointer, field_name, index)
                if url is None:
                    self.diagnostics.append(
                        EvidenceDiagnostic(
                            "sticker_format_unsupported",
                            "error",
                            node.node_key,
                            pointer,
                        )
                    )
                self.media.append(
                    MediaOccurrence(
                        logical_key=f"sticker:{sticker_id}",
                        kind="sticker",
                        field=field_name,
                        node_key=node.node_key,
                        json_pointer=pointer,
                        source=self._source(node.node_key, pointer),
                        url=url,
                        proxy_url=None,
                        observed_url=url,
                        attachment_id=None,
                        downloadable=url is not None,
                        resolution=(
                            "derived_cdn" if url is not None else "unsupported_format"
                        ),
                        metadata=deepcopy(dict(sticker)),
                    )
                )

    def _extract_poll(
        self,
        message: Mapping[str, Any],
        node: MessageNodeEvidence,
    ) -> None:
        poll = message.get("poll")
        if not isinstance(poll, Mapping):
            return
        question = poll.get("question")
        if isinstance(question, Mapping):
            self._extract_poll_media(
                question,
                node=node,
                pointer=_pointer(node.json_pointer, "poll", "question"),
            )
        answers = poll.get("answers")
        if not isinstance(answers, list):
            return
        for index, answer in enumerate(answers):
            if not isinstance(answer, Mapping):
                continue
            poll_media = answer.get("poll_media")
            if not isinstance(poll_media, Mapping):
                continue
            self._extract_poll_media(
                poll_media,
                node=node,
                pointer=_pointer(
                    node.json_pointer,
                    "poll",
                    "answers",
                    index,
                    "poll_media",
                ),
            )

    def _extract_poll_media(
        self,
        poll_media: Mapping[str, Any],
        *,
        node: MessageNodeEvidence,
        pointer: str,
    ) -> None:
        emoji = poll_media.get("emoji")
        if not isinstance(emoji, Mapping):
            return
        emoji_pointer = _pointer(pointer, "emoji")
        emoji_id = _snowflake(emoji.get("id"))
        name = emoji.get("name")
        if emoji_id is not None:
            suffix = "?animated=true" if emoji.get("animated") is True else ""
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.webp{suffix}"
            self.media.append(
                MediaOccurrence(
                    logical_key=f"emoji:{emoji_id}",
                    kind="emoji",
                    field="poll_emoji",
                    node_key=node.node_key,
                    json_pointer=emoji_pointer,
                    source=self._source(node.node_key, emoji_pointer),
                    url=url,
                    proxy_url=None,
                    observed_url=url,
                    attachment_id=None,
                    downloadable=True,
                    resolution="derived_cdn",
                    metadata=deepcopy(dict(emoji)),
                )
            )
        elif isinstance(name, str) and name:
            self.references.append(
                _reference_occurrence(
                    node.node_key,
                    emoji_pointer,
                    self._source(node.node_key, emoji_pointer),
                    "unicode_emoji",
                    name,
                )
            )

    def _walk_components(
        self,
        value: object,
        *,
        pointer: str,
        node: MessageNodeEvidence,
        owner: str,
        attachments_by_id: Mapping[str, MediaOccurrence],
        attachments_by_filename: Mapping[str, list[MediaOccurrence]],
        ancestors: tuple[int, ...],
    ) -> None:
        if isinstance(value, list):
            identity = id(value)
            if identity in ancestors:
                self._component_cycle(node.node_key, pointer)
                return
            descendants = (*ancestors, identity)
            for index, child in enumerate(value):
                self._walk_components(
                    child,
                    pointer=_pointer(pointer, index),
                    node=node,
                    owner=owner,
                    attachments_by_id=attachments_by_id,
                    attachments_by_filename=attachments_by_filename,
                    ancestors=descendants,
                )
            return
        if not isinstance(value, Mapping):
            return
        identity = id(value)
        if identity in ancestors:
            self._component_cycle(node.node_key, pointer)
            return
        descendants = (*ancestors, identity)
        component_type = value.get("type")
        handled: set[str] = set()
        if component_type == 11 and isinstance(value.get("media"), Mapping):
            self._extract_component_media(
                value["media"],
                field="thumbnail",
                pointer=_pointer(pointer, "media"),
                node=node,
                owner=owner,
                attachments_by_id=attachments_by_id,
                attachments_by_filename=attachments_by_filename,
            )
            handled.add("media")
        elif component_type == 12 and isinstance(value.get("items"), list):
            for index, item in enumerate(value["items"]):
                media = item.get("media") if isinstance(item, Mapping) else None
                if isinstance(media, Mapping):
                    self._extract_component_media(
                        media,
                        field="media_gallery",
                        pointer=_pointer(pointer, "items", index, "media"),
                        node=node,
                        owner=owner,
                        attachments_by_id=attachments_by_id,
                        attachments_by_filename=attachments_by_filename,
                    )
            handled.add("items")
        elif component_type == 13 and isinstance(value.get("file"), Mapping):
            self._extract_component_media(
                value["file"],
                field="file",
                pointer=_pointer(pointer, "file"),
                node=node,
                owner=owner,
                attachments_by_id=attachments_by_id,
                attachments_by_filename=attachments_by_filename,
            )
            handled.add("file")

        link = _http_url(value.get("url"))
        if link is not None:
            link_pointer = _pointer(pointer, "url")
            self.references.append(
                _reference_occurrence(
                    node.node_key,
                    link_pointer,
                    self._source(node.node_key, link_pointer),
                    "component_link",
                    link,
                )
            )
        for key, child in value.items():
            if key in handled or key == "url":
                continue
            if isinstance(child, (Mapping, list)):
                self._walk_components(
                    child,
                    pointer=_pointer(pointer, key),
                    node=node,
                    owner=owner,
                    attachments_by_id=attachments_by_id,
                    attachments_by_filename=attachments_by_filename,
                    ancestors=descendants,
                )

    def _extract_component_media(
        self,
        media: Mapping[str, Any],
        *,
        field: str,
        pointer: str,
        node: MessageNodeEvidence,
        owner: str,
        attachments_by_id: Mapping[str, MediaOccurrence],
        attachments_by_filename: Mapping[str, list[MediaOccurrence]],
    ) -> None:
        observed_url = media.get("url") if isinstance(media.get("url"), str) else None
        proxy_url = _http_url(media.get("proxy_url"))
        attachment_id = _snowflake(media.get("attachment_id"))
        resolution = "direct"
        logical_pointer = _relative_pointer(pointer, node.json_pointer)
        logical_key = f"{owner}:component:{field}:{logical_pointer}"
        resolved_url = _http_url(observed_url)
        resolved_proxy = proxy_url
        downloadable = resolved_url is not None or resolved_proxy is not None
        if resolved_url is None and resolved_proxy is not None:
            resolved_url = resolved_proxy
            resolution = "proxy_only"

        if attachment_id is not None:
            attachment = attachments_by_id.get(attachment_id)
            logical_key = f"{owner}:attachment:{attachment_id}"
            if attachment is not None:
                resolution = "attachment_id"
                resolved_url = attachment.url
                resolved_proxy = attachment.proxy_url
                downloadable = attachment.downloadable
            else:
                resolution = "attachment_id_unlisted"
                resolved_url = _http_url(observed_url) or proxy_url
                downloadable = resolved_url is not None
                if not downloadable:
                    self._component_resolution_error(
                        "component_attachment_unresolved",
                        node.node_key,
                        pointer,
                    )
        elif isinstance(observed_url, str) and observed_url.startswith("attachment://"):
            filename = unquote(observed_url.removeprefix("attachment://"))
            matches = attachments_by_filename.get(filename, [])
            if len(matches) == 1:
                attachment = matches[0]
                resolution = "attachment_filename"
                logical_key = attachment.logical_key
                attachment_id = attachment.attachment_id
                resolved_url = attachment.url
                resolved_proxy = attachment.proxy_url
                downloadable = attachment.downloadable
            elif len(matches) > 1:
                resolution = "ambiguous_attachment_filename"
                resolved_url = None
                resolved_proxy = None
                downloadable = False
                self._component_resolution_error(
                    "component_attachment_ambiguous",
                    node.node_key,
                    pointer,
                )
            else:
                resolution = "unresolved_attachment_reference"
                resolved_url = None
                resolved_proxy = None
                downloadable = False
                self._component_resolution_error(
                    "component_attachment_unresolved",
                    node.node_key,
                    pointer,
                )
        elif resolved_url is None:
            resolution = "unresolved_media_url"
            downloadable = False
            self._component_resolution_error(
                "component_attachment_unresolved",
                node.node_key,
                pointer,
            )

        self.media.append(
            MediaOccurrence(
                logical_key=logical_key,
                kind="component",
                field=field,
                node_key=node.node_key,
                json_pointer=pointer,
                source=self._source(node.node_key, pointer),
                url=resolved_url,
                proxy_url=resolved_proxy,
                observed_url=observed_url,
                attachment_id=attachment_id,
                downloadable=downloadable,
                resolution=resolution,
                metadata=deepcopy(dict(media)),
            )
        )

    def _component_resolution_error(
        self,
        code: str,
        node_key: str,
        pointer: str,
    ) -> None:
        self.diagnostics.append(
            EvidenceDiagnostic(code, "error", node_key, pointer)
        )

    def _component_cycle(self, node_key: str, pointer: str) -> None:
        self.diagnostics.append(
            EvidenceDiagnostic(
                "component_cycle_detected",
                "error",
                node_key,
                pointer,
            )
        )

    def _source(self, node_key: str, pointer: str) -> SourceProvenance:
        return SourceProvenance(
            stream=self.stream,
            evidence_path=self.evidence_path,
            evidence_sha256=self.evidence_sha256,
            root_message_id=self.root_message_id,
            root_channel_id=self.root_channel_id,
            node_key=node_key,
            json_pointer=pointer,
        )


def _snowflake(value: object) -> str | None:
    return value if isinstance(value, str) and value.isdigit() and int(value) > 0 else None


def _message_node_key(
    channel_id: str | None,
    message_id: str | None,
    json_pointer: str,
) -> str:
    if channel_id is not None and message_id is not None:
        return f"message:{channel_id}:{message_id}"
    return f"invalid-message:{json_pointer or '/'}"


def _delivery_attribution(message: Mapping[str, Any]) -> DeliveryAttribution:
    author = message.get("author")
    author_mapping = author if isinstance(author, Mapping) else {}
    author_id = _snowflake(author_mapping.get("id"))
    webhook_id = _snowflake(message.get("webhook_id"))
    if webhook_id is not None:
        kind = "webhook"
    elif author_mapping.get("bot") is True:
        kind = "bot_user"
    elif author_id is not None:
        kind = "human_candidate"
    else:
        kind = "unknown"
    username = author_mapping.get("username")
    return DeliveryAttribution(
        kind=kind,
        author_id=author_id,
        webhook_id=webhook_id,
        application_id=_snowflake(message.get("application_id")),
        username=username if isinstance(username, str) else None,
    )


def _validate_full_timestamp(
    message: Mapping[str, Any],
    *,
    node_key: str,
    json_pointer: str,
    diagnostics: list[EvidenceDiagnostic],
) -> TimestampValidation:
    message_id = _snowflake(message.get("id"))
    raw_timestamp = message.get("timestamp")
    timestamp = _parse_timestamp(raw_timestamp)
    raw_edited = message.get("edited_timestamp")
    edited = _parse_timestamp(raw_edited) if raw_edited is not None else None
    if message_id is None or timestamp is None:
        diagnostics.append(
            EvidenceDiagnostic(
                "full_message_identity_or_timestamp_invalid",
                "error",
                node_key,
                json_pointer,
            )
        )
        return TimestampValidation(
            "invalid",
            raw_timestamp if isinstance(raw_timestamp, str) else None,
            raw_edited if isinstance(raw_edited, str) else None,
            None,
            None,
        )
    snowflake_ms = (int(message_id) >> 22) + _DISCORD_EPOCH_MS
    api_microseconds = _epoch_microseconds(timestamp)
    delta_microseconds = abs(api_microseconds - snowflake_ms * 1000)
    status = "valid" if delta_microseconds <= 1000 else "mismatch"
    if status == "mismatch":
        diagnostics.append(
            EvidenceDiagnostic(
                "full_message_timestamp_snowflake_mismatch",
                "error",
                node_key,
                json_pointer,
            )
        )
    if raw_edited is not None and edited is None:
        diagnostics.append(
            EvidenceDiagnostic(
                "edited_timestamp_invalid",
                "error",
                node_key,
                _pointer(json_pointer, "edited_timestamp"),
            )
        )
    elif edited is not None and edited < timestamp:
        diagnostics.append(
            EvidenceDiagnostic(
                "edited_timestamp_before_created",
                "error",
                node_key,
                _pointer(json_pointer, "edited_timestamp"),
            )
        )
    snowflake_timestamp = datetime.fromtimestamp(
        snowflake_ms / 1000,
        tz=UTC,
    ).isoformat(timespec="milliseconds")
    return TimestampValidation(
        status,
        raw_timestamp if isinstance(raw_timestamp, str) else None,
        raw_edited if isinstance(raw_edited, str) else None,
        snowflake_timestamp,
        delta_microseconds / 1000,
    )


def _validate_snapshot_timestamp(
    message: Mapping[str, Any],
    *,
    reference_id: str | None,
    node_key: str,
    json_pointer: str,
    diagnostics: list[EvidenceDiagnostic],
) -> TimestampValidation:
    raw_timestamp = message.get("timestamp")
    timestamp = _parse_timestamp(raw_timestamp)
    raw_edited = message.get("edited_timestamp")
    edited = _parse_timestamp(raw_edited) if raw_edited is not None else None
    if timestamp is None:
        diagnostics.append(
            EvidenceDiagnostic(
                "snapshot_timestamp_invalid",
                "error",
                node_key,
                _pointer(json_pointer, "timestamp"),
            )
        )
        return TimestampValidation(
            "invalid",
            raw_timestamp if isinstance(raw_timestamp, str) else None,
            raw_edited if isinstance(raw_edited, str) else None,
            None,
            None,
        )
    if raw_edited is not None and edited is None:
        diagnostics.append(
            EvidenceDiagnostic(
                "edited_timestamp_invalid",
                "error",
                node_key,
                _pointer(json_pointer, "edited_timestamp"),
            )
        )
    elif edited is not None and edited < timestamp:
        diagnostics.append(
            EvidenceDiagnostic(
                "edited_timestamp_before_created",
                "error",
                node_key,
                _pointer(json_pointer, "edited_timestamp"),
            )
        )
    if reference_id is None:
        return TimestampValidation(
            "unverifiable",
            raw_timestamp if isinstance(raw_timestamp, str) else None,
            raw_edited if isinstance(raw_edited, str) else None,
            None,
            None,
        )
    snowflake_ms = (int(reference_id) >> 22) + _DISCORD_EPOCH_MS
    delta_microseconds = abs(
        _epoch_microseconds(timestamp) - snowflake_ms * 1000
    )
    status = "valid_reference" if delta_microseconds <= 1000 else "warning_mismatch"
    if status == "warning_mismatch":
        diagnostics.append(
            EvidenceDiagnostic(
                "snapshot_timestamp_reference_mismatch",
                "warning",
                node_key,
                _pointer(json_pointer, "timestamp"),
            )
        )
    return TimestampValidation(
        status,
        raw_timestamp if isinstance(raw_timestamp, str) else None,
        raw_edited if isinstance(raw_edited, str) else None,
        datetime.fromtimestamp(snowflake_ms / 1000, tz=UTC).isoformat(
            timespec="milliseconds"
        ),
        delta_microseconds / 1000,
    )


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _epoch_microseconds(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value.astimezone(UTC) - epoch
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _pointer(base: str, *parts: object) -> str:
    pointer = base.rstrip("/")
    for part in parts:
        encoded = str(part).replace("~", "~0").replace("/", "~1")
        pointer += f"/{encoded}"
    return pointer or ""


def _relative_pointer(pointer: str, base: str) -> str:
    normalized_base = base.rstrip("/")
    if not normalized_base:
        return pointer or "/"
    if pointer == normalized_base:
        return "/"
    prefix = f"{normalized_base}/"
    if pointer.startswith(prefix):
        return pointer[len(normalized_base) :]
    return pointer or "/"


def _http_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value if value.lower().startswith(("http://", "https://")) else None


def _sticker_url(sticker_id: str, format_type: object) -> str | None:
    if format_type in (1, 2):
        return f"https://cdn.discordapp.com/stickers/{sticker_id}.png"
    if format_type == 3:
        return f"https://cdn.discordapp.com/stickers/{sticker_id}.json"
    if format_type == 4:
        return f"https://media.discordapp.net/stickers/{sticker_id}.gif"
    return None


def _mapping_value(value: object, key: str) -> object:
    return value.get(key) if isinstance(value, Mapping) else None


def _content_urls(content: str) -> list[tuple[str, int, int]]:
    occurrences: list[tuple[str, int, int]] = []
    for match in _HTTP_URL.finditer(content):
        value = match.group(0).rstrip(_URL_TRAILING_PUNCTUATION)
        occurrences.append((value, match.start(), match.start() + len(value)))
    return occurrences


def _reference_occurrence(
    node_key: str,
    pointer: str,
    source: SourceProvenance,
    kind: str,
    value: str,
    *,
    start: int | None = None,
    end: int | None = None,
) -> ReferenceOccurrence:
    suffix = start if start is not None else 0
    return ReferenceOccurrence(
        logical_key=f"{node_key}:reference:{pointer}:{suffix}",
        kind=kind,
        node_key=node_key,
        json_pointer=pointer,
        source=source,
        value=value,
        start=start,
        end=end,
    )
