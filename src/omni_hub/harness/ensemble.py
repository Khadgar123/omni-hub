"""Multi-model ensemble generation through ccLoad.

User-chosen self-evolution strategy:  *no weight updates*; instead the same
prompt is fanned out to multiple models and their candidates are stored in a
``GenerationRecord``.  Later stages (judge, human, DSPy compile) consume that
record.

This module is intentionally minimal:

- Uses ``urllib`` from stdlib (the main repository keeps ``dependencies = []``).
- Talks to ``ccLoad`` on ``127.0.0.1:8080`` by default — ccLoad does the
  protocol conversion to Claude / Codex / Gemini / OpenAI-compatible upstreams
  and applies cost/RPM limits.  Each model spec only needs a model name.
- ``api-management/defaults.json`` provides the default base URL and provider.
- ``omni_hub.secrets.load_api_key`` is used to fetch the bearer token; the key
  itself never enters this file.
- Calls are parallelized with ``ThreadPoolExecutor`` so latency is
  ``max(model_i)`` not ``sum(model_i)``.

The candidates returned do NOT yet have ``judge_scores``; that comes from a
separate ``harness.judge_ensemble`` module (next phase).  This module's only
job is faithful fanout and tight error capture.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import Candidate, GenerationRecord


DEFAULT_GATEWAY_URL = "http://127.0.0.1:8080"
DEFAULT_PATH = "/v1/chat/completions"


@dataclass(slots=True)
class ModelSpec:
    """One model to fan out to."""

    name: str
    base_url: str = DEFAULT_GATEWAY_URL
    path: str = DEFAULT_PATH
    secret_ref: str = "local:omni-hub/api/deepseek/default"
    temperature: float = 0.7
    max_tokens: int = 1024
    extra_headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 60.0

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.path}"


@dataclass(slots=True)
class EnsembleConfig:
    """How to run the fanout."""

    models: list[ModelSpec] = field(default_factory=list)
    system_prompt: str = ""
    max_workers: int = 4
    prompt_version: str = "v0"


# ---------------------------------------------------------------------------
# Defaults: read api-management/defaults.json so the harness inherits the
# project-wide provider setup without duplicating configuration.
# ---------------------------------------------------------------------------


def load_default_models(
    workspace: Path | str = ".",
    *,
    extra_models: list[str] | None = None,
) -> list[ModelSpec]:
    """Build a default ModelSpec list from api-management/defaults.json.

    ``extra_models`` may include model names not declared in defaults.json;
    they all inherit the default provider's ``base_url`` and ``secret_ref``.
    """

    root = Path(workspace).resolve()
    defaults_file = root / "api-management" / "defaults.json"
    if not defaults_file.exists():
        return [ModelSpec(name="deepseek-v4-pro")]
    defaults = json.loads(defaults_file.read_text(encoding="utf-8"))
    provider_id = defaults.get("default_provider", "deepseek")
    provider = (defaults.get("providers") or {}).get(provider_id, {})
    secret_ref = provider.get("secret_ref", "local:omni-hub/api/deepseek/default")

    base_url = DEFAULT_GATEWAY_URL  # always prefer ccLoad
    model_names = list(provider.get("models") or [])
    if extra_models:
        for name in extra_models:
            if name not in model_names:
                model_names.append(name)
    if not model_names:
        model_names = [defaults.get("default_model", "deepseek-v4-pro")]

    return [
        ModelSpec(name=name, base_url=base_url, secret_ref=secret_ref)
        for name in model_names
    ]


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


SecretResolver = Callable[[str], str]


def _default_secret_resolver(secret_ref: str) -> str:
    """Resolve ``local:...`` refs via omni_hub.secrets when available.

    Returns ``""`` if the secret backend is missing or the ref is unknown; the
    caller treats that as "no Authorization header" so dry runs against mock
    servers still work.
    """

    if not secret_ref:
        return ""
    try:
        from omni_hub.secrets import resolve_secret_ref  # type: ignore
    except Exception:
        return ""
    try:
        return resolve_secret_ref(secret_ref) or ""
    except Exception:
        return ""


def _build_request(
    spec: ModelSpec,
    prompt: str,
    system_prompt: str,
    secret_resolver: SecretResolver,
) -> Request:
    body = {
        "model": spec.name,
        "messages": (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        )
        + [{"role": "user", "content": prompt}],
        "temperature": spec.temperature,
        "max_tokens": spec.max_tokens,
        "stream": False,
    }
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "omni-hub-harness-ensemble/1.0",
        **spec.extra_headers,
    }
    token = secret_resolver(spec.secret_ref)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return Request(spec.endpoint, data=data, headers=headers, method="POST")


def _extract_text(response_payload: dict[str, Any]) -> str:
    """Pull assistant text from an OpenAI-compatible chat completion payload."""

    choices = response_payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # multimodal content
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


def _call_one(
    spec: ModelSpec,
    prompt: str,
    system_prompt: str,
    secret_resolver: SecretResolver,
    http_call: Callable[[Request, float], dict[str, Any]] | None = None,
) -> Candidate:
    """Issue a single chat completion request, capture errors as failure_tags."""

    candidate = Candidate(model=spec.name)
    start = time.perf_counter()
    try:
        request = _build_request(spec, prompt, system_prompt, secret_resolver)
        if http_call is not None:
            payload = http_call(request, spec.timeout_seconds)
        else:
            with urlopen(request, timeout=spec.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        candidate.text = _extract_text(payload)
        if not candidate.text:
            candidate.failure_tags.append("empty_response")
    except HTTPError as exc:
        candidate.error = f"HTTPError {exc.code}: {exc.reason}"
        candidate.failure_tags.append("http_error")
    except URLError as exc:
        candidate.error = f"URLError: {exc.reason}"
        candidate.failure_tags.append("network_error")
    except json.JSONDecodeError as exc:
        candidate.error = f"JSON decode failed: {exc}"
        candidate.failure_tags.append("invalid_response")
    except Exception as exc:  # pragma: no cover - defensive
        candidate.error = f"{type(exc).__name__}: {exc}"
        candidate.failure_tags.append("unexpected_error")
    finally:
        candidate.elapsed_ms = int((time.perf_counter() - start) * 1000)
    return candidate


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_ensemble(
    prompt: str,
    config: EnsembleConfig,
    *,
    task_id: str = "",
    secret_resolver: SecretResolver | None = None,
    http_call: Callable[[Request, float], dict[str, Any]] | None = None,
) -> GenerationRecord:
    """Fan out ``prompt`` across all models in ``config`` and collect candidates.

    Parameters
    ----------
    prompt:
        The user prompt.  System prompt comes from ``config.system_prompt``.
    config:
        Ensemble configuration; must declare at least one model.
    task_id:
        Optional ``TaskPacket.task_id`` to link this record back.
    secret_resolver:
        Pluggable resolver used in tests to avoid touching the real keychain.
    http_call:
        Pluggable HTTP function used in tests to avoid real network calls.

    Returns
    -------
    GenerationRecord with one ``Candidate`` per model.
    """

    if not config.models:
        raise ValueError("EnsembleConfig.models must contain at least one ModelSpec")

    resolver = secret_resolver or _default_secret_resolver

    record = GenerationRecord(
        task_id=task_id,
        prompt_version=config.prompt_version,
    )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(config.max_workers, len(config.models)))
    ) as pool:
        futures = {
            pool.submit(
                _call_one,
                spec,
                prompt,
                config.system_prompt,
                resolver,
                http_call,
            ): spec
            for spec in config.models
        }
        for future in concurrent.futures.as_completed(futures):
            record.candidates.append(future.result())

    # Stable ordering: by model name then candidate_id so tests are
    # deterministic and downstream diffs stay clean.
    record.candidates.sort(key=lambda c: (c.model, c.candidate_id))
    return record
