"""HTML extraction patterns lifted from ``kepano/defuddle`` (MIT).

Two small primitives — pure stdlib (regex + html.parser + json) — that
catch the 80% of "Jina/urllib returned suspiciously little text" cases:

1. **``schema_org_sanity_check``** — pull JSON-LD ``articleBody`` from
   ``<script type="application/ld+json">``.  If JSON-LD says the article
   is 1.5× longer than what the DOM-text extractor returned, the DOM
   extractor probably hit a paywall stub / pre-hydration empty div.
   Caller decides whether to re-run with a heavier extractor.

2. **``PER_SITE_REGISTRY``** + ``extract_with_site_override(url, html)``
   — a dict of ``{host: callable}`` that handles a handful of sites
   where generic DOM scoring fails (LinkedIn pulse, Medium clap-walls,
   Reddit old-vs-new, Substack subscriber-only previews).  Add a host
   key + function returning ``(text, status)``; falls through if not
   registered.

Both are deliberately tiny.  Heavy lifting stays in trafilatura (pinned
via ``trafilatura_bridge``) when the user opts in — these defuddle-style
primitives are zero-dep filters / overrides that run before the bridge.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Callable


# ---------------------------------------------------------------------------
# 1. Schema.org JSON-LD sanity check
# ---------------------------------------------------------------------------


# Capture every <script type="application/ld+json"> block.  We tolerate
# whitespace + attribute order; we do NOT tolerate broken JSON.
JSONLD_RE = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)

# Defuddle's threshold: trust JSON-LD only if it's clearly longer than
# what the DOM extractor saw.  1.5× is the published threshold.
SCHEMA_OVERRIDE_RATIO = 1.5


def extract_schema_org_article_body(html: str) -> str:
    """Return the longest ``articleBody`` found across all JSON-LD blocks.

    Empty string when no usable block is present.  We walk every script
    tag because pages often inject 3-4 JSON-LD blocks (NewsArticle,
    BreadcrumbList, Organization, ...).
    """

    if not html:
        return ""
    longest = ""
    for match in JSONLD_RE.finditer(html):
        block = match.group(1).strip()
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for body in _walk_for_article_body(data):
            if len(body) > len(longest):
                longest = body
    return longest


def _walk_for_article_body(node: object) -> list[str]:
    """DFS for any ``articleBody`` string in a parsed JSON-LD subtree.

    ``@graph`` blocks (multi-entity), ``itemListElement`` (BreadcrumbList),
    and nested ``isPartOf`` all surface here.
    """

    out: list[str] = []
    if isinstance(node, dict):
        body = node.get("articleBody")
        if isinstance(body, str) and body.strip():
            out.append(body.strip())
        for value in node.values():
            out.extend(_walk_for_article_body(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(_walk_for_article_body(item))
    return out


def schema_org_sanity_check(
    extracted_text: str,
    html: str,
    *,
    ratio: float = SCHEMA_OVERRIDE_RATIO,
) -> tuple[str, str]:
    """Compare ``extracted_text`` length to JSON-LD ``articleBody`` length.

    Returns ``(verdict, longer_text)``:
      * ``"trust_dom"``  — extracted_text is long enough; no override.
      * ``"trust_schema"`` — JSON-LD is ≥ ratio × extracted; longer_text
        is the JSON-LD body, caller should use it.
      * ``"no_schema"`` — no usable JSON-LD found.
    """

    schema_body = extract_schema_org_article_body(html)
    if not schema_body:
        return "no_schema", ""
    dom_len = len(extracted_text or "")
    schema_len = len(schema_body)
    if schema_len >= max(int(dom_len * ratio), 200):
        return "trust_schema", schema_body
    return "trust_dom", ""


# ---------------------------------------------------------------------------
# 2. Per-site extractor registry
# ---------------------------------------------------------------------------


# A registered extractor takes the full HTML + the final URL and returns
# ``(text, status)`` where status is "ok" | "empty" | "not_applicable".
# Extractors are conservative — they bail to "not_applicable" rather than
# return junk, so the caller can fall through to generic extraction.
SiteExtractor = Callable[[str, str], tuple[str, str]]


def _extract_linkedin_pulse(html: str, url: str) -> tuple[str, str]:
    """LinkedIn Pulse article body lives in <div class="article-content__body">.

    Generic Readability misses it because the page wraps every paragraph
    in nested utility divs.  Pull the body section then strip tags.
    """

    m = re.search(
        r'<div[^>]*class="[^"]*article-content__body[^"]*"[^>]*>(.*?)</div>\s*<div[^>]*class="[^"]*article-content__footer',
        html, re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return "", "not_applicable"
    body_html = m.group(1)
    text = _strip_tags(body_html).strip()
    return (text, "ok") if text else ("", "empty")


def _extract_medium(html: str, url: str) -> tuple[str, str]:
    """Medium articles ship the full body in a ``<script>__APOLLO_STATE__``
    blob *even when* the page renders a clap-wall — fish the markdown out."""

    m = re.search(
        r"window\.__APOLLO_STATE__\s*=\s*({.+?});\s*</script>",
        html, re.DOTALL,
    )
    if not m:
        return "", "not_applicable"
    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError:
        return "", "not_applicable"
    paragraphs: list[str] = []
    for entry in state.values() if isinstance(state, dict) else []:
        if isinstance(entry, dict) and entry.get("__typename") == "Paragraph":
            text = entry.get("text")
            if isinstance(text, str) and text.strip():
                paragraphs.append(text.strip())
    if not paragraphs:
        return "", "empty"
    return "\n\n".join(paragraphs), "ok"


def _extract_substack(html: str, url: str) -> tuple[str, str]:
    """Substack preview pages embed full body in a ``post-content`` div for
    free posts and stub it for subscriber-only.  Extract what's there."""

    m = re.search(
        r'<div[^>]*class="[^"]*post-content[^"]*"[^>]*>(.*?)</div>\s*<div[^>]*class="[^"]*comments-block',
        html, re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return "", "not_applicable"
    text = _strip_tags(m.group(1)).strip()
    if len(text) < 200:
        # Subscriber-only stub
        return "", "empty"
    return text, "ok"


PER_SITE_REGISTRY: dict[str, SiteExtractor] = {
    "www.linkedin.com":  _extract_linkedin_pulse,
    "linkedin.com":      _extract_linkedin_pulse,
    "medium.com":        _extract_medium,
    # Substack hosts each newsletter on its own subdomain; match the suffix
    # rather than every author manually — see ``site_extractor_for(url)``.
}

_SUBSTACK_SUFFIX = ".substack.com"


def site_extractor_for(url: str) -> SiteExtractor | None:
    """Return a registered extractor for ``url``'s host, or None."""

    host = urllib.parse.urlparse(url).hostname or ""
    if host in PER_SITE_REGISTRY:
        return PER_SITE_REGISTRY[host]
    if host.endswith(_SUBSTACK_SUFFIX):
        return _extract_substack
    return None


def extract_with_site_override(
    html: str,
    url: str,
) -> tuple[str, str]:
    """If ``url``'s host has a registered extractor, run it.

    Returns ``(text, status)`` where status is one of:
      ``"ok"`` / ``"empty"`` / ``"not_applicable"`` (no extractor) /
      ``"no_override"`` (host not in registry).
    """

    extractor = site_extractor_for(url)
    if extractor is None:
        return "", "no_override"
    return extractor(html, url)


# ---------------------------------------------------------------------------
# Tiny HTML strip — re-uses stdlib html.parser via a state machine.  We do
# not depend on the project's existing html_to_text helper here to keep
# this module independently importable.
# ---------------------------------------------------------------------------


_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_tags(html: str) -> str:
    """Quick HTML → text.  Sufficient for our per-site extractors which
    already isolated the article subtree."""

    no_tags = _TAG_RE.sub(" ", html)
    return _WHITESPACE_RE.sub(" ", no_tags).strip()
