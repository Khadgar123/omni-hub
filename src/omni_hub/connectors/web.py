from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .youtube import extract_youtube_video_id

DEFAULT_USER_AGENT = (
    "omni-hub/0.2 (+https://github.com/Khadgar123/omni-hub; personal knowledge capture)"
)


@dataclass(slots=True)
class HTMLMetadata:
    title: str = ""
    description: str = ""
    canonical_url: str = ""


@dataclass(slots=True)
class CapturedResource:
    url: str
    final_url: str
    source_kind: str
    content_type: str
    status_code: int | None
    body: str
    title: str = ""
    description: str = ""
    text: str = ""
    metadata: dict[str, str | bool | int | None] = field(default_factory=dict)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    truncated: bool = False

    def metadata_dict(self) -> dict[str, object]:
        data = asdict(self)
        data.pop("body")
        data["fetched_at"] = self.fetched_at.isoformat()
        data["body_chars"] = len(self.body)
        data["text_chars"] = len(self.text)
        return data


class _HTMLMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.title_parts: list[str] = []
        self.description = ""
        self.og_title = ""
        self.og_description = ""
        self.canonical_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        tag = tag.lower()

        if tag == "title":
            self.in_title = True
            return

        if tag == "meta":
            key = attr_map.get("name", "").lower() or attr_map.get("property", "").lower()
            content = attr_map.get("content", "").strip()
            if key == "description" and not self.description:
                self.description = content
            elif key == "og:title" and not self.og_title:
                self.og_title = content
            elif key == "og:description" and not self.og_description:
                self.og_description = content
            return

        if tag == "link" and attr_map.get("rel", "").lower() == "canonical":
            self.canonical_url = attr_map.get("href", "").strip()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    def result(self) -> HTMLMetadata:
        title = self.og_title or " ".join(self.title_parts)
        description = self.og_description or self.description
        return HTMLMetadata(
            title=_clean_text(title),
            description=_clean_text(description),
            canonical_url=self.canonical_url,
        )


class _HTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = _clean_text(data)
        if text:
            self.parts.append(text)

    def result(self, max_chars: int = 8000) -> str:
        return "\n".join(self.parts)[:max_chars].strip()


def extract_html_metadata(html: str) -> HTMLMetadata:
    parser = _HTMLMetadataParser()
    parser.feed(html)
    return parser.result()


def html_to_text(html: str, *, max_chars: int = 8000) -> str:
    parser = _HTMLTextParser()
    parser.feed(html)
    return parser.result(max_chars=max_chars)


def build_resource_from_body(
    url: str,
    body: str,
    *,
    content_type: str = "text/html",
    final_url: str | None = None,
    status_code: int | None = None,
    truncated: bool = False,
) -> CapturedResource:
    validate_http_url(url)
    if final_url:
        validate_http_url(final_url)

    final_url = final_url or url
    youtube_video_id = extract_youtube_video_id(final_url) or extract_youtube_video_id(url)
    source_kind = "youtube_video" if youtube_video_id else "webpage"
    content_type_lower = content_type.lower()

    title = ""
    description = ""
    canonical_url = ""
    if "html" in content_type_lower or body.lstrip().startswith("<"):
        html_metadata = extract_html_metadata(body)
        title = html_metadata.title
        description = html_metadata.description
        canonical_url = html_metadata.canonical_url
        text = html_to_text(body)
    else:
        text = body[:8000].strip()

    if not title:
        title = youtube_video_id or final_url

    metadata: dict[str, str | bool | int | None] = {
        "canonical_url": canonical_url,
        "youtube_video_id": youtube_video_id,
    }

    return CapturedResource(
        url=url,
        final_url=final_url,
        source_kind=source_kind,
        content_type=content_type,
        status_code=status_code,
        body=body,
        title=title,
        description=description,
        text=text,
        metadata=metadata,
        truncated=truncated,
    )


def fetch_url(
    url: str,
    *,
    timeout_seconds: int = 20,
    max_bytes: int = 2_000_000,
    user_agent: str = DEFAULT_USER_AGENT,
) -> CapturedResource:
    validate_http_url(url)
    request = Request(url, headers={"User-Agent": user_agent})

    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        if truncated:
            raw = raw[:max_bytes]

        content_type = response.headers.get("content-type", "application/octet-stream")
        body = _decode_body(raw, content_type)
        return build_resource_from_body(
            url,
            body,
            content_type=content_type,
            final_url=response.geturl(),
            status_code=response.status,
            truncated=truncated,
        )


def validate_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("only http and https URLs can be captured")


def _decode_body(raw: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([\w.-]+)", content_type, flags=re.IGNORECASE)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        return raw.decode(charset)
    except LookupError:
        return raw.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
