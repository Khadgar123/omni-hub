"""Hugging Face Hub — models + datasets (checkpoint / dataset audit).

DISTINCT from ``hf_daily_papers`` (the Daily-Papers feed): this hits the
Hub *catalog* API (``huggingface.co/api/models`` + ``/api/datasets``) to
audit released checkpoints and datasets for a paper.  Optional ``HF_TOKEN``
raises rate limits; public listing works anonymously.

* ``retrieve(query)`` — model search (top by downloads), for the cascade.
* ``model_info`` / ``dataset_info`` — on-demand single-artifact detail.
"""

from __future__ import annotations

import os

from .base import DEFAULT_TIMEOUT_SEC, RetrievalError, RetrievalRecord, http_get_json


API_BASE = "https://huggingface.co/api"
MODELS = f"{API_BASE}/models"
DATASETS = f"{API_BASE}/datasets"
HF_SECRET_REF = "local:omni-hub/api/huggingface/token"


def _resolve_hf_token() -> str:
    env = (
        os.environ.get("HF_TOKEN", "").strip()
        or os.environ.get("HUGGING_FACE_HUB_TOKEN", "").strip()
    )
    if env:
        return env
    try:
        from ..secrets import resolve_secret_ref, SecretStoreError
    except ImportError:
        return ""
    try:
        return resolve_secret_ref(HF_SECRET_REF) or ""
    except SecretStoreError:
        return ""
    except Exception:                                            # noqa: BLE001
        return ""


class HFHubSource:
    name = "hf_hub"
    tier = 0          # public models/datasets list anonymously

    def __init__(
        self,
        *,
        token: str | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.token = token if token is not None else _resolve_hf_token()
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def check(self) -> tuple[str, str]:
        if self.token:
            return "ok", "authenticated HF Hub"
        return "ok", "anonymous HF Hub (public models/datasets)"

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
        domain: str = "",
    ) -> list[RetrievalRecord]:
        if not query.strip():
            return []
        try:
            data = http_get_json(
                MODELS,
                params={
                    "search": query,
                    "limit": str(min(limit, 25)),
                    "sort": "downloads",
                    "direction": "-1",
                    "full": "true",
                },
                headers=self._headers(),
                timeout=self.timeout,
            )
        except RetrievalError:
            return []                                  # fail-soft: cascade continues
        items = data if isinstance(data, list) else []
        records: list[RetrievalRecord] = []
        for m in items[:limit]:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or m.get("modelId") or "")
            if not mid:
                continue
            downloads = int(m.get("downloads", 0) or 0)
            likes = int(m.get("likes", 0) or 0)
            pipeline = str(m.get("pipeline_tag", "") or "")
            snippet = f"⬇{downloads} · ♥{likes}"
            if pipeline:
                snippet += f" · {pipeline}"
            records.append(RetrievalRecord(
                source=self.name,
                title=mid,
                url=f"https://huggingface.co/{mid}",
                snippet=snippet[:500],
                score=float(downloads),
                canonical_id=f"hf:{mid.lower()}",
                metadata={
                    "model_id": mid,
                    "downloads": downloads,
                    "likes": likes,
                    "pipeline_tag": pipeline,
                    "library_name": m.get("library_name", "") or "",
                    "tags": m.get("tags", []) or [],
                    "last_modified": m.get("lastModified", "") or "",
                },
            ))
        return records

    def model_info(self, model_id: str) -> dict | None:
        return self._artifact(MODELS, model_id, "model_id")

    def dataset_info(self, dataset_id: str) -> dict | None:
        return self._artifact(DATASETS, dataset_id, "dataset_id")

    def _artifact(self, base: str, ident: str, key: str) -> dict | None:
        ident = str(ident).strip().strip("/")
        if not ident:
            return None
        try:
            data = http_get_json(
                f"{base}/{ident}", headers=self._headers(), timeout=self.timeout,
            )
        except RetrievalError:
            return None
        if not isinstance(data, dict):
            return None
        return {
            key: data.get("id", ident),
            "downloads": int(data.get("downloads", 0) or 0),
            "likes": int(data.get("likes", 0) or 0),
            "pipeline_tag": data.get("pipeline_tag", "") or "",
            "library_name": data.get("library_name", "") or "",
            "tags": data.get("tags", []) or [],
            "last_modified": data.get("lastModified", "") or "",
            "gated": data.get("gated", False),
            "url": f"https://huggingface.co/{data.get('id', ident)}",
        }
