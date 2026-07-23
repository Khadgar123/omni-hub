"""YouTube transcript connector — auto-captions for any video.

Unlocks the long-form content that *isn't* in RSS / HN: Lex Fridman
podcast episodes, Dwarkesh interviews, Karpathy lectures, a16z talks,
keynotes, etc.  YouTube's auto-generated (or human-uploaded) captions
are accessible without API key.

Library: ``youtube-transcript-api`` (pip).  Falls back gracefully if not
installed.  Stdlib alone can't do this — YouTube's caption endpoint
requires JS-emulated session.

Usage: pass a video ID or URL as ``query``::

    https://www.youtube.com/watch?v=LCEmiRjPEtQ
    https://youtu.be/LCEmiRjPEtQ
    LCEmiRjPEtQ                                # bare 11-char id

Returns one ``RetrievalRecord`` per video with the full transcript in
``snippet`` (capped at 8k chars; full text in ``metadata['full_text']``).

Caveats:
* YouTube blocks the API from many cloud / VPN IPs in 2026; expect
  retries.  Connector raises RetrievalError on block, cascade fail-soft.
* No search by topic — caller must hand in URL/ID.  Combine with HN /
  Tavily to *find* video URLs, then this connector to *transcribe*.
"""

from __future__ import annotations

import re
from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord


_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})")


def _extract_video_id(query: str) -> str:
    """Normalize a YouTube URL or bare ID → 11-char video ID."""

    q = query.strip()
    if len(q) == 11 and re.fullmatch(r"[A-Za-z0-9_-]{11}", q):
        return q
    m = _VIDEO_ID_RE.search(q)
    return m.group(1) if m else ""


class YouTubeTranscriptSource:
    """YouTube auto-caption transcript via youtube-transcript-api."""

    name = "youtube_transcript"
    tier = 0          # no API key

    def __init__(
        self,
        *,
        languages: list[str] | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        # Preferred language order; library falls back through these.
        self.languages = languages or ["en", "zh-Hans", "zh-Hant", "zh"]
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        try:
            import youtube_transcript_api                          # noqa: F401
            return "ok", "youtube_transcript_api installed (no API key)"
        except ImportError:
            return "off", "youtube-transcript-api not installed; pip install youtube-transcript-api"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 1,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        video_id = _extract_video_id(query)
        if not video_id:
            raise RetrievalError(
                f"could not extract video id from query={query!r}; "
                f"pass URL or 11-char id"
            )
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError as exc:
            raise RetrievalError(
                "youtube-transcript-api not installed (pip install youtube-transcript-api)"
            ) from exc

        try:
            ytt = YouTubeTranscriptApi()
            fetched = ytt.fetch(video_id, languages=self.languages)
        except Exception as exc:                                  # noqa: BLE001
            raise RetrievalError(
                f"youtube transcript failed for {video_id}: {type(exc).__name__}: {exc}"
            ) from exc

        # Result is a FetchedTranscript with .snippets, .language, .video_id
        snippets = list(fetched)                                  # iterable of FetchedTranscriptSnippet
        full_text = " ".join(s.text for s in snippets if s.text).strip()
        if not full_text:
            return []

        # Aggregate-side metadata
        duration = sum(s.duration for s in snippets) if snippets else 0
        first_ts = snippets[0].start if snippets else 0
        language = getattr(fetched, "language", "")
        is_generated = getattr(fetched, "is_generated", None)

        url = f"https://www.youtube.com/watch?v={video_id}"
        title = f"YouTube transcript: {video_id}"
        # Build a short summary from first 500 chars of transcript.
        excerpt = full_text[:8000]

        return [
            RetrievalRecord(
                source=self.name,
                title=title,
                url=url,
                snippet=excerpt,
                score=0.0,
                canonical_id=f"yt:{video_id}",
                metadata={
                    "video_id": video_id,
                    "language": language,
                    "is_auto_generated": is_generated,
                    "duration_sec": round(duration, 1),
                    "snippet_count": len(snippets),
                    "first_timestamp_sec": round(first_ts, 1),
                    "char_count": len(full_text),
                    "full_text": full_text,
                },
            ),
        ]


__all__ = ["YouTubeTranscriptSource"]
