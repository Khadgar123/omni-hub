from __future__ import annotations

import json
import os
import re
import shlex
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import monotonic, time
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
from .secrets import SecretStoreError, has_secret, resolve_secret_ref, store_api_key


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


STREAM_CHECK_DEFAULTS: dict[str, Any] = {
    "timeout_secs": 45,
    "max_retries": 2,
    "degraded_threshold_ms": 6000,
    "test_prompt": "Who are you?",
}


MODEL_FETCH_TIMEOUT_SECS = 15
BALANCE_CHECK_TIMEOUT_SECS = 10


OFFICIAL_PROVIDER_PRESETS: list[dict[str, Any]] = [
    {
        "name": "OpenAI",
        "slug": "openai",
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "secret_ref": "env:OPENAI_API_KEY",
        "env_var": "OPENAI_API_KEY",
        "base_env_var": "OPENAI_BASE_URL",
        "quota_ref": "dashboard:https://platform.openai.com/usage",
        "models": ["gpt-5.4", "gpt-5.4-mini"],
        "capabilities": ["text", "tools", "vision"],
        "rank": 100,
    },
    {
        "name": "Claude",
        "slug": "claude",
        "provider": "claude",
        "base_url": "https://api.anthropic.com/v1",
        "secret_ref": "env:ANTHROPIC_API_KEY",
        "env_var": "ANTHROPIC_API_KEY",
        "base_env_var": "ANTHROPIC_BASE_URL",
        "quota_ref": "dashboard:https://console.anthropic.com/settings/billing",
        "models": ["claude-opus-4-20250514", "claude-sonnet-4-5"],
        "capabilities": ["text", "tools", "vision"],
        "rank": 96,
    },
    {
        "name": "Qwen",
        "slug": "qwen",
        "provider": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "secret_ref": "env:DASHSCOPE_API_KEY",
        "env_var": "DASHSCOPE_API_KEY",
        "base_env_var": "DASHSCOPE_BASE_URL",
        "quota_ref": "dashboard:阿里云百炼控制台",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo"],
        "capabilities": ["text", "tools", "vision"],
        "rank": 92,
    },
    {
        "name": "DeepSeek",
        "slug": "deepseek",
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "secret_ref": "env:DEEPSEEK_API_KEY",
        "env_var": "DEEPSEEK_API_KEY",
        "base_env_var": "DEEPSEEK_BASE_URL",
        "quota_ref": "dashboard:DeepSeek 开放平台余额",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "capabilities": ["text", "tools"],
        "rank": 88,
    },
    {
        "name": "GLM",
        "slug": "glm",
        "provider": "glm",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "secret_ref": "env:ZAI_API_KEY",
        "env_var": "ZAI_API_KEY",
        "base_env_var": "ZAI_BASE_URL",
        "quota_ref": "dashboard:智谱 AI 开放平台",
        "models": ["glm-5", "glm-4.7"],
        "capabilities": ["text", "tools", "vision"],
        "rank": 84,
    },
    {
        "name": "MiniMax",
        "slug": "minimax",
        "provider": "minimax",
        "base_url": "https://api.minimax.io/v1",
        "secret_ref": "env:MINIMAX_API_KEY",
        "env_var": "MINIMAX_API_KEY",
        "base_env_var": "MINIMAX_BASE_URL",
        "quota_ref": "dashboard:MiniMax Token Plan",
        "models": ["MiniMax-M2.7", "MiniMax-M2.7-highspeed"],
        "capabilities": ["text", "tools", "vision"],
        "rank": 80,
    },
]


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
        if parsed.path == "/api/stream-check-config":
            self._send_json({"stream_check": dict(STREAM_CHECK_DEFAULTS)})
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
        "official_providers": sorted(
            OFFICIAL_PROVIDER_PRESETS,
            key=lambda item: (-int(item.get("rank", 0)), str(item.get("name", ""))),
        ),
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
    if path == "/api/official-provider-config":
        preset = _official_provider_preset(_required(payload, "provider"))
        account_id = str(payload.get("account_id", "")).strip() or (
            f"{preset['slug']}-main"
        )
        api_key = str(payload.get("api_key", "")).strip()
        secret_ref = str(payload.get("secret_ref", "")).strip()
        secret_mode = "ref"
        if api_key:
            if api_key.startswith(("env:", "keychain:", "local:", "runtime:")):
                secret_ref = api_key
            else:
                secret_ref = store_api_key(account_id, api_key)
                secret_mode = secret_ref.split(":", 1)[0]
        elif secret_ref and not secret_ref.startswith(("env:", "keychain:", "local:", "runtime:")):
            secret_ref = store_api_key(account_id, secret_ref)
            secret_mode = secret_ref.split(":", 1)[0]

        usage_access_token_ref = str(payload.get("usage_access_token_ref", "")).strip()
        usage_access_token = str(payload.get("usage_access_token", "")).strip()
        if usage_access_token:
            if usage_access_token.startswith(("env:", "keychain:", "local:", "runtime:")):
                usage_access_token_ref = usage_access_token
            else:
                usage_access_token_ref = store_api_key(
                    f"{account_id}-usage-access-token",
                    usage_access_token,
                )

        quota_ref = str(payload.get("quota_ref", "")).strip() or str(
            preset.get("quota_ref", "")
        )
        notes = "\n".join(
            item
            for item in [
                f"official_provider={preset['slug']}",
                f"quota_ref={quota_ref}" if quota_ref else "",
                f"api_key_storage={secret_mode}" if secret_mode != "ref" else "",
                _payload_note(payload, "api_format"),
                _payload_note(payload, "auth_field"),
                _payload_note(payload, "is_full_url"),
                _payload_note(payload, "models_url"),
                _payload_note(payload, "max_concurrency"),
                _payload_note(payload, "rpm_limit"),
                _payload_note(payload, "tpm_limit"),
                _payload_note(payload, "cost_multiplier"),
                _payload_note(payload, "pricing_model_source"),
                _payload_note(payload, "default_model"),
                _payload_note(payload, "model_reasoning_effort"),
                _payload_note(payload, "disable_response_storage"),
                _payload_note(payload, "wire_api"),
                _payload_note(payload, "requires_openai_auth"),
                _payload_note(payload, "usage_template"),
                _payload_note(payload, "usage_base_url"),
                _payload_note(payload, "usage_endpoint"),
                _payload_note(payload, "usage_user_id"),
                f"usage_access_token_ref={usage_access_token_ref}"
                if usage_access_token_ref
                else "",
                _payload_note(payload, "test_model"),
                _payload_note(payload, "test_prompt"),
                _payload_note(payload, "timeout_secs"),
                _payload_note(payload, "max_retries"),
                _payload_note(payload, "degraded_threshold_ms"),
                str(payload.get("notes", "")).strip(),
            ]
            if item
        )
        account = ProviderAccount(
            account_id=account_id,
            provider=str(preset["provider"]),
            name=str(payload.get("name", "")).strip() or str(preset["name"]),
            base_url=str(payload.get("base_url", "")).strip()
            or str(preset["base_url"]),
            secret_ref=secret_ref,
            proxy_url=str(payload.get("proxy_url", "")).strip(),
            status=ProviderAccountStatus(str(payload.get("status", "active"))),
            account_group="official",
            notes=notes,
        )
        stored_account = store.upsert_account(account)
        priority = _int_value(payload.get("priority"), default=90)
        capabilities = _list_value(payload.get("capabilities")) or _list_value(
            preset.get("capabilities")
        )
        model_ids = _list_value(payload.get("model_ids")) or _list_value(
            preset.get("models")
        )
        default_model = str(payload.get("default_model", "")).strip()
        if default_model and default_model in model_ids:
            model_ids = [default_model, *[item for item in model_ids if item != default_model]]
        stored_models = []
        stored_abilities = []
        for index, model_id in enumerate(model_ids):
            model = ModelSpec(
                model_id=model_id,
                display_name=model_id,
                capabilities=capabilities,
                notes=f"official_provider={preset['slug']}",
            )
            stored_model = store.upsert_model(model)
            ability = RouteAbility(
                account_id=stored_account.account_id,
                model_id=stored_model.model_id,
                enabled=True,
                priority=max(priority - index * 5, 0),
                weight=1.0,
                model_mapping=model_id,
                notes=f"official_provider={preset['slug']}",
            )
            stored_models.append(stored_model.to_dict())
            stored_abilities.append(store.upsert_ability(ability).to_dict())
        return {
            "account": stored_account.to_dict(),
            "models": stored_models,
            "abilities": stored_abilities,
            "secret_mode": secret_mode,
        }

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

    if path in {"/api/stream-check", "/api/model-probe"}:
        account = store.get_account(_required(payload, "account_id"))
        account_health, model_health, stream_check = _stream_check_provider(
            account,
            store,
            model_id=str(payload.get("model_id", "")),
            config=_stream_check_config(payload, account),
        )
        stored_account_health = store.set_health(account_health)
        stored_model_health = (
            store.set_health(model_health).to_dict()
            if model_health.model_id
            else stored_account_health.to_dict()
        )
        output = {
            "health": stored_account_health.to_dict(),
            "model_health": stored_model_health,
            "stream_check": stream_check,
        }
        if path == "/api/model-probe":
            output["probe"] = stream_check
        return output

    if path == "/api/model-fetch":
        fetched = _fetch_models_from_payload(payload, store)
        return {"models": fetched["models"], "candidates": fetched["candidates"]}

    if path == "/api/balance-check":
        account = store.get_account(_required(payload, "account_id"))
        return _balance_check(account)

    if path == "/api/provider-script":
        account = store.get_account(_required(payload, "account_id"))
        return _provider_script_response(account, store, payload)

    if path == "/api/project-import-routes":
        return _project_import_routes(store, payload)

    if path == "/api/project-bundle":
        return _project_bundle_response(store, _required(payload, "project_id"))

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


def _official_provider_preset(slug: str) -> dict[str, Any]:
    normalized = slug.strip().lower()
    for preset in OFFICIAL_PROVIDER_PRESETS:
        if normalized in {str(preset["slug"]).lower(), str(preset["provider"]).lower()}:
            return preset
    raise ValueError(f"unknown official provider: {slug}")


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
    try:
        if not has_secret(account.secret_ref):
            return ProviderHealth(
                account_id=account.account_id,
                status=HealthStatus.LIMITED,
                last_error=f"secret is not available for {account.secret_ref}",
            )
    except SecretStoreError as exc:
        return ProviderHealth(
            account_id=account.account_id,
            status=HealthStatus.LIMITED,
            last_error=str(exc),
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


def _stream_check_provider(
    account: ProviderAccount,
    store: ProviderRouterStore,
    *,
    model_id: str = "",
    config: dict[str, Any] | None = None,
) -> tuple[ProviderHealth, ProviderHealth, dict[str, Any]]:
    config = config or dict(STREAM_CHECK_DEFAULTS)
    abilities = store.list_abilities(account_id=account.account_id, enabled=True)
    if model_id:
        abilities = [ability for ability in abilities if ability.model_id == model_id]
    if not abilities:
        health = ProviderHealth(
            account_id=account.account_id,
            status=HealthStatus.LIMITED,
            last_error="no enabled model route for stream check",
        )
        return health, ProviderHealth.unknown(account.account_id), {
            "ok": False,
            "stage": "route",
            "status": "failed",
            "success": False,
            "error": health.last_error,
        }

    ability = abilities[0]
    if str(config.get("test_model", "")).strip():
        ability = RouteAbility(
            account_id=ability.account_id,
            model_id=ability.model_id,
            enabled=ability.enabled,
            priority=ability.priority,
            weight=ability.weight,
            model_mapping=str(config["test_model"]).strip(),
            notes=ability.notes,
            created_at=ability.created_at,
            updated_at=ability.updated_at,
        )
    model = store.get_model(ability.model_id)
    provider_model_id = ability.model_mapping or model.model_id
    try:
        api_key = resolve_secret_ref(account.secret_ref)
    except SecretStoreError as exc:
        health = ProviderHealth(
            account_id=account.account_id,
            model_id=model.model_id,
            status=HealthStatus.LIMITED,
            last_error=str(exc),
        )
        return _account_health_from_model(health), health, {
            "ok": False,
            "stage": "secret",
            "status": "failed",
            "success": False,
            "model_id": model.model_id,
            "error": str(exc),
        }
    if not api_key:
        health = ProviderHealth(
            account_id=account.account_id,
            model_id=model.model_id,
            status=HealthStatus.LIMITED,
            last_error=f"secret is not available for {account.secret_ref}",
        )
        return _account_health_from_model(health), health, {
            "ok": False,
            "stage": "secret",
            "status": "failed",
            "success": False,
            "model_id": model.model_id,
            "error": health.last_error,
        }

    result = _send_stream_check(
        account,
        provider_model_id,
        api_key,
        config=config,
    )
    status = _health_status_from_stream_check(result, config)
    last_error = _stream_check_summary(result)
    model_health = ProviderHealth(
        account_id=account.account_id,
        model_id=model.model_id,
        status=status,
        latency_ms=result.get("latency_ms"),
        consecutive_failures=0 if result.get("ok") else 1,
        last_error=last_error,
    )
    stream_check = {
        **{
            key: value
            for key, value in result.items()
            if key not in {"body_preview"}
        },
        "model_id": model.model_id,
        "provider_model_id": provider_model_id,
        "account_id": account.account_id,
        "status": _stream_check_public_status(result, config),
        "success": bool(result.get("ok")),
        "message": _stream_check_public_message(result),
        "responseTimeMs": result.get("latency_ms"),
        "httpStatus": result.get("http_status"),
        "modelUsed": provider_model_id,
        "testedAt": int(time()),
        "retryCount": int(result.get("retry_count", 0)),
        "errorCategory": _detect_error_category(
            int(result.get("http_status") or 0),
            str(result.get("error", "")),
        ),
    }
    return _account_health_from_model(model_health), model_health, stream_check


def _stream_check_config(
    payload: dict[str, Any],
    account: ProviderAccount | None = None,
) -> dict[str, Any]:
    config = dict(STREAM_CHECK_DEFAULTS)
    notes = account.notes if account else ""
    for key in ("timeout_secs", "max_retries", "degraded_threshold_ms"):
        noted = _note_value(notes, key)
        if noted:
            config[key] = max(_int_value(noted), 1 if key != "max_retries" else 0)
    noted_prompt = _note_value(notes, "test_prompt")
    if noted_prompt:
        config["test_prompt"] = noted_prompt
    test_model = _note_value(notes, "test_model")
    default_model = _note_value(notes, "default_model")
    if "model_id" not in payload:
        if test_model:
            config["test_model"] = test_model
        elif default_model:
            config["test_model"] = default_model
    if "timeout_secs" in payload:
        config["timeout_secs"] = max(_int_value(payload.get("timeout_secs")), 1)
    if "max_retries" in payload:
        config["max_retries"] = max(_int_value(payload.get("max_retries")), 0)
    if "degraded_threshold_ms" in payload:
        config["degraded_threshold_ms"] = max(
            _int_value(payload.get("degraded_threshold_ms")),
            1,
        )
    if str(payload.get("test_prompt", "")).strip():
        config["test_prompt"] = str(payload["test_prompt"]).strip()
    return config


def _send_stream_check(
    account: ProviderAccount,
    provider_model_id: str,
    api_key: str,
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    max_retries = max(int(config.get("max_retries", 0)), 0)
    last_result: dict[str, Any] | None = None
    for attempt in range(max_retries + 1):
        result = _send_stream_check_once(
            account,
            provider_model_id,
            api_key,
            timeout_secs=max(int(config.get("timeout_secs", 45)), 1),
            test_prompt=str(config.get("test_prompt", "Who are you?")),
        )
        result["retry_count"] = attempt
        last_result = result
        if result.get("ok") or not _stream_check_should_retry(result):
            return result
    return last_result or {
        "ok": False,
        "http_status": None,
        "latency_ms": None,
        "retry_count": max_retries,
        "error": "stream check failed",
    }


def _send_stream_check_once(
    account: ProviderAccount,
    provider_model_id: str,
    api_key: str,
    *,
    timeout_secs: int,
    test_prompt: str,
) -> dict[str, Any]:
    request, api_format = _stream_check_request(
        account,
        provider_model_id,
        api_key,
        test_prompt=test_prompt,
    )
    opener = _opener_for_account(account)
    started = monotonic()
    try:
        with opener.open(request, timeout=timeout_secs) as response:
            first_chunk = response.read(1)
            latency_ms = int((monotonic() - started) * 1000)
            if not first_chunk:
                return {
                    "ok": False,
                    "http_status": getattr(response, "status", response.getcode()),
                    "latency_ms": latency_ms,
                    "quota": _quota_signals(response.headers),
                    "request_id": _request_id(response.headers),
                    "api_format": api_format,
                    "endpoint": _redact_url(request.full_url),
                    "error": "No response data received",
                }
            return {
                "ok": True,
                "http_status": getattr(response, "status", response.getcode()),
                "latency_ms": latency_ms,
                "quota": _quota_signals(response.headers),
                "request_id": _request_id(response.headers),
                "api_format": api_format,
                "endpoint": _redact_url(request.full_url),
            }
    except HTTPError as exc:
        raw = exc.read(65536)
        latency_ms = int((monotonic() - started) * 1000)
        return {
            "ok": False,
            "http_status": exc.code,
            "latency_ms": latency_ms,
            "quota": _quota_signals(exc.headers),
            "request_id": _request_id(exc.headers),
            "api_format": api_format,
            "endpoint": _redact_url(request.full_url),
            "error": _safe_body_preview(raw, api_key),
        }
    except URLError as exc:
        latency_ms = int((monotonic() - started) * 1000)
        return {
            "ok": False,
            "http_status": None,
            "latency_ms": latency_ms,
            "api_format": api_format,
            "endpoint": _redact_url(request.full_url),
            "error": f"network stream check failed: {exc.reason}",
        }


def _stream_check_request(
    account: ProviderAccount,
    provider_model_id: str,
    api_key: str,
    *,
    test_prompt: str,
) -> Request:
    api_format = _account_api_format(account)
    if api_format == "anthropic":
        url = _api_endpoint(account.base_url, "messages")
        payload = {
            "model": provider_model_id,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": test_prompt}],
            "stream": True,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Accept-Encoding": "identity",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    elif api_format == "openai_responses":
        actual_model, reasoning_effort = _parse_model_with_effort(provider_model_id)
        url = _api_endpoint(account.base_url, "responses")
        payload = {
            "model": actual_model,
            "input": [{"role": "user", "content": test_prompt}],
            "stream": True,
            "max_output_tokens": 1,
        }
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
        headers = _bearer_json_headers(api_key, stream=True)
    elif api_format == "gemini_native":
        actual_model = _normalize_gemini_model_id(provider_model_id)
        url = _gemini_stream_endpoint(account.base_url, actual_model)
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": test_prompt}]},
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "x-goog-api-key": api_key,
        }
    else:
        url = _api_endpoint(account.base_url, "chat/completions")
        payload = {
            "model": provider_model_id,
            "messages": [{"role": "user", "content": test_prompt}],
            "max_tokens": 1,
            "temperature": 0,
            "stream": True,
        }
        headers = _bearer_json_headers(api_key, stream=True)
    return (
        Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        ),
        api_format,
    )


def _account_health_from_model(model_health: ProviderHealth) -> ProviderHealth:
    return ProviderHealth(
        account_id=model_health.account_id,
        status=model_health.status,
        latency_ms=model_health.latency_ms,
        consecutive_failures=model_health.consecutive_failures,
        last_error=(
            f"{model_health.model_id}: {model_health.last_error}"
            if model_health.model_id and model_health.last_error
            else model_health.last_error
        ),
    )


def _health_status_from_stream_check(
    result: dict[str, Any],
    config: dict[str, Any],
) -> HealthStatus:
    if result.get("ok"):
        latency_ms = result.get("latency_ms")
        if isinstance(latency_ms, int) and latency_ms > int(
            config.get("degraded_threshold_ms", 6000)
        ):
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY
    status = result.get("http_status")
    if status in {401, 403, 429}:
        return HealthStatus.LIMITED
    if isinstance(status, int) and 400 <= status < 500:
        return HealthStatus.DEGRADED
    return HealthStatus.DOWN


def _stream_check_summary(result: dict[str, Any]) -> str:
    status = result.get("http_status")
    quota = result.get("quota") or {}
    request_id = result.get("request_id")
    if result.get("ok"):
        details = [
            f"stream check ok; http={status}",
            f"format={result.get('api_format')}",
        ]
        if request_id:
            details.append(f"request_id={request_id}")
        if quota:
            details.append(
                "quota="
                + ",".join(f"{key}:{value}" for key, value in quota.items())
            )
        return "; ".join(details)
    error = str(result.get("error", "")).strip()
    category = _detect_error_category(int(status or 0), error)
    prefix = f"stream check failed; http={status}"
    if category:
        prefix = f"{prefix}; category={category}"
    return f"{prefix}; {error}" if error else prefix


def _stream_check_public_status(
    result: dict[str, Any],
    config: dict[str, Any],
) -> str:
    if not result.get("ok"):
        return "failed"
    latency_ms = result.get("latency_ms")
    if isinstance(latency_ms, int) and latency_ms > int(
        config.get("degraded_threshold_ms", 6000)
    ):
        return "degraded"
    return "operational"


def _stream_check_public_message(result: dict[str, Any]) -> str:
    if result.get("ok"):
        return "Check succeeded"
    status = result.get("http_status")
    if isinstance(status, int):
        return _classify_http_status(status)
    return str(result.get("error", "Check failed"))


def _stream_check_should_retry(result: dict[str, Any]) -> bool:
    status = result.get("http_status")
    return status is None or status == 429 or (isinstance(status, int) and status >= 500)


def _classify_http_status(status: int) -> str:
    if status in {400, 422}:
        return "Request rejected"
    if status in {401, 403}:
        return "Authentication failed"
    if status == 404:
        return "Endpoint or model not found"
    if status == 429:
        return "Rate limited or quota exceeded"
    if status >= 500:
        return "Provider server error"
    return f"HTTP {status}"


def _detect_error_category(status: int, body: str) -> str | None:
    lower = body.lower()
    if any(
        item in lower
        for item in (
            "coding_plan_hour_quota_exceeded",
            "coding_plan_week_quota_exceeded",
            "coding_plan_month_quota_exceeded",
            "insufficient_quota",
            "quota exceeded",
        )
    ):
        return "quotaExceeded"
    if not (400 <= status < 500) or "model" not in lower:
        return None
    if any(
        item in lower
        for item in (
            "model_not_found",
            "model not found",
            "does not exist",
            "invalid_model",
            "invalid model",
            "unknown_model",
            "unknown model",
            "is not a valid model",
            "not_found_error",
        )
    ):
        return "modelNotFound"
    return None


def _account_api_format(account: ProviderAccount) -> str:
    notes_format = _note_value(account.notes, "api_format")
    if notes_format:
        return notes_format
    provider = account.provider.lower()
    if provider in {"claude", "anthropic"}:
        return "anthropic"
    if provider == "gemini":
        return "gemini_native"
    if "codex" in provider:
        return "openai_responses"
    return "openai_chat"


def _api_endpoint(base_url: str, suffix: str) -> str:
    clean = base_url.rstrip("/")
    if clean.endswith(("/chat/completions", "/messages", "/responses")):
        return clean
    return f"{clean}/{suffix.lstrip('/')}"


def _gemini_stream_endpoint(base_url: str, model: str) -> str:
    clean = base_url.rstrip("/")
    if ":streamGenerateContent" in clean:
        return clean
    if clean.endswith("/models"):
        return f"{clean}/{model}:streamGenerateContent?alt=sse"
    if "/v1beta" in clean or "/v1/" in clean:
        return f"{clean}/models/{model}:streamGenerateContent?alt=sse"
    return f"{clean}/v1beta/models/{model}:streamGenerateContent?alt=sse"


def _bearer_json_headers(api_key: str, *, stream: bool = False) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Accept-Encoding": "identity",
    }
    if stream:
        headers["Accept"] = "text/event-stream"
    return headers


def _parse_model_with_effort(model: str) -> tuple[str, str | None]:
    for separator in ("@", "#"):
        if separator in model:
            actual_model, effort = model.rsplit(separator, 1)
            effort = effort.strip()
            if actual_model.strip() and effort in {"minimal", "low", "medium", "high"}:
                return actual_model.strip(), effort
    return model, None


def _normalize_gemini_model_id(model: str) -> str:
    clean = model.strip().lstrip("/")
    return clean.removeprefix("models/")


MODEL_FETCH_COMPAT_SUFFIXES = (
    "/api/claudecode",
    "/api/anthropic",
    "/apps/anthropic",
    "/api/coding",
    "/claudecode",
    "/anthropic",
    "/step_plan",
    "/coding",
    "/claude",
)


def _fetch_models_from_payload(
    payload: dict[str, Any],
    store: ProviderRouterStore,
) -> dict[str, Any]:
    account_id = str(payload.get("account_id", "")).strip()
    raw_api_key = str(payload.get("api_key", "")).strip()
    payload_secret_ref = str(payload.get("secret_ref", "")).strip()
    stored_account = None
    if account_id:
        try:
            stored_account = store.get_account(account_id)
        except KeyError:
            stored_account = None

    if stored_account:
        base_url = str(payload.get("base_url", "")).strip() or stored_account.base_url
        account = ProviderAccount(
            account_id=stored_account.account_id,
            provider=stored_account.provider,
            name=stored_account.name,
            base_url=base_url,
            secret_ref=stored_account.secret_ref or payload_secret_ref,
            proxy_url=str(payload.get("proxy_url", "")).strip()
            or stored_account.proxy_url,
            status=stored_account.status,
            account_group=stored_account.account_group,
            notes=stored_account.notes,
        )
        api_key = raw_api_key
        if not api_key and account.secret_ref:
            api_key = resolve_secret_ref(account.secret_ref)
        is_full_url = _truthy(
            str(payload.get("is_full_url", "")).strip()
            or _note_value(account.notes, "is_full_url")
        )
        models_url = str(payload.get("models_url", "")).strip() or _note_value(
            account.notes,
            "models_url",
        )
    else:
        base_url = _required(payload, "base_url")
        account = ProviderAccount(
            account_id=account_id or "draft-model-fetch",
            provider=str(payload.get("provider", "draft")).strip() or "draft",
            name=str(payload.get("name", "")).strip() or "Draft Model Fetch",
            base_url=base_url,
            secret_ref=payload_secret_ref,
            proxy_url=str(payload.get("proxy_url", "")),
        )
        api_key = raw_api_key
        if not api_key and account.secret_ref:
            api_key = resolve_secret_ref(account.secret_ref)
        is_full_url = _truthy(str(payload.get("is_full_url", "")).strip())
        models_url = str(payload.get("models_url", "")).strip()

    if not api_key:
        raise ValueError("请先填写 API Key，或先保存渠道让本地密钥引用可用")

    candidates = _build_models_url_candidates(
        base_url,
        is_full_url=is_full_url,
        models_url_override=models_url,
    )
    models = _fetch_models_from_candidates(account, api_key, candidates)
    return {"models": models, "candidates": candidates}


def _fetch_models_from_candidates(
    account: ProviderAccount,
    api_key: str,
    candidates: list[str],
) -> list[dict[str, str]]:
    opener = _opener_for_account(account)
    last_error = "no candidates"
    for url in candidates:
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with opener.open(request, timeout=MODEL_FETCH_TIMEOUT_SECS) as response:
                raw = response.read(1_000_000)
        except HTTPError as exc:
            body = _safe_body_preview(exc.read(4096), api_key)
            if exc.code in {404, 405}:
                last_error = f"HTTP {exc.code}: {body}"
                continue
            raise ValueError(f"HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise ValueError(f"Request failed: {exc.reason}") from exc

        parsed = json.loads(raw.decode("utf-8"))
        entries = _model_entries(parsed)
        if not isinstance(entries, list):
            raise ValueError("Failed to parse models response: data is not a list")
        models = []
        for item in entries:
            if isinstance(item, str):
                models.append({"id": item, "owned_by": ""})
                continue
            if not isinstance(item, dict) or not item.get("id"):
                continue
            models.append(
                {
                    "id": str(item["id"]),
                    "owned_by": str(item.get("owned_by", "")),
                }
            )
        return sorted(models, key=lambda item: item["id"])
    raise ValueError(f"All model endpoint candidates failed: {last_error}")


def _model_entries(parsed: Any) -> list[Any]:
    if isinstance(parsed, list):
        return parsed
    if not isinstance(parsed, dict):
        return []
    for key in ("data", "models"):
        entries = parsed.get(key)
        if isinstance(entries, list):
            return entries
    data = parsed.get("data")
    if isinstance(data, dict):
        entries = data.get("models")
        if isinstance(entries, list):
            return entries
    return []


def _build_models_url_candidates(
    base_url: str,
    *,
    is_full_url: bool = False,
    models_url_override: str = "",
) -> list[str]:
    override = models_url_override.strip()
    if override:
        return [override]

    clean = base_url.strip().rstrip("/")
    if not clean:
        raise ValueError("base_url is required")

    candidates: list[str] = []
    if is_full_url:
        marker = "/v1/"
        if marker in clean:
            candidates.append(f"{clean.split(marker, 1)[0]}/v1/models")
        elif "/" in clean.removeprefix("https://").removeprefix("http://"):
            candidates.append(f"{clean.rsplit('/', 1)[0]}/v1/models")
        if not candidates:
            raise ValueError("cannot derive models endpoint from full URL")
        return _unique(candidates)

    if clean.endswith("/v1"):
        candidates.append(f"{clean}/models")
    else:
        candidates.append(f"{clean}/v1/models")

    stripped = _strip_model_fetch_compat_suffix(clean)
    if stripped:
        root = stripped.rstrip("/")
        candidates.append(f"{root}/v1/models")
        candidates.append(f"{root}/models")
    return _unique(candidates)


def _strip_model_fetch_compat_suffix(base_url: str) -> str:
    for suffix in MODEL_FETCH_COMPAT_SUFFIXES:
        if base_url.endswith(suffix):
            return base_url[: -len(suffix)]
    return ""


def _unique(items: list[str]) -> list[str]:
    output: list[str] = []
    for item in items:
        if item not in output:
            output.append(item)
    return output


def _provider_script_response(
    account: ProviderAccount,
    store: ProviderRouterStore,
    payload: dict[str, Any],
) -> dict[str, Any]:
    output_format = str(payload.get("format", "shell")).strip() or "shell"
    try:
        api_key = resolve_secret_ref(account.secret_ref)
    except SecretStoreError as exc:
        raise ValueError(str(exc)) from exc
    if not api_key:
        raise ValueError(f"secret is not available for {account.secret_ref}")

    if output_format == "codex_toml":
        script = _codex_toml_for_account(account, store)
        return {
            "account_id": account.account_id,
            "format": output_format,
            "contains_secret": False,
            "script": script,
        }

    script = _shell_script_for_account(account, api_key)
    return {
        "account_id": account.account_id,
        "format": "shell",
        "contains_secret": True,
        "script": script,
    }


def _shell_script_for_account(account: ProviderAccount, api_key: str) -> str:
    env_var = "OPENAI_API_KEY"
    base_env = "OPENAI_BASE_URL"
    if account.provider == "claude":
        env_var = "ANTHROPIC_API_KEY"
        base_env = "ANTHROPIC_BASE_URL"
    elif account.provider == "qwen":
        env_var = "DASHSCOPE_API_KEY"
        base_env = "DASHSCOPE_BASE_URL"
    elif account.provider == "deepseek":
        env_var = "DEEPSEEK_API_KEY"
        base_env = "DEEPSEEK_BASE_URL"
    elif account.provider == "glm":
        env_var = "ZAI_API_KEY"
        base_env = "ZAI_BASE_URL"
    elif account.provider == "minimax":
        env_var = "MINIMAX_API_KEY"
        base_env = "MINIMAX_BASE_URL"

    lines = [
        f"export {env_var}={shlex.quote(api_key)}",
        f"export {base_env}={shlex.quote(account.base_url)}",
    ]
    if account.provider != "openai":
        lines.append(f"export OPENAI_API_KEY={shlex.quote(api_key)}")
        lines.append(f"export OPENAI_BASE_URL={shlex.quote(account.base_url)}")
    if account.proxy_url:
        lines.append(f"export HTTPS_PROXY={shlex.quote(account.proxy_url)}")
        lines.append(f"export HTTP_PROXY={shlex.quote(account.proxy_url)}")
    else:
        lines.append("unset HTTPS_PROXY HTTP_PROXY")
    return "\n".join(lines)


def _codex_toml_for_account(
    account: ProviderAccount,
    store: ProviderRouterStore,
) -> str:
    provider_key = _codex_provider_key(account)
    default_model = _note_value(account.notes, "default_model") or _first_account_model(
        account,
        store,
    )
    reasoning = _note_value(account.notes, "model_reasoning_effort") or "high"
    wire_api = _note_value(account.notes, "wire_api") or "responses"
    disable_storage = _note_value(account.notes, "disable_response_storage") or "true"
    requires_openai_auth = _note_value(account.notes, "requires_openai_auth") or "true"
    lines = [
        f"model_provider = {_toml_string(provider_key)}",
        f"model = {_toml_string(default_model or 'gpt-5.4')}",
        f"model_reasoning_effort = {_toml_string(reasoning)}",
        f"disable_response_storage = {_toml_bool(disable_storage)}",
        "",
        f"[model_providers.{provider_key}]",
        f"name = {_toml_string(provider_key)}",
        f"base_url = {_toml_string(account.base_url)}",
        f"wire_api = {_toml_string(wire_api)}",
        f"requires_openai_auth = {_toml_bool(requires_openai_auth)}",
    ]
    return "\n".join(lines)


def _first_account_model(
    account: ProviderAccount,
    store: ProviderRouterStore,
) -> str:
    abilities = store.list_abilities(account_id=account.account_id, enabled=True)
    if not abilities:
        return ""
    return abilities[0].model_mapping or abilities[0].model_id


def _codex_provider_key(account: ProviderAccount) -> str:
    if account.provider == "openai":
        return "codex"
    cleaned = re.sub(r"[^a-z0-9_]", "_", account.account_id.lower()).strip("_")
    return cleaned or "codex"


def _toml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _toml_bool(value: str) -> str:
    return "true" if _truthy(str(value)) else "false"


def _balance_check(account: ProviderAccount) -> dict[str, Any]:
    try:
        api_key = resolve_secret_ref(account.secret_ref)
    except SecretStoreError as exc:
        return {
            "account_id": account.account_id,
            "success": False,
            "data": None,
            "error": str(exc),
            "checked_at": int(time()),
        }
    if not api_key:
        return {
            "account_id": account.account_id,
            "success": False,
            "data": None,
            "error": f"secret is not available for {account.secret_ref}",
            "checked_at": int(time()),
        }

    result = _query_provider_balance(account, api_key)
    return {
        "account_id": account.account_id,
        "provider": account.provider,
        "quota_ref": _note_value(account.notes, "quota_ref"),
        "checked_at": int(time()),
        **result,
    }


def _query_provider_balance(
    account: ProviderAccount,
    api_key: str,
) -> dict[str, Any]:
    provider = _detect_balance_provider(account.base_url)
    if provider == "deepseek":
        return _query_balance_json(
            account,
            api_key,
            "https://api.deepseek.com/user/balance",
            lambda body: [
                {
                    "plan_name": str(info.get("currency", "CNY")),
                    "remaining": _json_float(info, "total_balance"),
                    "total": None,
                    "used": None,
                    "unit": str(info.get("currency", "CNY")),
                    "is_valid": bool(body.get("is_available", True)),
                }
                for info in body.get("balance_infos", [])
                if isinstance(info, dict)
            ],
        )
    if provider == "stepfun":
        return _query_balance_json(
            account,
            api_key,
            "https://api.stepfun.com/v1/accounts",
            lambda body: [
                {
                    "plan_name": "StepFun",
                    "remaining": _json_float(body, "balance"),
                    "total": None,
                    "used": None,
                    "unit": "CNY",
                    "is_valid": True,
                }
            ],
        )
    if provider in {"siliconflow-cn", "siliconflow-en"}:
        is_cn = provider == "siliconflow-cn"
        host = "api.siliconflow.cn" if is_cn else "api.siliconflow.com"
        unit = "CNY" if is_cn else "USD"
        return _query_balance_json(
            account,
            api_key,
            f"https://{host}/v1/user/info",
            lambda body: [
                {
                    "plan_name": "SiliconFlow" if is_cn else "SiliconFlow (EN)",
                    "remaining": _json_float(body.get("data", {}), "totalBalance"),
                    "total": None,
                    "used": None,
                    "unit": unit,
                    "is_valid": True,
                }
            ],
        )
    if provider == "openrouter":
        return _query_balance_json(
            account,
            api_key,
            "https://openrouter.ai/api/v1/credits",
            lambda body: [
                {
                    "plan_name": "OpenRouter",
                    "remaining": (
                        _json_float(body.get("data", body), "total_credits") or 0
                    )
                    - (_json_float(body.get("data", body), "total_usage") or 0),
                    "total": _json_float(body.get("data", body), "total_credits"),
                    "used": _json_float(body.get("data", body), "total_usage"),
                    "unit": "USD",
                    "is_valid": True,
                }
            ],
        )
    if provider == "novita":
        return _query_balance_json(
            account,
            api_key,
            "https://api.novita.ai/v3/user/balance",
            lambda body: [
                {
                    "plan_name": "Novita AI",
                    "remaining": (_json_float(body, "availableBalance") or 0) / 10000,
                    "total": None,
                    "used": None,
                    "unit": "USD",
                    "is_valid": True,
                }
            ],
        )
    template = _note_value(account.notes, "usage_template") or "auto"
    if template in {"auto", "generic", "newapi"}:
        generic = _query_generic_balance(account, api_key, template=template)
        if generic["success"] or not provider:
            return generic
        if template != "auto":
            return generic
    return {
        "success": False,
        "data": None,
        "error": "unsupported balance provider",
    }


def _query_generic_balance(
    account: ProviderAccount,
    api_key: str,
    *,
    template: str = "auto",
) -> dict[str, Any]:
    root = _balance_root_url(_note_value(account.notes, "usage_base_url") or account.base_url)
    candidates: list[tuple[str, Any, dict[str, str] | None]] = []
    if template in {"auto", "newapi"}:
        try:
            newapi_headers = _newapi_usage_headers(account, api_key)
        except SecretStoreError as exc:
            return {"success": False, "data": None, "error": str(exc)}
        candidates.append(
            (
                f"{root}/api/user/self",
                _parse_newapi_balance,
                newapi_headers,
            )
        )
    if template in {"auto", "generic"}:
        usage_endpoint = _note_value(account.notes, "usage_endpoint") or "/v1/usage"
        candidates.append((_join_url(root, usage_endpoint), _parse_generic_balance, None))
        candidates.extend(
            [
                (f"{root}/user/balance", _parse_generic_balance, None),
                (f"{root}/v1/user/info", _parse_generic_balance, None),
                (f"{root}/dashboard/billing/credit_grants", _parse_generic_balance, None),
                (f"{root}/billing/credit_grants", _parse_generic_balance, None),
            ]
        )

    last_result = {
        "success": False,
        "data": None,
        "error": "unsupported balance provider",
    }
    for url, parser, headers in _unique_balance_candidates(candidates):
        result = _query_balance_json(account, api_key, url, parser, headers=headers)
        if result["success"]:
            result["template"] = template
            result["endpoint"] = url
            return result
        last_result = result
        error = str(result.get("error", ""))
        if "HTTP 401" in error or "HTTP 403" in error:
            break
    return last_result


def _newapi_usage_headers(account: ProviderAccount, api_key: str) -> dict[str, str]:
    access_token = api_key
    access_token_ref = _note_value(account.notes, "usage_access_token_ref")
    if access_token_ref:
        access_token = resolve_secret_ref(access_token_ref)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "omni-hub/0.1",
    }
    user_id = _note_value(account.notes, "usage_user_id")
    if user_id:
        headers["New-Api-User"] = user_id
    return headers


def _unique_balance_candidates(
    candidates: list[tuple[str, Any, dict[str, str] | None]],
) -> list[tuple[str, Any, dict[str, str] | None]]:
    output: list[tuple[str, Any, dict[str, str] | None]] = []
    seen: set[str] = set()
    for url, parser, headers in candidates:
        if url in seen:
            continue
        seen.add(url)
        output.append((url, parser, headers))
    return output


def _balance_root_url(base_url: str) -> str:
    clean = base_url.strip().rstrip("/")
    for suffix in ("/v1", "/api/v1", "/api/openai/v1"):
        if clean.lower().endswith(suffix):
            return clean[: -len(suffix)].rstrip("/")
    return clean


def _join_url(root: str, endpoint: str) -> str:
    clean_endpoint = endpoint.strip()
    if clean_endpoint.startswith("http://") or clean_endpoint.startswith("https://"):
        return clean_endpoint
    return f"{root.rstrip('/')}/{clean_endpoint.lstrip('/')}"


def _parse_newapi_balance(body: dict[str, Any]) -> list[dict[str, Any]]:
    data = body.get("data", body)
    if not isinstance(data, dict):
        return []
    quota = _json_float(data, "quota")
    used_quota = _json_float(data, "used_quota")
    if quota is None and used_quota is None:
        return _parse_generic_balance(body)
    scale = 500000
    remaining = (quota or 0) / scale
    used = (used_quota or 0) / scale
    total = remaining + used
    return [
        {
            "plan_name": str(data.get("group") or data.get("plan") or "New API"),
            "remaining": remaining,
            "total": total,
            "used": used,
            "unit": "USD",
            "is_valid": bool(body.get("success", True)),
            "extra": {
                "raw_quota": quota,
                "raw_used_quota": used_quota,
            },
        }
    ]


def _parse_generic_balance(body: dict[str, Any]) -> list[dict[str, Any]]:
    data = body.get("data", body)
    if not isinstance(data, dict):
        return []
    quota = data.get("quota")
    quota_data = quota if isinstance(quota, dict) else {}
    remaining = (
        _json_float(data, "remaining")
        or _json_float(quota_data, "remaining")
        or _json_float(data, "balance")
        or _json_float(data, "totalBalance")
        or _json_float(data, "availableBalance")
        or _json_float(data, "total_available")
        or _json_float(data, "credit")
        or _json_float(data, "credits")
    )
    total = (
        _json_float(data, "total")
        or _json_float(quota_data, "total")
        or _json_float(data, "total_credits")
        or _json_float(data, "limit")
    )
    used = (
        _json_float(data, "used")
        or _json_float(quota_data, "used")
        or _json_float(data, "total_usage")
    )
    if remaining is None and total is not None and used is not None:
        remaining = total - used
    if remaining is None:
        return []
    unit = str(data.get("unit") or quota_data.get("unit") or data.get("currency") or "USD")
    plan_name = str(data.get("plan_name") or data.get("planName") or data.get("plan") or "余额")
    is_valid = data.get("is_active", data.get("isValid", remaining > 0))
    return [
        {
            "plan_name": plan_name,
            "remaining": remaining,
            "total": total,
            "used": used,
            "unit": unit,
            "is_valid": bool(is_valid),
        }
    ]


def _query_balance_json(
    account: ProviderAccount,
    api_key: str,
    url: str,
    parser: Any,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request = Request(
        url,
        headers=headers or {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    opener = _opener_for_account(account)
    try:
        with opener.open(request, timeout=BALANCE_CHECK_TIMEOUT_SECS) as response:
            body = json.loads(response.read(1_000_000).decode("utf-8"))
    except HTTPError as exc:
        body_text = _safe_body_preview(exc.read(4096), api_key)
        return {
            "success": False,
            "data": [
                {
                    "is_valid": False,
                    "invalid_message": f"Authentication failed (HTTP {exc.code})"
                    if exc.code in {401, 403}
                    else f"API error (HTTP {exc.code})",
                }
            ],
            "error": f"{url}: HTTP {exc.code}: {body_text}",
        }
    except URLError as exc:
        return {"success": False, "data": None, "error": f"{url}: Network error: {exc.reason}"}
    except json.JSONDecodeError as exc:
        return {"success": False, "data": None, "error": f"{url}: Failed to parse response: {exc}"}

    try:
        data = parser(body)
    except Exception as exc:
        return {"success": False, "data": None, "error": f"{url}: Failed to parse balance: {exc}"}
    return {"success": True, "data": data or None, "error": None}


def _detect_balance_provider(base_url: str) -> str:
    url = base_url.lower()
    if "api.deepseek.com" in url:
        return "deepseek"
    if "api.stepfun.ai" in url or "api.stepfun.com" in url:
        return "stepfun"
    if "api.siliconflow.cn" in url:
        return "siliconflow-cn"
    if "api.siliconflow.com" in url:
        return "siliconflow-en"
    if "openrouter.ai" in url:
        return "openrouter"
    if "api.novita.ai" in url:
        return "novita"
    return ""


def _json_float(obj: Any, key: str) -> float | None:
    if not isinstance(obj, dict):
        return None
    value = obj.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _note_value(notes: str, key: str) -> str:
    prefix = f"{key}="
    for line in notes.splitlines():
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    return ""


def _payload_note(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"false", "none", "null"}:
        return ""
    return f"{key}={text}"


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    query = "&".join(
        part
        for part in parsed.query.split("&")
        if not part.lower().startswith(("key=", "api_key=", "access_token="))
    )
    base = url.split("?", 1)[0]
    return f"{base}?{query}" if query else base


MODEL_SLOT_PRESETS: list[dict[str, Any]] = [
    {
        "slot": "default",
        "label": "默认文本",
        "capabilities": ["text"],
        "description": "日常问答、总结、写作和轻量工具调用。",
    },
    {
        "slot": "reasoning",
        "label": "复杂推理",
        "capabilities": ["text", "reasoning"],
        "description": "规划、研究、长链路分析和关键决策。",
    },
    {
        "slot": "code",
        "label": "代码与工具",
        "capabilities": ["text", "tools", "code"],
        "description": "工程修改、工具调用、测试和自动化执行。",
    },
    {
        "slot": "vision",
        "label": "多模态",
        "capabilities": ["vision"],
        "description": "图片、OCR、视频帧和视觉理解。",
    },
    {
        "slot": "batch",
        "label": "批处理/低价",
        "capabilities": ["text", "batch"],
        "description": "异步处理、批量总结和成本敏感任务。",
    },
    {
        "slot": "embedding",
        "label": "检索向量",
        "capabilities": ["embedding"],
        "description": "知识库索引、相似度检索和重排链路。",
    },
]


def _project_import_routes(
    store: ProviderRouterStore,
    payload: dict[str, Any],
) -> dict[str, Any]:
    project_id = _required(payload, "project_id")
    provider = str(payload.get("provider", "")).strip()
    scope = str(payload.get("scope", "selected_provider")).strip()
    account_ids = set(_list_value(payload.get("account_ids")))
    accounts = [
        account
        for account in store.list_accounts(status=ProviderAccountStatus.ACTIVE.value)
        if (
            account.account_id in account_ids
            or (
                not account_ids
                and (scope == "all" or not provider or account.provider == provider)
            )
        )
    ]
    abilities = [
        ability
        for account in accounts
        for ability in store.list_abilities(account_id=account.account_id, enabled=True)
    ]
    if not abilities:
        raise ValueError("没有可导入的已启用模型配置")

    preferred_accounts = _unique([ability.account_id for ability in abilities])
    profile = store.upsert_project_profile(
        ProjectRouteProfile(
            project_id=project_id,
            default_capabilities=["text"],
            preferred_providers=_unique([account.provider for account in accounts]),
            preferred_accounts=preferred_accounts,
            notes="imported_from=provider_config_list",
        )
    )
    overrides = []
    for ability in abilities:
        account = store.get_account(ability.account_id)
        model = store.get_model(ability.model_id)
        overrides.append(
            store.upsert_project_override(
                ProjectRouteOverride(
                    project_id=project_id,
                    account_id=ability.account_id,
                    model_id=ability.model_id,
                    priority=ability.priority,
                    weight=ability.weight,
                    enabled=ability.enabled,
                    notes=_project_route_notes(account, model, ability),
                )
            ).to_dict()
        )
    return {
        "profile": profile.to_dict(),
        "routes": overrides,
        "bundle": _project_model_bundle(store, project_id),
    }


def _project_bundle_response(
    store: ProviderRouterStore,
    project_id: str,
) -> dict[str, Any]:
    return {"bundle": _project_model_bundle(store, project_id)}


def _project_model_bundle(
    store: ProviderRouterStore,
    project_id: str,
) -> dict[str, Any]:
    overrides = store.list_project_overrides(project_id=project_id)
    routes = []
    for override in overrides:
        account = store.get_account(override.account_id)
        model = store.get_model(override.model_id)
        ability = next(
            (
                item
                for item in store.list_abilities(
                    account_id=override.account_id,
                    model_id=override.model_id,
                )
            ),
            None,
        )
        health = store.get_health(account.account_id, model.model_id)
        routes.append(
            {
                "account_id": account.account_id,
                "provider": account.provider,
                "channel_name": account.name,
                "base_url": account.base_url,
                "secret_ref": account.secret_ref,
                "proxy_url": account.proxy_url,
                "model_id": model.model_id,
                "provider_model_id": ability.model_mapping if ability else model.model_id,
                "capabilities": model.capabilities,
                "context_window": model.context_window,
                "supports_batch": model.supports_batch,
                "priority": override.priority if override.priority is not None else (ability.priority if ability else 0),
                "weight": override.weight if override.weight is not None else (ability.weight if ability else 1),
                "api_format": _account_api_format(account),
                "auth_field": _note_value(account.notes, "auth_field"),
                "models_url": _note_value(account.notes, "models_url"),
                "max_concurrency": _note_value(account.notes, "max_concurrency"),
                "rpm_limit": _note_value(account.notes, "rpm_limit"),
                "tpm_limit": _note_value(account.notes, "tpm_limit"),
                "cost_multiplier": _note_value(account.notes, "cost_multiplier"),
                "pricing_model_source": _note_value(account.notes, "pricing_model_source"),
                "health": health.to_dict(),
            }
        )
    sorted_routes = sorted(
        routes,
        key=lambda item: (
            -int(item.get("priority") or 0),
            item["provider"],
            item["model_id"],
        ),
    )
    return {
        "project_id": project_id,
        "slots": MODEL_SLOT_PRESETS,
        "slot_routes": _project_slot_routes(sorted_routes),
        "routes": sorted_routes,
        "security": {
            "api_key": "not_exported",
            "secret_ref": "resolve at runtime from local file/env/runtime",
        },
    }


def _project_slot_routes(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slot_routes = []
    for slot in MODEL_SLOT_PRESETS:
        required = set(slot["capabilities"])
        candidates = []
        for route in routes:
            capabilities = set(route.get("capabilities") or [])
            if route.get("supports_batch"):
                capabilities.add("batch")
            if required.issubset(capabilities):
                candidates.append(
                    {
                        "account_id": route["account_id"],
                        "provider": route["provider"],
                        "channel_name": route["channel_name"],
                        "model_id": route["model_id"],
                        "provider_model_id": route["provider_model_id"],
                        "priority": route["priority"],
                        "health_status": route["health"]["status"],
                        "max_concurrency": route["max_concurrency"],
                        "rpm_limit": route["rpm_limit"],
                    }
                )
        slot_routes.append(
            {
                "slot": slot["slot"],
                "label": slot["label"],
                "capabilities": slot["capabilities"],
                "candidates": candidates,
            }
        )
    return slot_routes


def _project_route_notes(
    account: ProviderAccount,
    model: ModelSpec,
    ability: RouteAbility,
) -> str:
    lines = [
        "imported_from=provider_config_list",
        f"provider={account.provider}",
        f"channel={account.name}",
        f"base_url={account.base_url}",
        f"secret_ref={account.secret_ref}",
        f"proxy_url={account.proxy_url or 'unset'}",
        f"provider_model_id={ability.model_mapping or model.model_id}",
        f"api_format={_account_api_format(account)}",
    ]
    for key in (
        "max_concurrency",
        "rpm_limit",
        "tpm_limit",
        "cost_multiplier",
        "pricing_model_source",
    ):
        value = _note_value(account.notes, key)
        if value:
            lines.append(f"{key}={value}")
    return "\n".join(lines)


def _opener_for_account(account: ProviderAccount):
    handler = None
    if account.proxy_url:
        proxy_url = account.proxy_url
        if proxy_url.startswith("env:"):
            proxy_url = os.environ.get(proxy_url.split(":", 1)[1], "")
        if proxy_url:
            handler = ProxyHandler({"http": proxy_url, "https": proxy_url})
    return build_opener(handler) if handler else build_opener()


def _quota_signals(headers: Any) -> dict[str, str]:
    signals: dict[str, str] = {}
    for key in headers.keys():
        lower = key.lower()
        if lower.startswith("x-ratelimit") or lower in {
            "retry-after",
            "x-request-cost",
            "x-remaining-credits",
        }:
            signals[key] = str(headers.get(key, ""))[:120]
    return signals


def _request_id(headers: Any) -> str:
    for key in ("request-id", "x-request-id", "cf-ray"):
        value = headers.get(key)
        if value:
            return str(value)[:120]
    return ""


def _safe_body_preview(raw: bytes, api_key: str) -> str:
    text = raw.decode("utf-8", errors="replace").replace(api_key, "[redacted]")
    return " ".join(text.split())[:500]


def _probe_base_url(account: ProviderAccount) -> tuple[int | None, str]:
    parsed = urlparse(account.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url is not a valid HTTP URL")

    opener = _opener_for_account(account)
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
    text = str(value).replace("\n", ",")
    return [item.strip() for item in text.split(",") if item.strip()]


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
