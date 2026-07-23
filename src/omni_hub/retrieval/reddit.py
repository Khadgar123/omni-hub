"""Reddit OAuth search connector.

Replaces / augments TwitterAPI.io for the ``social_en`` domain.  Reddit's
client_credentials OAuth flow is free (no human approval needed) and gives
~100 req/min — plenty for personal research.

Setup (one-time, 2 minutes):

1. Log in at https://www.reddit.com/prefs/apps and click
   "Create another app...".
2. Pick **script** type, fill any name (e.g. "omni-hub"), redirect URI
   ``http://localhost:8080``.
3. Copy the **client_id** (14 chars under the app name) and
   **client_secret** (27 chars).
4. Store both:

       store_api_key('api/reddit/client_id',     '<14 chars>')
       store_api_key('api/reddit/client_secret', '<27 chars>')

   Optionally set ``REDDIT_USER_AGENT`` env (defaults to
   ``omni-hub/0.42 (by /u/anonymous)`` — Reddit requires a UA).

Hard constraint: stdlib only — no PRAW dependency.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord


TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
SEARCH_URL = "https://oauth.reddit.com/search"
DEFAULT_UA = "omni-hub/0.42 (personal research; +https://github.com/Khadgar123)"

REDDIT_CLIENT_ID_REF = "local:omni-hub/api/reddit/client_id"
REDDIT_CLIENT_SECRET_REF = "local:omni-hub/api/reddit/client_secret"


def _resolve_secret(env_var: str, secret_ref: str) -> str:
    val = os.environ.get(env_var, "").strip()
    if val:
        return val
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(secret_ref) or ""
    except SecretStoreError:
        return ""
    except Exception:                                            # noqa: BLE001
        return ""


class RedditSource:
    """Reddit search via client_credentials OAuth."""

    name = "reddit"
    tier = 1

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        user_agent: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.client_id = (
            client_id if client_id is not None
            else _resolve_secret("REDDIT_CLIENT_ID", REDDIT_CLIENT_ID_REF)
        )
        self.client_secret = (
            client_secret if client_secret is not None
            else _resolve_secret("REDDIT_CLIENT_SECRET", REDDIT_CLIENT_SECRET_REF)
        )
        self.user_agent = user_agent or os.environ.get("REDDIT_USER_AGENT") or DEFAULT_UA
        self.timeout = timeout
        self._token: str = ""
        self._token_expires_at: float = 0.0

    def check(self) -> tuple[str, str]:
        if self.client_id and self.client_secret:
            return "ok", "Reddit OAuth credentials configured (~100 req/min)"
        return "warn", "REDDIT_CLIENT_ID/_SECRET not set; register at reddit.com/prefs/apps"

    def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        if not (self.client_id and self.client_secret):
            raise RetrievalError("Reddit OAuth credentials not configured")
        auth = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("ascii"),
        ).decode("ascii")
        body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("ascii")
        req = urllib.request.Request(
            TOKEN_URL,
            data=body,
            headers={
                "Authorization": f"Basic {auth}",
                "User-Agent": self.user_agent,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RetrievalError(f"reddit oauth HTTP {exc.code}: {exc.reason}") from exc
        except Exception as exc:                                  # noqa: BLE001
            raise RetrievalError(f"reddit oauth {type(exc).__name__}: {exc}") from exc

        token = str(payload.get("access_token", ""))
        if not token:
            raise RetrievalError(f"reddit oauth response missing token: {payload}")
        self._token = token
        self._token_expires_at = time.time() + int(payload.get("expires_in", 3600))
        return token

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        token = self._ensure_token()
        params = urllib.parse.urlencode({
            "q": query,
            "limit": str(min(max(limit, 1), 25)),
            "sort": "new",                                  # recency-first; alt: "relevance"
            "type": "link",
            "raw_json": "1",
        })
        req = urllib.request.Request(
            f"{SEARCH_URL}?{params}",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data: Any = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RetrievalError(f"reddit search HTTP {exc.code}: {exc.reason}") from exc
        except Exception as exc:                                  # noqa: BLE001
            raise RetrievalError(f"reddit search {type(exc).__name__}: {exc}") from exc

        if not isinstance(data, dict):
            return []
        children = (data.get("data") or {}).get("children") or []
        records: list[RetrievalRecord] = []
        for entry in children[:limit]:
            post = (entry or {}).get("data") or {}
            if not post:
                continue
            title = str(post.get("title", ""))
            subreddit = str(post.get("subreddit", ""))
            url = "https://www.reddit.com" + str(post.get("permalink", ""))
            selftext = str(post.get("selftext", "")).strip()
            link_url = str(post.get("url", ""))
            records.append(RetrievalRecord(
                source=self.name,
                title=f"[r/{subreddit}] {title}",
                url=url,
                snippet=(selftext or link_url)[:600],
                score=float(post.get("score", 0) or 0) / 1000.0,
                canonical_id=f"reddit:{post.get('id', '')}",
                metadata={
                    "subreddit": subreddit,
                    "author": post.get("author", ""),
                    "score": post.get("score", 0),
                    "num_comments": post.get("num_comments", 0),
                    "created_utc": post.get("created_utc", 0),
                    "link_url": link_url,
                },
            ))
        return records


__all__ = ["RedditSource"]
