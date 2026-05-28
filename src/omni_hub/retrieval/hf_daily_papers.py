"""HuggingFace Daily Papers — community-curated arXiv subset.

Why over raw arXiv: each paper carries community upvotes + linked code /
models / spaces.  Filters arXiv noise without us having to re-rank.

API: ``https://huggingface.co/api/daily_papers?date=YYYY-MM-DD`` returns
the day's curated list.  Free, no API key, no rate limit documented (low
volume — single user fine).  When ``date`` is omitted, defaults to today.

We use it as the top-priority source for ``ai_progress`` domain because
the community signal is much higher than raw arXiv ordering.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .base import DEFAULT_TIMEOUT_SEC, RetrievalRecord, http_get_json


API_URL = "https://huggingface.co/api/daily_papers"


class HFDailyPapersSource:
    """Search the community-curated HuggingFace daily papers feed.

    Note: the underlying endpoint returns the day's list, not a search.
    We pull a window (default 7 days) and filter client-side by case-
    insensitive substring match on title / authors / abstract.  This is
    cheap because each day has only ~10-30 papers.
    """

    name = "hf_daily_papers"
    tier = 0          # no auth; HF_TOKEN optional for rate-limit headroom

    def __init__(
        self,
        *,
        api_token: str | None = None,
        days_window: int = 7,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_token = api_token or os.environ.get("HF_TOKEN", "")
        self.days_window = days_window
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        if self.api_token:
            return "ok", "HF_TOKEN set"
        return "ok", "anonymous (huggingface.co/api/daily_papers)"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        q_norm = query.strip().lower()
        if not q_norm:
            return []

        headers = (
            {"Authorization": f"Bearer {self.api_token}"} if self.api_token else None
        )
        records: list[RetrievalRecord] = []
        for day_offset in range(self.days_window):
            day_ts = time.time() - day_offset * 86400
            date_str = time.strftime("%Y-%m-%d", time.gmtime(day_ts))
            data: Any = http_get_json(
                API_URL,
                params={"date": date_str},
                headers=headers,
                timeout=self.timeout,
            )
            if not isinstance(data, list):
                continue
            for item in data:
                paper = item.get("paper") if isinstance(item, dict) else None
                if not isinstance(paper, dict):
                    continue
                title = str(paper.get("title", ""))
                authors = [
                    str(a.get("name", "")) for a in (paper.get("authors") or [])
                    if isinstance(a, dict)
                ][:8]
                abstract = str(paper.get("summary", ""))
                arxiv_id = str(paper.get("id", "")).strip()
                upvotes = int(paper.get("upvotes", 0) or 0)
                hay = (
                    title.lower() + " " + abstract.lower() + " "
                    + " ".join(a.lower() for a in authors)
                )
                if q_norm not in hay:
                    continue
                canonical = f"arxiv:{arxiv_id.split('v')[0]}" if arxiv_id else ""
                records.append(RetrievalRecord(
                    source=self.name,
                    title=title,
                    url=f"https://huggingface.co/papers/{arxiv_id}" if arxiv_id else "",
                    snippet=abstract[:500],
                    score=float(upvotes),     # community upvotes — strong signal
                    canonical_id=canonical,
                    metadata={
                        "arxiv_id": arxiv_id,
                        "authors": authors,
                        "upvotes": upvotes,
                        "date_str": date_str,
                        "num_models": int(paper.get("numModels", 0) or 0),
                        "num_datasets": int(paper.get("numDatasets", 0) or 0),
                    },
                ))
                if len(records) >= limit:
                    return records
        return records
