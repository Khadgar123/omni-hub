"""Connector resilience sweep (v0.49 production hardening).

Every retrieval source must FAIL SOFT: when an upstream API returns a
malformed / unexpected-shape response — the #1 cause of silent breakage when
a provider changes its JSON schema — ``retrieve()`` must return a list or
raise ``RetrievalError``, and NEVER crash the cascade with a bare
KeyError / TypeError / AttributeError.  This sweep mocks the HTTP layer with
common malformed envelopes and asserts that contract for every
``http_get_json``-based connector.
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

# (module, primary Source class) for every http_get_json-based connector.
_JSON_CONNECTORS = [
    ("archive", "InternetArchiveSource"),
    ("bilibili", "BilibiliSource"),
    ("biomedical", "EuropePMCSource"),
    ("bluesky", "BlueskySource"),
    ("business_intel", "OpenCorporatesSource"),
    ("crossref", "CrossrefSource"),
    ("datacommons", "DataCommonsSource"),
    ("finance", "EdgarSource"),
    ("gdelt", "GDELTSource"),
    ("github", "GitHubRepoSource"),
    ("hackernews", "HackerNewsSource"),
    ("hf_daily_papers", "HFDailyPapersSource"),
    ("hf_hub", "HFHubSource"),
    ("legal", "CourtListenerSource"),
    ("mastodon", "MastodonSource"),
    ("openalex", "OpenAlexSource"),
    ("openreview", "OpenReviewSource"),
    ("photo", "UnsplashSource"),
    ("pixabay", "PixabaySource"),
    ("semantic_scholar", "SemanticScholarSource"),
    ("twitterapi_io", "TwitterApiIoSource"),
    ("ucdp", "UCDPSource"),
    ("us_gov", "FederalRegisterSource"),
    ("web_search", "BraveSearchSource"),
    ("wikidata", "WikidataSource"),
    ("wikipedia", "WikipediaSource"),
]

# Common malformed envelopes: empty, null, wrong-type, and the
# "key present but value is null" trap that ``.get(k, [])`` does NOT protect.
_MALFORMED = [
    {},
    None,
    [],
    "not json",
    {"unexpected": "shape"},
]
# Every envelope key any connector reads, set explicitly to null — the trap
# that ``.get(k, [])`` does NOT protect against (default only covers MISSING
# keys, not present-but-null).  Covers all http_get_json connectors.
_MALFORMED += [
    {k: None}
    for k in (
        "results", "data", "items", "message", "hits", "articles", "tweets",
        "search", "posts", "pages", "notes", "photos", "seriess",
        "observations", "bills",
    )
]


class ConnectorResilienceTests(unittest.TestCase):
    def test_fail_soft_on_malformed_response(self) -> None:
        from omni_hub.retrieval.base import RetrievalError

        for mod_name, cls_name in _JSON_CONNECTORS:
            mod = importlib.import_module(f"omni_hub.retrieval.{mod_name}")
            if not hasattr(mod, "http_get_json") or not hasattr(mod, cls_name):
                continue
            Source = getattr(mod, cls_name)
            try:
                src = Source()
            except Exception as exc:  # noqa: BLE001
                self.fail(f"{mod_name}.{cls_name} construction needs args: {exc!r}")
            for bad in _MALFORMED:
                with self.subTest(connector=mod_name, payload=repr(bad)[:30]):
                    with patch.object(mod, "http_get_json", return_value=bad):
                        try:
                            out = src.retrieve("test query", limit=3)
                        except RetrievalError:
                            continue  # acceptable, fail-soft contract
                        self.assertIsInstance(
                            out, list,
                            f"{mod_name} returned non-list on malformed input",
                        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
