from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .connectors.web import CapturedResource


@dataclass(slots=True)
class StoredCapture:
    content_id: str
    source_kind: str
    title: str
    source_url: str
    final_url: str
    markdown_path: str
    raw_path: str
    metadata_path: str
    captured_at: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class ContentStore:
    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()

    def store(
        self,
        resource: CapturedResource,
        *,
        note: str = "",
        inbox_dir: str = "vault/00_Inbox",
    ) -> StoredCapture:
        content_id = self._content_id(resource)
        captured_at = datetime.now(UTC).isoformat()

        raw_dir = self._safe_path(f".omni/content/{content_id}")
        raw_dir.mkdir(parents=True, exist_ok=True)

        raw_ext = self._raw_extension(resource.content_type)
        raw_path = raw_dir / f"raw.{raw_ext}"
        metadata_path = raw_dir / "metadata.json"
        raw_path.write_text(resource.body, encoding="utf-8")
        metadata_path.write_text(
            json.dumps(resource.metadata_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        markdown_path = self._safe_path(f"{inbox_dir}/{timestamp}-{content_id}.md")
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(
            self._build_markdown(resource, content_id, captured_at, note),
            encoding="utf-8",
        )

        return StoredCapture(
            content_id=content_id,
            source_kind=resource.source_kind,
            title=resource.title,
            source_url=resource.url,
            final_url=resource.final_url,
            markdown_path=str(markdown_path.relative_to(self.workspace)),
            raw_path=str(raw_path.relative_to(self.workspace)),
            metadata_path=str(metadata_path.relative_to(self.workspace)),
            captured_at=captured_at,
        )

    def _safe_path(self, relative_path: str) -> Path:
        from ._storage import safe_workspace_path
        return safe_workspace_path(self.workspace, relative_path)

    def _content_id(self, resource: CapturedResource) -> str:
        payload = f"{resource.final_url}\n{resource.body}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    def _raw_extension(self, content_type: str) -> str:
        normalized = content_type.lower()
        if "html" in normalized:
            return "html"
        if "json" in normalized:
            return "json"
        return "txt"

    def _build_markdown(
        self,
        resource: CapturedResource,
        content_id: str,
        captured_at: str,
        note: str,
    ) -> str:
        lines = [
            "---",
            "omni_type: captured_url",
            f"content_id: {json.dumps(content_id, ensure_ascii=False)}",
            f"source_kind: {json.dumps(resource.source_kind, ensure_ascii=False)}",
            f"source_url: {json.dumps(resource.url, ensure_ascii=False)}",
            f"final_url: {json.dumps(resource.final_url, ensure_ascii=False)}",
            f"captured_at: {json.dumps(captured_at, ensure_ascii=False)}",
            "---",
            "",
            f"# {resource.title}",
            "",
            f"- Source: {resource.final_url}",
            f"- Kind: {resource.source_kind}",
            f"- Content ID: {content_id}",
        ]

        youtube_video_id = resource.metadata.get("youtube_video_id")
        if youtube_video_id:
            lines.append(f"- YouTube Video ID: {youtube_video_id}")

        if resource.description:
            lines.extend(["", "## Description", "", resource.description])

        if note:
            lines.extend(["", "## Note", "", note.strip()])

        if resource.text:
            excerpt = resource.text[:3000].strip()
            lines.extend(["", "## Extracted Text", "", excerpt])
        else:
            lines.extend(["", "## Extracted Text", "", "待补充正文、字幕或转写内容。"])

        lines.extend(
            ["", "## Next Actions", "", "- [ ] 总结", "- [ ] 抽取实体", "- [ ] 建立关系"]
        )
        return "\n".join(lines) + "\n"
