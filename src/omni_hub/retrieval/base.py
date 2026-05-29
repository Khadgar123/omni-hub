"""Common contract for every retrieval source.

A retrieval source is anything that maps a query (or a URL, for the URL→md
fetchers) to a sequence of :class:`RetrievalRecord` results.  Every source
implements the :class:`RetrievalSource` Protocol so the cascade dispatcher
can plug them in interchangeably.

Why stdlib-only: per ``pyproject.toml: dependencies = []``, every connector
must HTTP via ``urllib`` and parse with ``json``/``html.parser``.  Heavy
SDKs (``openalex-py``, ``tavily-python``, ``exa-py``) are wrong for this
project — wrap the HTTP yourself, it's <120 LOC per source.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterable, Protocol


DEFAULT_USER_AGENT = (
    "omni-hub/0.8 personal-knowledge-capture "
    "(+https://github.com/Khadgar123/omni-hub)"
)
DEFAULT_TIMEOUT_SEC = 20


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class RetrievalRecord:
    """One snippet returned by a retrieval source.

    ``score`` is source-relative when emitted by a single connector; after
    ``Cascade.retrieve(fusion="rrf")`` it carries the fused Reciprocal
    Rank Fusion score so cross-source comparison is meaningful.

    ``canonical_id`` is the strong identity used for semantic dedup —
    DOI for papers, arxiv_id for preprints, page_id+lang for Wikipedia,
    URL hash for news.  Lets the cascade fold "same paper on arXiv +
    OpenAlex with different URLs" into one record.

    ``cite_id`` is assigned by the cascade post-fusion (``R1``/``R2``/…)
    so downstream output formatters can render inline ``[1][2]`` citations
    that link back to records.
    """

    source: str                           # connector name (e.g. "openalex")
    title: str
    url: str = ""
    snippet: str = ""
    score: float = 0.0
    fetched_at: str = field(default_factory=_utcnow)
    domain: str = ""                      # which domain_profile this was for
    metadata: dict[str, Any] = field(default_factory=dict)
    canonical_id: str = ""                # DOI / arxiv_id / wp:<lang>:<pid> / ...
    cite_id: str = ""                     # cascade-assigned "R1"/"R2"/... post-fusion

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RetrievalError(RuntimeError):
    """A connector failed (network, parse, rate-limited, auth)."""


class RetrievalSource(Protocol):
    """Anything with a ``name`` and a ``retrieve`` method is a source."""

    name: str

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 10,
        domain: str = "",
    ) -> list[RetrievalRecord]: ...


# ---------------------------------------------------------------------------
# HTTP helpers — every connector uses these so we have ONE place for
# user-agent, timeout, error handling, and rate-limit detection.
# ---------------------------------------------------------------------------


# Provenance: how a record was served. DESCRIPTIVE, kept separate from
# MEASURED quality (source_quality.SourceQualityStore) — a 'fallback' or
# 'cache' record is NOT assumed worse than a 'live'/'primary' one; that is
# the reviewer's '降级源≠劣等' invariant. Values:
#   live     - fetched fresh from the source's primary endpoint
#   fallback - source's primary path failed; a secondary path produced this
#   cache    - served from the local TTL cache, no network call
SERVED_VIA_LIVE = "live"
SERVED_VIA_FALLBACK = "fallback"
SERVED_VIA_CACHE = "cache"


def mark_served_via(records, served_via):
    """Stamp ``metadata['served_via']`` on each record (in place) and return them.

    Connectors with a genuine internal fallback path call this with
    ``SERVED_VIA_FALLBACK`` when their primary path failed, so downstream
    evidence + claims record *how* the data was obtained. The cascade stamps
    'live'/'cache' itself; an explicit value set here is preserved (the
    cascade uses ``setdefault``).
    """
    for rec in records:
        if getattr(rec, "metadata", None) is None:
            rec.metadata = {}
        rec.metadata["served_via"] = served_via
    return records


def http_get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> Any:
    """GET ``url``, return parsed JSON.  Raises :class:`RetrievalError` on failure."""

    if params:
        cleaned_params: dict[str, Any] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                cleaned_params[key] = [str(item) for item in value if item is not None]
            else:
                cleaned_params[key] = str(value)
        qs = urllib.parse.urlencode(
            cleaned_params,
            doseq=True,
        )
        joiner = "&" if "?" in url else "?"
        url = f"{url}{joiner}{qs}"

    req_headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        body_preview = ""
        try:
            body_preview = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # pragma: no cover
            pass
        finally:
            exc.close()  # release the socket/tempfile fp (no ResourceWarning)
        raise RetrievalError(
            f"{url} returned HTTP {exc.code}: {body_preview}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RetrievalError(f"{url} unreachable: {exc.reason}") from exc

    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalError(f"{url} returned non-JSON: {exc}") from exc


def http_get_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    accept: str = "text/plain, text/markdown, */*",
) -> tuple[str, dict[str, str]]:
    """GET ``url`` and return ``(body_text, response_headers)``.

    Used for the URL→markdown fetchers (Jina Reader) that return plain text.
    Returns ``response_headers`` so callers can sniff Content-Type for PDF
    vs HTML branching.
    """

    req_headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": accept}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            response_headers = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        exc.close()  # release the socket/tempfile fp (no ResourceWarning)
        raise RetrievalError(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RetrievalError(f"{url} unreachable: {exc.reason}") from exc

    content_type = response_headers.get("content-type", "")
    encoding = "utf-8"
    if "charset=" in content_type:
        encoding = content_type.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
    try:
        return raw.decode(encoding, errors="replace"), response_headers
    except LookupError:
        return raw.decode("utf-8", errors="replace"), response_headers


def http_post_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any | None = None,
    headers: dict[str, str] | None = None,
    content_type: str = "application/json",
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> Any:
    """POST and parse a JSON response with urllib.

    Two body modes, picked by ``content_type``:
    * ``application/x-www-form-urlencoded`` → ``params`` are form-encoded
      (used for OAuth2 token grants, e.g. ACLED).
    * ``application/json`` (default) → ``json_body`` (falling back to
      ``params``) is JSON-encoded.

    Raises :class:`RetrievalError` on any failure.
    """

    if content_type == "application/x-www-form-urlencoded":
        body = urllib.parse.urlencode(
            {k: v for k, v in (params or {}).items() if v is not None},
        ).encode("ascii")
    else:
        payload = json_body if json_body is not None else (params or {})
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req_headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json",
        "Content-Type": content_type,
    }
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        body_preview = ""
        try:
            body_preview = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # pragma: no cover
            pass
        finally:
            exc.close()  # release the socket/tempfile fp (no ResourceWarning)
        raise RetrievalError(f"{url} returned HTTP {exc.code}: {body_preview}") from exc
    except urllib.error.URLError as exc:
        raise RetrievalError(f"{url} unreachable: {exc.reason}") from exc

    text = data.decode("utf-8", errors="replace")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RetrievalError(f"{url} returned non-JSON: {exc}") from exc


def normalize_records(
    records: Iterable[RetrievalRecord],
    *,
    dedup_by_url: bool = True,
) -> list[RetrievalRecord]:
    """Deduplicate by URL (when ``dedup_by_url``); preserve first occurrence."""

    out: list[RetrievalRecord] = []
    seen_urls: set[str] = set()
    for rec in records:
        if dedup_by_url and rec.url:
            if rec.url in seen_urls:
                continue
            seen_urls.add(rec.url)
        out.append(rec)
    return out
