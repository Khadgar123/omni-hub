from __future__ import annotations

from urllib.parse import parse_qs, urlparse

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}


def extract_youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":")[0]

    if host not in YOUTUBE_HOSTS:
        return None

    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/").split("/", maxsplit=1)[0]
        return video_id or None

    if parsed.path == "/watch":
        video_ids = parse_qs(parsed.query).get("v", [])
        return video_ids[0] if video_ids else None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"embed", "live", "shorts"}:
        return parts[1]

    return None


def is_youtube_url(url: str) -> bool:
    return extract_youtube_video_id(url) is not None
