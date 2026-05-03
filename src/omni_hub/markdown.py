from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class MarkdownDocument:
    path: str
    title: str
    body: str
    metadata: dict[str, object] = field(default_factory=dict)
    headings: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    wiki_links: list[str] = field(default_factory=list)
    markdown_links: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_markdown(path: Path, workspace: Path) -> MarkdownDocument:
    text = path.read_text(encoding="utf-8")
    metadata, body = split_frontmatter(text)
    title = extract_title(body) or str(metadata.get("title", "")) or path.stem

    return MarkdownDocument(
        path=str(path.relative_to(workspace)),
        title=title,
        body=body,
        metadata=metadata,
        headings=extract_headings(body),
        tags=extract_tags(body),
        wiki_links=extract_wiki_links(body),
        markdown_links=extract_markdown_links(body),
    )


def split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        return {}, text

    end_marker = "\n---\n"
    end = text.find(end_marker, 4)
    if end == -1:
        return {}, text

    raw_metadata = text[4:end]
    body = text[end + len(end_marker) :]
    return parse_frontmatter(raw_metadata), body


def parse_frontmatter(raw_metadata: str) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for line in raw_metadata.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue

        key, value = stripped.split(":", maxsplit=1)
        key = key.strip()
        value = value.strip()
        metadata[key] = parse_frontmatter_value(value)
    return metadata


def parse_frontmatter_value(value: str) -> object:
    if not value:
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass

    normalized = value.lower()
    if normalized in {"true", "false"}:
        return normalized == "true"
    if normalized in {"null", "none"}:
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value.strip("\"'")


def extract_title(body: str) -> str:
    for line in body.splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            return match.group(1).strip()
    return ""


def extract_headings(body: str) -> list[str]:
    headings: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^#{1,6}\s+(.+)$", line.strip())
        if match:
            headings.append(match.group(1).strip())
    return headings


def extract_tags(body: str) -> list[str]:
    tags = re.findall(r"(?<!\w)#([\w\-\u4e00-\u9fff]+)", body)
    return sorted(set(tags))


def extract_wiki_links(body: str) -> list[str]:
    links = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", body)
    return sorted({link.strip() for link in links if link.strip()})


def extract_markdown_links(body: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for label, url in re.findall(r"\[([^\]]+)\]\(([^)\s]+)\)", body):
        links.append({"label": label.strip(), "url": url.strip()})
    return links


def plain_text_excerpt(body: str, max_chars: int = 1200) -> str:
    text = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars].strip()
