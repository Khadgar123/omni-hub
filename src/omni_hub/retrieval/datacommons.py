"""Data Commons statistical facts.

The adapter intentionally accepts structured queries rather than arbitrary
natural language.  Data Commons is strongest when the caller already knows a
place DCID and stat variable, e.g. ``place=country/USA stat_var=Count_Person``.
"""

from __future__ import annotations

import os
import shlex

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json
from .health import env_var_probe


STAT_SERIES_URL = "https://api.datacommons.org/stat/series"


class DataCommonsSource:
    """Data Commons stat-series lookup. Requires ``DATACOMMONS_API_KEY``."""

    name = "data_commons"
    tier = 1

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("DATACOMMONS_API_KEY", "")
        self.timeout = timeout

    def check(self) -> tuple[str, str]:
        return env_var_probe("DATACOMMONS_API_KEY")

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        if not self.api_key:
            raise RetrievalError("DATACOMMONS_API_KEY not set")

        place, stat_var = _parse_query(query)
        if not place or not stat_var:
            raise RetrievalError(
                "data_commons query must include place=<dcid> and stat_var=<dcid>"
            )

        data = http_get_json(
            STAT_SERIES_URL,
            params={"place": place, "stat_var": stat_var, "key": self.api_key},
            timeout=self.timeout,
        )
        series = data.get("series", {}) if isinstance(data, dict) else {}
        if not isinstance(series, dict) or not series:
            return []
        latest_year = sorted(series.keys())[-1]
        latest_value = series[latest_year]
        title = f"{place} {stat_var}"
        return [RetrievalRecord(
            source=self.name,
            title=title,
            url=f"https://datacommons.org/browser/{place}",
            snippet=f"{stat_var}: {latest_value} ({latest_year})",
            score=float(latest_year) if str(latest_year).isdigit() else 0.0,
            canonical_id=f"dc:{place}:{stat_var}",
            metadata={
                "place": place,
                "stat_var": stat_var,
                "latest_year": latest_year,
                "latest_value": latest_value,
                "series": series,
            },
        )]


def _parse_query(query: str) -> tuple[str, str]:
    values: dict[str, str] = {}
    for token in shlex.split(query):
        if "=" in token:
            key, value = token.split("=", 1)
            values[key.strip().lower()] = value.strip()
    place = values.get("place") or values.get("dcid") or values.get("entity")
    stat_var = values.get("stat_var") or values.get("stat") or values.get("variable")
    if place and stat_var:
        return place, stat_var

    parts = query.split()
    if len(parts) >= 2 and "/" in parts[0] and "_" in parts[1]:
        return parts[0], parts[1]
    return "", ""
