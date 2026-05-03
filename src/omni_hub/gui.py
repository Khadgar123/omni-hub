from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import urlparse

from .agent import AgentPlanner, AgentTaskRequest, estimate_input_tokens, task_preview
from .gui_dashboard import INDEX_HTML
from .provider_router import (
    HealthStatus,
    ModelSpec,
    ModelStatus,
    ProviderAccount,
    ProviderHealth,
    ProviderAccountStatus,
    ProviderRouterStore,
    ProjectRouteOverride,
    ProjectRouteProfile,
    RouteAbility,
)


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


PROVIDER_PRESETS: list[dict[str, Any]] = [
    {"name": "OpenRouter", "slug": "openrouter", "base_url": "https://openrouter.ai/api/v1", "secret_ref": "env:OPENROUTER_API_KEY", "rank": 100, "category": "hot"},
    {"name": "AiHubMix", "slug": "aihubmix", "base_url": "https://aihubmix.com/v1", "secret_ref": "env:AIHUBMIX_API_KEY", "rank": 95, "category": "hot"},
    {"name": "CodexOpenAI Official", "slug": "codex-openai", "base_url": "https://api.openai.com/v1", "secret_ref": "env:OPENAI_API_KEY", "rank": 92, "category": "official"},
    {"name": "CodexAzure OpenAI", "slug": "codex-azure-openai", "base_url": "https://YOUR_RESOURCE.openai.azure.com/openai/v1", "secret_ref": "env:AZURE_OPENAI_API_KEY", "rank": 90, "category": "official"},
    {"name": "CrazyRouter", "slug": "crazyrouter", "base_url": "", "secret_ref": "env:CRAZYROUTER_API_KEY", "rank": 88, "category": "hot"},
    {"name": "AICoding", "slug": "aicoding", "base_url": "", "secret_ref": "env:AICODING_API_KEY", "rank": 86, "category": "hot"},
    {"name": "RightCode", "slug": "rightcode", "base_url": "", "secret_ref": "env:RIGHTCODE_API_KEY", "rank": 84, "category": "hot"},
    {"name": "SSSAiCode", "slug": "sssaicode", "base_url": "", "secret_ref": "env:SSSAICODE_API_KEY", "rank": 82, "category": "hot"},
    {"name": "Micu", "slug": "micu", "base_url": "", "secret_ref": "env:MICU_API_KEY", "rank": 80, "category": "hot"},
    {"name": "DMXAPI", "slug": "dmxapi", "base_url": "", "secret_ref": "env:DMXAPI_API_KEY", "rank": 78, "category": "relay"},
    {"name": "TheRouter", "slug": "therouter", "base_url": "", "secret_ref": "env:THEROUTER_API_KEY", "rank": 76, "category": "relay"},
    {"name": "胜算云", "slug": "shengsuanyun", "base_url": "", "secret_ref": "env:SHENGSUANYUN_API_KEY", "rank": 74, "category": "relay"},
    {"name": "优云智算", "slug": "youyun", "base_url": "", "secret_ref": "env:YOUYUN_API_KEY", "rank": 72, "category": "relay"},
    {"name": "PIPELLM", "slug": "pipellm", "base_url": "", "secret_ref": "env:PIPELLM_API_KEY", "rank": 70, "category": "relay"},
    {"name": "PackyCode", "slug": "packycode", "base_url": "", "secret_ref": "env:PACKYCODE_API_KEY", "rank": 68, "category": "relay"},
    {"name": "Cubence", "slug": "cubence", "base_url": "", "secret_ref": "env:CUBENCE_API_KEY", "rank": 66, "category": "relay"},
    {"name": "AIGoCode", "slug": "aigocode", "base_url": "", "secret_ref": "env:AIGOCODE_API_KEY", "rank": 64, "category": "relay"},
    {"name": "CTok.ai", "slug": "ctok", "base_url": "", "secret_ref": "env:CTOK_API_KEY", "rank": 62, "category": "relay"},
    {"name": "LionCCAPI", "slug": "lionccapi", "base_url": "", "secret_ref": "env:LIONCCAPI_API_KEY", "rank": 60, "category": "relay"},
    {"name": "DDSHub", "slug": "ddshub", "base_url": "", "secret_ref": "env:DDSHUB_API_KEY", "rank": 58, "category": "relay"},
    {"name": "E-FlowCode", "slug": "eflowcode", "base_url": "", "secret_ref": "env:EFLOWCODE_API_KEY", "rank": 56, "category": "relay"},
    {"name": "LemonData", "slug": "lemondata", "base_url": "", "secret_ref": "env:LEMONDATA_API_KEY", "rank": 54, "category": "relay"},
    {"name": "AICodeMirror", "slug": "aicodemirror", "base_url": "", "secret_ref": "env:AICODEMIRROR_API_KEY", "rank": 52, "category": "relay"},
    {"name": "自定义配置", "slug": "custom", "base_url": "", "secret_ref": "env:CUSTOM_API_KEY", "rank": 1, "category": "custom"},
]

MODEL_PRESETS: list[dict[str, Any]] = [
    {"alias": "GPT-5.4", "model_id": "gpt-5.4", "provider": "codex-openai", "base_url": "https://api.openai.com/v1", "capabilities": ["text", "tools", "vision"], "rank": 100},
    {"alias": "GPT-5.4 Mini", "model_id": "gpt-5.4-mini", "provider": "codex-openai", "base_url": "https://api.openai.com/v1", "capabilities": ["text", "tools"], "rank": 95},
    {"alias": "Claude Opus", "model_id": "claude-opus-4.5", "provider": "openrouter", "base_url": "https://openrouter.ai/api/v1", "capabilities": ["text", "tools", "vision"], "rank": 94},
    {"alias": "Claude Sonnet", "model_id": "claude-sonnet-4.5", "provider": "openrouter", "base_url": "https://openrouter.ai/api/v1", "capabilities": ["text", "tools", "vision"], "rank": 92},
    {"alias": "Gemini Pro", "model_id": "gemini-3-pro", "provider": "openrouter", "base_url": "https://openrouter.ai/api/v1", "capabilities": ["text", "tools", "vision"], "rank": 90},
    {"alias": "DeepSeek Reasoner", "model_id": "deepseek-reasoner", "provider": "openrouter", "base_url": "https://openrouter.ai/api/v1", "capabilities": ["text", "tools"], "rank": 86},
    {"alias": "Qwen Coder", "model_id": "qwen3-coder", "provider": "openrouter", "base_url": "https://openrouter.ai/api/v1", "capabilities": ["text", "tools"], "rank": 82},
]

MODEL_POOL_PRESETS: list[dict[str, Any]] = [
    {
        "model_id": "text-primary",
        "display_name": "文本主力",
        "capabilities": ["text", "tools"],
        "context_window": 128000,
        "priority": 90,
        "weight": 1.0,
    },
    {
        "model_id": "text-fast",
        "display_name": "快速低价",
        "capabilities": ["text"],
        "context_window": 64000,
        "priority": 70,
        "weight": 2.0,
    },
    {
        "model_id": "vision-primary",
        "display_name": "视觉理解",
        "capabilities": ["text", "vision"],
        "context_window": 128000,
        "priority": 65,
        "weight": 1.0,
    },
    {
        "model_id": "code-primary",
        "display_name": "代码与工具",
        "capabilities": ["text", "tools", "code"],
        "context_window": 128000,
        "priority": 85,
        "weight": 1.0,
    },
    {
        "model_id": "batch-cheap",
        "display_name": "批处理低价",
        "capabilities": ["text", "batch"],
        "context_window": 64000,
        "supports_batch": True,
        "priority": 55,
        "weight": 2.0,
    },
]


def create_gui_server(
    workspace: Path | str = ".",
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    allow_non_localhost: bool = False,
) -> ThreadingHTTPServer:
    if host not in LOOPBACK_HOSTS and not allow_non_localhost:
        raise ValueError(
            "GUI refuses to bind non-localhost host without allow_non_localhost"
        )

    workspace_path = Path(workspace).resolve()

    class Handler(OmniHubGuiHandler):
        workspace = workspace_path

    return ThreadingHTTPServer((host, port), Handler)


def serve_gui(
    workspace: Path | str = ".",
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    allow_non_localhost: bool = False,
) -> None:
    server = create_gui_server(
        workspace,
        host=host,
        port=port,
        allow_non_localhost=allow_non_localhost,
    )
    bound_host, bound_port = server.server_address[:2]
    print(f"Omni Hub GUI running at http://{bound_host}:{bound_port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class OmniHubGuiHandler(BaseHTTPRequestHandler):
    workspace: Path
    server_version = "OmniHubGUI/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_text(INDEX_HTML, content_type="text/html; charset=utf-8")
            return
        if parsed.path == "/api/state":
            self._send_json(build_state(self.workspace))
            return
        if parsed.path == "/api/health":
            self._send_json({"ok": True})
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            output = handle_post(self.workspace, parsed.path, payload)
        except Exception as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json(output)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > 1_000_000:
            raise ValueError("request body is too large")
        raw = self.rfile.read(content_length) if content_length else b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, *, content_type: str, status: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status=int(status))


def build_state(workspace: Path | str) -> dict[str, Any]:
    store = ProviderRouterStore(workspace, create=False)
    return {
        "stats": store.stats(),
        "provider_presets": sorted(
            PROVIDER_PRESETS,
            key=lambda item: (-int(item.get("rank", 0)), str(item.get("name", ""))),
        ),
        "model_presets": sorted(
            MODEL_PRESETS,
            key=lambda item: (-int(item.get("rank", 0)), str(item.get("alias", ""))),
        ),
        "accounts": [account.to_dict() for account in store.list_accounts()],
        "models": [model.to_dict() for model in store.list_models()],
        "abilities": [ability.to_dict() for ability in store.list_abilities()],
        "health": [health.to_dict() for health in store.list_health()],
        "profiles": [profile.to_dict() for profile in store.list_project_profiles()],
        "overrides": [
            override.to_dict() for override in store.list_project_overrides()
        ],
    }


def handle_post(
    workspace: Path | str,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    store = ProviderRouterStore(workspace)
    if path == "/api/providers":
        account = ProviderAccount(
            account_id=_required(payload, "account_id"),
            provider=_required(payload, "provider"),
            name=_required(payload, "name"),
            base_url=_required(payload, "base_url"),
            secret_ref=str(payload.get("secret_ref", "")),
            proxy_url=str(payload.get("proxy_url", "")),
            status=ProviderAccountStatus(str(payload.get("status", "active"))),
            account_group=str(payload.get("account_group", "")),
            notes=str(payload.get("notes", "")),
        )
        return {"account": store.upsert_account(account).to_dict()}

    if path == "/api/model-pool-import":
        account = store.get_account(_required(payload, "account_id"))
        imported = []
        for spec in MODEL_POOL_PRESETS:
            model = ModelSpec(
                model_id=str(spec["model_id"]),
                display_name=str(spec["display_name"]),
                capabilities=_list_value(spec.get("capabilities")),
                context_window=_int_value(spec.get("context_window")),
                supports_batch=bool(spec.get("supports_batch", False)),
                notes="starter model pool; replace provider mapping after live sync",
            )
            stored_model = store.upsert_model(model)
            ability = RouteAbility(
                account_id=account.account_id,
                model_id=stored_model.model_id,
                priority=_int_value(spec.get("priority")),
                weight=_float_value(spec.get("weight"), default=1.0),
                model_mapping=str(spec.get("model_mapping", "")),
                notes="imported from GUI starter model pool",
            )
            imported.append(store.upsert_ability(ability).to_dict())
        return {"account": account.to_dict(), "imported": imported}

    if path == "/api/provider-check":
        account = store.get_account(_required(payload, "account_id"))
        health = _check_provider(account, store)
        return {"health": store.set_health(health).to_dict()}

    if path == "/api/channel-model":
        account_id = _required(payload, "account_id")
        model = ModelSpec(
            model_id=_required(payload, "model_id"),
            display_name=str(payload.get("display_name", "")),
            status=ModelStatus(str(payload.get("status", "active"))),
            capabilities=_list_value(payload.get("capabilities")),
            context_window=_int_value(payload.get("context_window")),
            input_usd_per_million=_float_value(
                payload.get("input_usd_per_million")
            ),
            output_usd_per_million=_float_value(
                payload.get("output_usd_per_million")
            ),
            supports_batch=bool(payload.get("supports_batch", False)),
            notes=str(payload.get("notes", "")),
        )
        stored_model = store.upsert_model(model)
        ability = RouteAbility(
            account_id=account_id,
            model_id=stored_model.model_id,
            enabled=bool(payload.get("enabled", True)),
            priority=_int_value(payload.get("priority"), default=50),
            weight=_float_value(payload.get("weight"), default=1.0),
            model_mapping=str(payload.get("model_mapping", "")),
            notes=str(payload.get("role", "")),
        )
        return {
            "model": stored_model.to_dict(),
            "ability": store.upsert_ability(ability).to_dict(),
        }

    if path == "/api/models":
        model = ModelSpec(
            model_id=_required(payload, "model_id"),
            display_name=str(payload.get("display_name", "")),
            status=ModelStatus(str(payload.get("status", "active"))),
            capabilities=_list_value(payload.get("capabilities")),
            context_window=_int_value(payload.get("context_window")),
            input_usd_per_million=_float_value(
                payload.get("input_usd_per_million")
            ),
            output_usd_per_million=_float_value(
                payload.get("output_usd_per_million")
            ),
            cache_read_usd_per_million=_float_value(
                payload.get("cache_read_usd_per_million")
            ),
            cache_write_usd_per_million=_float_value(
                payload.get("cache_write_usd_per_million")
            ),
            supports_batch=bool(payload.get("supports_batch", False)),
            notes=str(payload.get("notes", "")),
        )
        return {"model": store.upsert_model(model).to_dict()}

    if path == "/api/route-abilities":
        ability = RouteAbility(
            account_id=_required(payload, "account_id"),
            model_id=_required(payload, "model_id"),
            enabled=bool(payload.get("enabled", True)),
            priority=_int_value(payload.get("priority")),
            weight=_float_value(payload.get("weight"), default=1.0),
            model_mapping=str(payload.get("model_mapping", "")),
            notes=str(payload.get("notes", "")),
        )
        return {"ability": store.upsert_ability(ability).to_dict()}

    if path == "/api/project-profiles":
        profile = ProjectRouteProfile(
            project_id=_required(payload, "project_id"),
            default_capabilities=_list_value(payload.get("default_capabilities")),
            max_cost_usd=_float_or_none(payload.get("max_cost_usd")),
            require_batch=bool(payload.get("require_batch", False)),
            preferred_providers=_list_value(payload.get("preferred_providers")),
            preferred_accounts=_list_value(payload.get("preferred_accounts")),
            notes=str(payload.get("notes", "")),
        )
        return {"profile": store.upsert_project_profile(profile).to_dict()}

    if path == "/api/project-routes":
        override = ProjectRouteOverride(
            project_id=_required(payload, "project_id"),
            account_id=_required(payload, "account_id"),
            model_id=_required(payload, "model_id"),
            priority=_int_or_none(payload.get("priority")),
            weight=_float_or_none(payload.get("weight")),
            enabled=bool(payload.get("enabled", True)),
            notes=str(payload.get("notes", "")),
        )
        return {"override": store.upsert_project_override(override).to_dict()}

    if path == "/api/project-group":
        project_id = _required(payload, "project_id")
        routes = payload.get("routes", [])
        if not isinstance(routes, list):
            raise ValueError("routes must be a list")
        stored = []
        for item in routes:
            if not isinstance(item, dict):
                continue
            account_id = str(item.get("account_id", "")).strip()
            model_id = str(item.get("model_id", "")).strip()
            if not account_id or not model_id:
                continue
            override = ProjectRouteOverride(
                project_id=project_id,
                account_id=account_id,
                model_id=model_id,
                priority=_int_or_none(item.get("priority")),
                weight=_float_or_none(item.get("weight")),
                enabled=bool(item.get("enabled", True)),
                notes=_route_note(item),
            )
            stored.append(store.upsert_project_override(override).to_dict())
        return {"project_id": project_id, "routes": stored}

    if path == "/api/agent-plan":
        task = str(payload.get("task", ""))
        request = AgentTaskRequest(
            project_id=str(payload.get("project_id", "")),
            task_preview=task_preview(task),
            task_chars=len(task),
            capabilities=_list_value(payload.get("capabilities")),
            input_tokens=_int_value(
                payload.get("input_tokens"),
                default=estimate_input_tokens(task),
            ),
            output_tokens=_int_value(payload.get("output_tokens")),
            max_cost_usd=_float_or_none(payload.get("max_cost_usd")),
            require_batch=bool(payload.get("require_batch", False)),
            preferred_providers=_list_value(payload.get("preferred_providers")),
            preferred_accounts=_list_value(payload.get("preferred_accounts")),
        )
        return AgentPlanner(workspace).plan(request).to_dict()

    if path == "/api/agent-select":
        mode = str(payload.get("mode", "text"))
        capabilities = {
            "text": ["text"],
            "code": ["text", "tools"],
            "vision": ["text", "vision"],
            "batch": ["text", "batch"],
        }.get(mode, ["text"])
        request = AgentTaskRequest(
            project_id=str(payload.get("project_id", "")),
            task_preview=f"select {mode} model",
            task_chars=0,
            capabilities=capabilities,
            input_tokens=_int_value(payload.get("input_tokens"), default=1000),
            output_tokens=_int_value(payload.get("output_tokens"), default=800),
            max_cost_usd=_float_or_none(payload.get("max_cost_usd")),
            require_batch=mode == "batch" or bool(payload.get("require_batch", False)),
        )
        return AgentPlanner(workspace).plan(request).to_dict()

    raise ValueError(f"unknown endpoint: {path}")


def _required(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _route_note(item: dict[str, Any]) -> str:
    role = str(item.get("role", "")).strip()
    skills = _list_value(item.get("skills"))
    if skills:
        return f"{role}; skills={', '.join(skills)}" if role else f"skills={', '.join(skills)}"
    return role


def _check_provider(
    account: ProviderAccount,
    store: ProviderRouterStore,
) -> ProviderHealth:
    model_count = len(store.list_abilities(account_id=account.account_id))
    if account.status != ProviderAccountStatus.ACTIVE:
        return ProviderHealth(
            account_id=account.account_id,
            status=HealthStatus.DOWN,
            last_error=f"account status is {account.status.value}",
        )
    if not account.secret_ref:
        return ProviderHealth(
            account_id=account.account_id,
            status=HealthStatus.LIMITED,
            last_error="secret_ref is not configured",
        )
    if model_count == 0:
        return ProviderHealth(
            account_id=account.account_id,
            status=HealthStatus.LIMITED,
            last_error="model pool is empty",
        )

    try:
        latency_ms, warning = _probe_base_url(account)
    except Exception as exc:
        return ProviderHealth(
            account_id=account.account_id,
            status=HealthStatus.DEGRADED,
            consecutive_failures=1,
            last_error=str(exc),
        )

    return ProviderHealth(
        account_id=account.account_id,
        status=HealthStatus.HEALTHY if not warning else HealthStatus.DEGRADED,
        latency_ms=latency_ms,
        last_error=warning,
    )


def _probe_base_url(account: ProviderAccount) -> tuple[int | None, str]:
    parsed = urlparse(account.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url is not a valid HTTP URL")

    handler = None
    if account.proxy_url:
        proxy_url = account.proxy_url
        if proxy_url.startswith("env:"):
            proxy_url = os.environ.get(proxy_url.split(":", 1)[1], "")
        if proxy_url:
            handler = ProxyHandler({"http": proxy_url, "https": proxy_url})
    opener = build_opener(handler) if handler else build_opener()
    request = Request(account.base_url, method="HEAD")
    started = monotonic()
    try:
        with opener.open(request, timeout=4) as response:
            response.read(0)
    except HTTPError as exc:
        latency_ms = int((monotonic() - started) * 1000)
        if exc.code < 500:
            return latency_ms, f"reachable with HTTP {exc.code}"
        return latency_ms, f"server returned HTTP {exc.code}"
    except URLError as exc:
        raise ValueError(f"network probe failed: {exc.reason}") from exc
    return int((monotonic() - started) * 1000), ""


def _list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _int_value(value: Any, *, default: int = 0) -> int:
    if value in (None, ""):
        return default
    return int(value)


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _float_value(value: Any, *, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    return float(value)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


LEGACY_INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>万象中枢控制台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f6f8;
      --side: #18202a;
      --side-ink: #edf2f7;
      --side-muted: #9aa7b4;
      --panel: #ffffff;
      --ink: #15212b;
      --muted: #64717f;
      --line: #d9e0e7;
      --blue: #2457d6;
      --green: #13795b;
      --red: #b42318;
      --amber: #936500;
      --teal: #087f8c;
      --soft-blue: #edf3ff;
      --soft-green: #edf8f4;
      --soft-red: #fff1f0;
      --soft-amber: #fff8e6;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }
    .shell {
      min-height: 100vh;
      display: grid;
      grid-template-columns: 248px 1fr;
    }
    aside {
      background: var(--side);
      color: var(--side-ink);
      padding: 18px 14px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
    }
    .brand {
      padding: 4px 8px 16px;
      border-bottom: 1px solid rgba(255,255,255,.12);
      margin-bottom: 12px;
    }
    .brand h1 {
      margin: 0;
      font-size: 18px;
      letter-spacing: 0;
    }
    .brand p {
      margin: 6px 0 0;
      color: var(--side-muted);
      font-size: 12px;
    }
    nav {
      display: grid;
      gap: 4px;
    }
    .nav-item {
      width: 100%;
      min-height: 38px;
      border: 0;
      border-radius: 7px;
      padding: 0 10px;
      background: transparent;
      color: var(--side-muted);
      text-align: left;
      font: inherit;
      cursor: pointer;
    }
    .nav-item.active {
      background: #263344;
      color: #fff;
    }
    .main {
      min-width: 0;
      display: grid;
      grid-template-rows: auto 1fr;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 5;
      background: rgba(255,255,255,.94);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(12px);
    }
    .topbar {
      padding: 14px 22px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    .title h2 {
      margin: 0;
      font-size: 18px;
      letter-spacing: 0;
    }
    .title p {
      margin: 3px 0 0;
      color: var(--muted);
      font-size: 12px;
    }
    .content {
      padding: 20px 22px 34px;
      display: grid;
      gap: 18px;
      max-width: 1500px;
      width: 100%;
    }
    .view { display: none; gap: 18px; }
    .view.active { display: grid; }
    .notice {
      border: 1px solid #b7c6e5;
      background: var(--soft-blue);
      color: #19376d;
      border-radius: 8px;
      padding: 12px 14px;
    }
    .toast {
      position: fixed;
      right: 18px;
      bottom: 18px;
      z-index: 20;
      min-width: 240px;
      max-width: min(420px, calc(100vw - 36px));
      padding: 12px 14px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      box-shadow: 0 12px 32px rgba(21, 33, 43, .18);
      opacity: 0;
      transform: translateY(8px);
      pointer-events: none;
      transition: opacity .16s ease, transform .16s ease;
    }
    .toast.show {
      opacity: 1;
      transform: translateY(0);
    }
    .toast.ok { border-color: #bfe3d4; }
    .toast.bad { border-color: #f5c5c0; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(6, minmax(126px, 1fr));
      gap: 10px;
    }
    .metric, .panel, .split-panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .metric {
      min-height: 82px;
      padding: 12px;
    }
    .metric span {
      color: var(--muted);
      font-size: 12px;
    }
    .metric strong {
      display: block;
      margin-top: 7px;
      font-size: 24px;
      line-height: 1;
    }
    .panel-head {
      min-height: 52px;
      padding: 13px 15px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .panel-head h3 {
      margin: 0;
      font-size: 15px;
      letter-spacing: 0;
    }
    .subtle { color: var(--muted); }
    .quick-grid, .preset-grid, .check-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(160px, 1fr));
      gap: 10px;
    }
    .preset-grid, .check-grid { grid-column: 1 / -1; }
    .choice-button, .action-button {
      height: auto;
      min-height: 58px;
      padding: 11px 12px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      text-align: left;
      display: grid;
      gap: 4px;
    }
    .choice-button.active {
      border-color: #7da2ee;
      background: var(--soft-blue);
    }
    .choice-button strong, .action-button strong {
      display: block;
      font-size: 14px;
    }
    .choice-button span, .action-button span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .side-stack {
      min-width: 0;
      display: grid;
      gap: 12px;
      align-content: start;
    }
    .advanced {
      grid-column: 1 / -1;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 9px 10px;
      background: #fbfcfd;
    }
    .advanced summary {
      cursor: pointer;
      color: var(--ink);
      font-weight: 600;
    }
    .advanced-grid {
      margin-top: 10px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .split-panel {
      display: grid;
      grid-template-columns: minmax(320px, 390px) minmax(0, 1fr);
      overflow: hidden;
    }
    form {
      padding: 15px;
      border-right: 1px solid var(--line);
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      align-content: start;
    }
    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      min-width: 0;
    }
    label.wide, textarea, .form-help, button.primary { grid-column: 1 / -1; }
    input, select, textarea {
      width: 100%;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 9px;
      color: var(--ink);
      background: #fff;
      font: inherit;
      letter-spacing: 0;
    }
    textarea {
      min-height: 128px;
      resize: vertical;
    }
    input[type="checkbox"] {
      width: auto;
      justify-self: start;
    }
    .check-grid label {
      min-height: 36px;
      grid-template-columns: auto 1fr;
      align-items: center;
      gap: 8px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 7px;
      color: var(--ink);
      background: #fff;
    }
    button {
      height: 36px;
      border-radius: 6px;
      font: inherit;
      cursor: pointer;
    }
    button.primary {
      border: 1px solid #1f4fc4;
      background: var(--blue);
      color: #fff;
      font-weight: 600;
    }
    button.secondary {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      padding: 0 12px;
    }
    .form-help {
      padding: 9px 10px;
      border-radius: 6px;
      background: #f8fafc;
      color: var(--muted);
      border: 1px solid var(--line);
      font-size: 12px;
    }
    .table-wrap { min-width: 0; }
    .table-tools {
      padding: 10px 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfd;
    }
    .table-tools input {
      max-width: 320px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 10px 12px;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      background: #fff;
    }
    .table-scroll {
      max-height: 520px;
      overflow: auto;
    }
    .pager {
      min-height: 44px;
      padding: 8px 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .pager .buttons {
      display: flex;
      gap: 8px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      border: 1px solid var(--line);
      background: #fff;
      white-space: nowrap;
    }
    .ok { color: var(--green); background: var(--soft-green); border-color: #bfe3d4; }
    .bad { color: var(--red); background: var(--soft-red); border-color: #f5c5c0; }
    .warn { color: var(--amber); background: var(--soft-amber); border-color: #efd492; }
    .info { color: var(--teal); background: #eaf7f8; border-color: #b8dde2; }
    pre {
      margin: 0;
      padding: 16px;
      min-height: 360px;
      max-height: 620px;
      overflow: auto;
      background: #101820;
      color: #e7edf3;
      font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .guide {
      display: grid;
      grid-template-columns: repeat(3, minmax(180px, 1fr));
      gap: 12px;
    }
    .guide-item {
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }
    .guide-item h4 {
      margin: 0 0 8px;
      font-size: 14px;
    }
    .guide-item p {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
    }
    @media (max-width: 980px) {
      .shell { grid-template-columns: 1fr; }
      aside { position: static; height: auto; }
      nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .metrics { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
      .split-panel { grid-template-columns: 1fr; }
      form { border-right: 0; border-bottom: 1px solid var(--line); }
      .guide { grid-template-columns: 1fr; }
      .quick-grid, .preset-grid, .check-grid { grid-template-columns: 1fr; }
      .advanced-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="brand">
        <h1>万象中枢</h1>
        <p>本地 AI 接入与调度控制台</p>
      </div>
      <nav>
        <button class="nav-item" data-view="overview">总览</button>
        <button class="nav-item" data-view="connectors">API 接入</button>
        <button class="nav-item" data-view="routing">路由策略</button>
        <button class="nav-item" data-view="projects">项目偏好</button>
        <button class="nav-item" data-view="preview">调用预演</button>
      </nav>
    </aside>

    <div class="main">
      <header>
        <div class="topbar">
          <div class="title">
            <h2 id="page-title">总览</h2>
            <p id="page-subtitle">本机 API 接入、策略和调用预演状态。</p>
          </div>
          <button class="secondary" id="refresh">刷新数据</button>
        </div>
      </header>

      <main class="content">
        <section class="view" data-view-panel="overview">
          <div class="notice">万象中枢先把 API 渠道、模型来源、项目偏好和调用预演串起来。模型目录不需要手填，后续由厂商接口、价格表和调用日志自动补全。</div>
          <div class="metrics" id="metrics"></div>
          <div class="quick-grid">
            <button type="button" class="action-button" data-jump="connectors"><strong>接入 API 渠道</strong><span>官方 API 或 OpenAI 兼容中转站</span></button>
            <button type="button" class="action-button" data-jump="routing"><strong>设置默认策略</strong><span>决定平时优先走哪个通道和模型</span></button>
            <button type="button" class="action-button" data-jump="preview"><strong>预演一次调用</strong><span>花钱前先看会选中哪个模型</span></button>
          </div>
          <div class="panel">
            <div class="panel-head"><h3>当前接入</h3><span class="subtle">API 渠道与项目偏好</span></div>
            <div id="overviewTable"></div>
          </div>
        </section>

        <section class="view" data-view-panel="connectors">
          <div class="split-panel">
            <form data-endpoint="/api/providers" data-success="API 渠道已保存">
              <div class="form-help">API 渠道就是一个可调用入口：官方 API、中转站、公司网关或本地网关都算。这里只保存密钥引用，不输入原始 key。</div>
              <div class="preset-grid">
                <button type="button" class="choice-button" data-preset="openai"><strong>OpenAI 官方</strong><span>官方接口</span></button>
                <button type="button" class="choice-button active" data-preset="openrouter"><strong>OpenRouter</strong><span>聚合中转</span></button>
                <button type="button" class="choice-button" data-preset="deepseek"><strong>DeepSeek</strong><span>OpenAI 兼容</span></button>
                <button type="button" class="choice-button" data-preset="siliconflow"><strong>SiliconFlow</strong><span>国内中转</span></button>
                <button type="button" class="choice-button" data-preset="custom"><strong>自定义中转</strong><span>任何 OpenAI 兼容地址</span></button>
              </div>
              <label>渠道名<input id="connector-account" name="account_id" value="openrouter-main" placeholder="openrouter-main" required></label>
              <label>密钥引用<input id="connector-secret" name="secret_ref" value="env:OPENROUTER_API_KEY" placeholder="env:OPENROUTER_API_KEY"></label>
              <label class="wide">显示名称<input id="connector-name" name="name" value="OpenRouter 主渠道" placeholder="OpenRouter 主渠道" required></label>
              <label class="wide">模型获取方式<select id="model-source"><option>优先调用 /models 或厂商模型列表</option><option>没有接口时从价格表和调用日志估算</option><option>稍后从文件批量导入</option></select></label>
              <input name="status" type="hidden" value="active">
              <input name="account_group" type="hidden" value="default">
              <details class="advanced">
                <summary>高级配置</summary>
                <div class="advanced-grid">
                  <label class="wide">Base URL<input id="connector-base" name="base_url" value="https://openrouter.ai/api/v1" placeholder="https://openrouter.ai/api/v1" required></label>
                  <label>Provider 标识<input id="connector-provider" name="provider" value="openrouter" placeholder="openrouter" required></label>
                </div>
              </details>
              <button class="primary">保存 API 渠道</button>
            </form>
            <div class="side-stack">
              <div id="accountsTable"></div>
              <div class="table-wrap">
                <div class="panel-head"><h3>已知模型</h3><button type="button" class="secondary" data-action="refresh-models">刷新模型列表</button></div>
                <div id="modelsTable"></div>
              </div>
            </div>
          </div>
        </section>

        <section class="view" data-view-panel="routing">
          <div class="split-panel">
            <form data-endpoint="/api/route-abilities" data-success="默认路由策略已保存">
              <div class="form-help">默认策略用于没有指定项目时的选择顺序。它决定哪个 API 渠道和模型先被尝试，失败后再换下一个。</div>
              <div class="preset-grid">
                <button type="button" class="choice-button active" data-route-preset="reliable"><strong>优先可靠</strong><span>高优先级，低随机权重</span></button>
                <button type="button" class="choice-button" data-route-preset="cheap"><strong>优先便宜</strong><span>中等优先级，高权重</span></button>
                <button type="button" class="choice-button" data-route-preset="fallback"><strong>备用通道</strong><span>主通道挂了再用</span></button>
              </div>
              <label>API 渠道<select name="account_id" data-account-select required></select></label>
              <label>模型<select name="model_id" data-model-select required></select></label>
              <input id="route-priority" name="priority" type="hidden" value="90">
              <input id="route-weight" name="weight" type="hidden" value="1">
              <input name="enabled" type="hidden" value="true">
              <details class="advanced">
                <summary>高级配置</summary>
                <div class="advanced-grid">
                  <label>优先级<input id="route-priority-visible" type="number" step="1" value="90"></label>
                  <label>权重<input id="route-weight-visible" type="number" min="0" step="0.1" value="1"></label>
                  <label class="wide">Provider 侧模型名<input name="model_mapping" placeholder="如果不同于内部模型 ID"></label>
                </div>
              </details>
              <button class="primary">保存默认策略</button>
            </form>
            <div id="abilitiesTable"></div>
          </div>
        </section>

        <section class="view" data-view-panel="projects">
          <div class="split-panel">
            <form data-endpoint="/api/project-profiles" data-success="项目偏好已保存">
              <div class="form-help">项目偏好用于区分不同项目的默认能力、预算上限和是否要求 batch。比如论文写作、视频理解、自动驾驶研究可以走不同策略。</div>
              <label class="wide">项目 ID<input name="project_id" placeholder="auto-driving-research" required></label>
              <div class="check-grid">
                <label><input type="checkbox" data-list-target="default_capabilities" data-list-value="text" checked>文本</label>
                <label><input type="checkbox" data-list-target="default_capabilities" data-list-value="tools">工具调用</label>
                <label><input type="checkbox" data-list-target="default_capabilities" data-list-value="vision">视觉</label>
                <label><input type="checkbox" data-list-target="default_capabilities" data-list-value="batch">批处理</label>
              </div>
              <label>预算上限<input name="max_cost_usd" type="number" min="0" step="0.0001" placeholder="0.02"></label>
              <label>要求 Batch<input name="require_batch" type="checkbox"></label>
              <details class="advanced">
                <summary>高级配置</summary>
                <div class="advanced-grid">
                  <label class="wide">偏好 Provider<input name="preferred_providers" data-list placeholder="openai, openrouter"></label>
                  <label class="wide">偏好渠道<input name="preferred_accounts" data-list placeholder="openrouter-main"></label>
                </div>
              </details>
              <button class="primary">保存项目偏好</button>
            </form>
            <div id="profilesTable"></div>
          </div>
          <div class="split-panel">
            <form data-endpoint="/api/project-routes" data-success="项目专属优先级已保存">
              <div class="form-help">项目专属优先级只在该项目内生效，用来让重要项目优先走更稳或更强的模型。</div>
              <label>项目<select name="project_id" data-project-select required></select></label>
              <label>API 渠道<select name="account_id" data-account-select required></select></label>
              <label>模型<select name="model_id" data-model-select required></select></label>
              <label>优先级<input name="priority" type="number" step="1" value="80"></label>
              <input name="weight" type="hidden" value="1">
              <input name="enabled" type="hidden" value="true">
              <button class="primary">保存项目优先级</button>
            </form>
            <div id="overridesTable"></div>
          </div>
        </section>

        <section class="view" data-view-panel="preview">
          <div class="split-panel">
            <form data-endpoint="/api/agent-plan" data-success="调用预演完成">
              <div class="form-help">调用预演是花钱前的 dry run：它只告诉你当前策略会选哪个 API 渠道和模型，不真实请求外部模型。</div>
              <label>项目<select name="project_id" data-project-select data-allow-empty="1"></select></label>
              <label>输出 token<input name="output_tokens" type="number" min="0" step="1" value="800"></label>
              <label class="wide">任务<textarea name="task" placeholder="帮我整理这个项目的上下文"></textarea></label>
              <div class="check-grid">
                <label><input type="checkbox" data-list-target="capabilities" data-list-value="text" checked>文本</label>
                <label><input type="checkbox" data-list-target="capabilities" data-list-value="tools">工具调用</label>
                <label><input type="checkbox" data-list-target="capabilities" data-list-value="vision">视觉</label>
                <label><input type="checkbox" data-list-target="capabilities" data-list-value="batch">批处理</label>
              </div>
              <label>预算上限<input name="max_cost_usd" type="number" min="0" step="0.0001" placeholder="0.02"></label>
              <label>要求 Batch<input name="require_batch" type="checkbox"></label>
              <button class="primary">预演调用</button>
            </form>
            <pre id="agent-result">{}</pre>
          </div>
        </section>
      </main>
    </div>
  </div>

  <div id="toast" class="toast" role="status" aria-live="polite"></div>

  <script>
    const PAGE_SIZE = 8;
    const views = {
      overview: ['总览', '本机 API 接入、策略和调用预演状态。'],
      connectors: ['API 接入', '用模板接入官方 API 或 OpenAI 兼容中转站。'],
      routing: ['路由策略', '设置默认 API 渠道、模型和失败切换顺序。'],
      projects: ['项目偏好', '为不同项目设置预算、能力和专属优先级。'],
      preview: ['调用预演', '真实调用前先检查会选中哪个 API 渠道和模型。']
    };
    const state = { data: null, view: 'overview' };
    const tableState = {};
    const presets = {
      openai: {
        account_id: 'openai-main',
        provider: 'openai',
        name: 'OpenAI 官方',
        base_url: 'https://api.openai.com/v1',
        secret_ref: 'env:OPENAI_API_KEY'
      },
      openrouter: {
        account_id: 'openrouter-main',
        provider: 'openrouter',
        name: 'OpenRouter 主渠道',
        base_url: 'https://openrouter.ai/api/v1',
        secret_ref: 'env:OPENROUTER_API_KEY'
      },
      deepseek: {
        account_id: 'deepseek-main',
        provider: 'deepseek',
        name: 'DeepSeek 主渠道',
        base_url: 'https://api.deepseek.com',
        secret_ref: 'env:DEEPSEEK_API_KEY'
      },
      siliconflow: {
        account_id: 'siliconflow-main',
        provider: 'siliconflow',
        name: 'SiliconFlow 主渠道',
        base_url: 'https://api.siliconflow.cn/v1',
        secret_ref: 'env:SILICONFLOW_API_KEY'
      },
      custom: {
        account_id: 'custom-gateway',
        provider: 'openai-compatible',
        name: '自定义中转站',
        base_url: 'https://example.com/v1',
        secret_ref: 'env:CUSTOM_API_KEY'
      }
    };
    const endpointSuccess = {
      '/api/providers': 'API 渠道已保存',
      '/api/route-abilities': '默认路由策略已保存',
      '/api/project-profiles': '项目偏好已保存',
      '/api/project-routes': '项目专属优先级已保存',
      '/api/agent-plan': '调用预演完成'
    };

    const api = async (url, options = {}) => {
      const res = await fetch(url, options);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || '请求失败');
      return data;
    };
    const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
    const escapeAttr = (value) => escapeHtml(value).replace(/`/g, '&#96;');
    const listValue = (value) => value.split(',').map(v => v.trim()).filter(Boolean);
    function showToast(message, tone = 'ok') {
      const toast = document.getElementById('toast');
      toast.textContent = message;
      toast.className = `toast show ${tone}`;
      clearTimeout(showToast.timer);
      showToast.timer = setTimeout(() => toast.className = 'toast', 2400);
    }
    const formPayload = (form) => {
      const out = {};
      for (const el of form.elements) {
        if (el.dataset.listTarget) {
          if (el.checked) {
            out[el.dataset.listTarget] = out[el.dataset.listTarget] || [];
            out[el.dataset.listTarget].push(el.dataset.listValue);
          }
          continue;
        }
        if (!el.name) continue;
        if (el.type === 'checkbox') out[el.name] = el.checked;
        else if (el.dataset.list !== undefined) out[el.name] = listValue(el.value);
        else out[el.name] = el.value;
      }
      return out;
    };
    const statusPill = (value) => {
      const text = String(value || '');
      const cls = text === 'active' || text === 'healthy' ? 'ok' : (text === 'disabled' || text === 'down' ? 'bad' : 'warn');
      return `<span class="pill ${cls}">${escapeHtml(text)}</span>`;
    };
    const boolPill = (value) => `<span class="pill ${value ? 'ok' : 'bad'}">${value ? '启用' : '停用'}</span>`;
    const jsonSearch = (row) => JSON.stringify(row).toLowerCase();

    function setView(view) {
      state.view = views[view] ? view : 'overview';
      location.hash = state.view;
      document.querySelectorAll('[data-view-panel]').forEach(panel => {
        panel.classList.toggle('active', panel.dataset.viewPanel === state.view);
      });
      document.querySelectorAll('[data-view]').forEach(button => {
        button.classList.toggle('active', button.dataset.view === state.view);
      });
      document.getElementById('page-title').textContent = views[state.view][0];
      document.getElementById('page-subtitle').textContent = views[state.view][1];
    }

    function renderDataTable(id, title, columns, rows) {
      const table = tableState[id] || {page: 1, query: ''};
      tableState[id] = table;
      const query = table.query.trim().toLowerCase();
      const filtered = query ? rows.filter(row => jsonSearch(row).includes(query)) : rows;
      const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
      table.page = Math.min(Math.max(table.page, 1), pages);
      const start = (table.page - 1) * PAGE_SIZE;
      const visible = filtered.slice(start, start + PAGE_SIZE);
      const body = visible.length
        ? visible.map(row => '<tr>' + columns.map(col => `<td>${col.render ? col.render(row) : escapeHtml(row[col.key])}</td>`).join('') + '</tr>').join('')
        : `<tr><td colspan="${columns.length}" class="subtle">暂无数据</td></tr>`;
      document.getElementById(id).innerHTML = `
        <div class="table-tools">
          <div><strong>${escapeHtml(title)}</strong> <span class="subtle">${filtered.length} 条</span></div>
          <input data-table-search="${id}" value="${escapeAttr(table.query)}" placeholder="搜索当前表格">
        </div>
        <div class="table-scroll">
          <table>
            <thead><tr>${columns.map(col => `<th>${escapeHtml(col.label)}</th>`).join('')}</tr></thead>
            <tbody>${body}</tbody>
          </table>
        </div>
        <div class="pager">
          <span class="subtle">第 ${table.page} / ${pages} 页</span>
          <div class="buttons">
            <button class="secondary" data-page="${id}" data-dir="-1">上一页</button>
            <button class="secondary" data-page="${id}" data-dir="1">下一页</button>
          </div>
        </div>`;
    }

    function renderMetrics(stats) {
      const labels = {
        provider_accounts: 'API 渠道',
        model_catalog: '已知模型',
        route_abilities: '默认策略',
        project_route_profiles: '项目偏好',
        project_route_overrides: '项目优先级',
        provider_health: '健康记录',
        usage_request_logs: '调用日志'
      };
      document.getElementById('metrics').innerHTML = Object.entries(labels).map(([key, label]) => (
        `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(stats[key] || 0)}</strong></div>`
      )).join('');
    }

    function renderSelectors(data) {
      const accountOptions = data.accounts.map(account => (
        `<option value="${escapeAttr(account.account_id)}">${escapeHtml(account.name || account.account_id)}</option>`
      )).join('');
      const modelOptions = data.models.map(model => (
        `<option value="${escapeAttr(model.model_id)}">${escapeHtml(model.display_name || model.model_id)}</option>`
      )).join('');
      const projectOptions = data.profiles.map(profile => (
        `<option value="${escapeAttr(profile.project_id)}">${escapeHtml(profile.project_id)}</option>`
      )).join('');
      document.querySelectorAll('[data-account-select]').forEach(select => {
        const current = select.value;
        select.innerHTML = accountOptions || '<option value="">先添加 API 渠道</option>';
        if (current) select.value = current;
      });
      document.querySelectorAll('[data-model-select]').forEach(select => {
        const current = select.value;
        select.innerHTML = modelOptions || '<option value="">等待模型同步</option>';
        if (current) select.value = current;
      });
      document.querySelectorAll('[data-project-select]').forEach(select => {
        const current = select.value;
        const empty = select.dataset.allowEmpty ? '<option value="">不指定项目</option>' : '<option value="">先保存项目偏好</option>';
        select.innerHTML = empty + projectOptions;
        if (current) select.value = current;
      });
    }

    function render() {
      const data = state.data;
      if (!data) return;
      renderMetrics(data.stats || {});
      renderSelectors(data);
      renderDataTable('overviewTable', '概览', [
        {label: '类型', render: r => `<span class="pill info">${escapeHtml(r.type)}</span>`},
        {label: '主键', key: 'id'},
        {label: '说明', key: 'detail'},
        {label: '状态', render: r => r.status ? statusPill(r.status) : ''}
      ], [
        ...data.accounts.slice(0, 5).map(r => ({type: 'API 渠道', id: r.account_id, detail: `${r.name || r.provider} · ${r.base_url}`, status: r.status})),
        ...data.profiles.slice(0, 5).map(r => ({type: '项目偏好', id: r.project_id, detail: `能力: ${(r.default_capabilities || []).join(', ') || '未设置'}`, status: r.max_cost_usd ? `预算 ${r.max_cost_usd}` : ''}))
      ]);
      renderDataTable('accountsTable', 'API 渠道', [
        {key: 'account_id', label: '渠道'},
        {key: 'provider', label: 'Provider'},
        {label: '状态', render: r => statusPill(r.status)},
        {key: 'base_url', label: 'Base URL'},
        {key: 'secret_ref', label: '密钥引用'}
      ], data.accounts);
      renderDataTable('modelsTable', '只读模型目录', [
        {key: 'model_id', label: '模型'},
        {label: '状态', render: r => statusPill(r.status)},
        {label: '能力', render: r => escapeHtml((r.capabilities || []).join(', '))},
        {key: 'context_window', label: '上下文'},
        {key: 'input_usd_per_million', label: '输入成本'},
        {key: 'output_usd_per_million', label: '输出成本'},
        {label: 'Batch', render: r => boolPill(r.supports_batch)}
      ], data.models);
      renderDataTable('abilitiesTable', '默认路由策略', [
        {key: 'account_id', label: 'API 渠道'},
        {key: 'model_id', label: '模型'},
        {label: '启用', render: r => boolPill(r.enabled)},
        {key: 'priority', label: '优先级'},
        {key: 'weight', label: '权重'},
        {key: 'model_mapping', label: 'Provider 模型名'}
      ], data.abilities);
      renderDataTable('profilesTable', '项目偏好', [
        {key: 'project_id', label: '项目'},
        {label: '默认能力', render: r => escapeHtml((r.default_capabilities || []).join(', '))},
        {key: 'max_cost_usd', label: '预算上限'},
        {label: '偏好 Provider', render: r => escapeHtml((r.preferred_providers || []).join(', '))},
        {label: '偏好渠道', render: r => escapeHtml((r.preferred_accounts || []).join(', '))}
      ], data.profiles);
      renderDataTable('overridesTable', '项目专属优先级', [
        {key: 'project_id', label: '项目'},
        {key: 'account_id', label: 'API 渠道'},
        {key: 'model_id', label: '模型'},
        {label: '启用', render: r => boolPill(r.enabled)},
        {key: 'priority', label: '优先级'},
        {key: 'weight', label: '权重'}
      ], data.overrides);
    }

    async function refresh(showMessage = false) {
      state.data = await api('/api/state');
      render();
      if (showMessage) showToast('数据已刷新');
    }

    document.querySelectorAll('[data-view]').forEach(button => {
      button.addEventListener('click', () => setView(button.dataset.view));
    });
    document.getElementById('refresh').addEventListener('click', () => refresh(true));
    window.addEventListener('hashchange', () => setView(location.hash.replace('#', '') || 'overview'));

    document.addEventListener('click', event => {
      const jump = event.target.closest('[data-jump]');
      if (!jump) return;
      setView(jump.dataset.jump);
      showToast(`已进入${views[state.view][0]}`);
    });
    document.addEventListener('click', event => {
      const presetButton = event.target.closest('[data-preset]');
      if (!presetButton) return;
      const preset = presets[presetButton.dataset.preset];
      if (!preset) return;
      document.getElementById('connector-account').value = preset.account_id;
      document.getElementById('connector-provider').value = preset.provider;
      document.getElementById('connector-name').value = preset.name;
      document.getElementById('connector-base').value = preset.base_url;
      document.getElementById('connector-secret').value = preset.secret_ref;
      document.querySelectorAll('[data-preset]').forEach(button => {
        button.classList.toggle('active', button === presetButton);
      });
      showToast(`已套用${preset.name}模板`);
    });
    document.addEventListener('click', event => {
      const routeButton = event.target.closest('[data-route-preset]');
      if (!routeButton) return;
      const mode = routeButton.dataset.routePreset;
      const values = {
        reliable: [90, 1],
        cheap: [60, 3],
        fallback: [10, 1]
      }[mode] || [50, 1];
      document.getElementById('route-priority').value = values[0];
      document.getElementById('route-weight').value = values[1];
      document.getElementById('route-priority-visible').value = values[0];
      document.getElementById('route-weight-visible').value = values[1];
      document.querySelectorAll('[data-route-preset]').forEach(button => {
        button.classList.toggle('active', button === routeButton);
      });
      showToast(`已选择${routeButton.querySelector('strong').textContent}`);
    });
    document.addEventListener('input', event => {
      if (event.target.id === 'route-priority-visible') {
        document.getElementById('route-priority').value = event.target.value;
      }
      if (event.target.id === 'route-weight-visible') {
        document.getElementById('route-weight').value = event.target.value;
      }
    });
    document.addEventListener('click', event => {
      const action = event.target.closest('[data-action]');
      if (!action) return;
      if (action.dataset.action === 'refresh-models') {
        refresh(false).then(() => showToast('已刷新本地模型目录；自动同步接口将在后续接入'));
      }
    });

    document.addEventListener('input', event => {
      const id = event.target.dataset.tableSearch;
      if (!id) return;
      tableState[id] = tableState[id] || {page: 1, query: ''};
      tableState[id].query = event.target.value;
      tableState[id].page = 1;
      render();
      const input = document.querySelector(`[data-table-search="${id}"]`);
      if (input) input.focus();
    });
    document.addEventListener('click', event => {
      const id = event.target.dataset.page;
      if (!id) return;
      tableState[id] = tableState[id] || {page: 1, query: ''};
      tableState[id].page += Number(event.target.dataset.dir || 0);
      render();
      showToast(`已切换到第 ${tableState[id].page} 页`);
    });
    for (const form of document.querySelectorAll('form[data-endpoint]')) {
      form.addEventListener('submit', async event => {
        event.preventDefault();
        const endpoint = form.dataset.endpoint;
        const button = form.querySelector('button.primary');
        const oldText = button ? button.textContent : '';
        try {
          if (button) {
            button.disabled = true;
            button.textContent = '处理中...';
          }
          const data = await api(endpoint, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(formPayload(form))
          });
          if (endpoint === '/api/agent-plan') {
            document.getElementById('agent-result').textContent = JSON.stringify(data, null, 2);
          }
          await refresh();
          showToast(form.dataset.success || endpointSuccess[endpoint] || '操作已完成');
        } catch (err) {
          showToast(err.message, 'bad');
          if (endpoint === '/api/agent-plan') {
            document.getElementById('agent-result').textContent = JSON.stringify({error: err.message}, null, 2);
          }
        } finally {
          if (button) {
            button.disabled = false;
            button.textContent = oldText;
          }
        }
      });
    }

    setView(location.hash.replace('#', '') || 'overview');
    refresh();
  </script>
</body>
</html>
"""
