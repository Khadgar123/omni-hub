from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .agent import AgentPlanner, AgentTaskRequest, estimate_input_tokens, task_preview
from .provider_router import (
    ModelSpec,
    ModelStatus,
    ProviderAccount,
    ProviderAccountStatus,
    ProviderRouterStore,
    ProjectRouteOverride,
    ProjectRouteProfile,
    RouteAbility,
)


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


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
        "accounts": [account.to_dict() for account in store.list_accounts()],
        "models": [model.to_dict() for model in store.list_models()],
        "abilities": [ability.to_dict() for ability in store.list_abilities()],
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
            status=ProviderAccountStatus(str(payload.get("status", "active"))),
            account_group=str(payload.get("account_group", "")),
            notes=str(payload.get("notes", "")),
        )
        return {"account": store.upsert_account(account).to_dict()}

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

    raise ValueError(f"unknown endpoint: {path}")


def _required(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


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


INDEX_HTML = r"""<!doctype html>
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
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="brand">
        <h1>万象中枢</h1>
        <p>本地 Provider Router 与 Agent 控制台</p>
      </div>
      <nav>
        <button class="nav-item" data-view="overview">总览</button>
        <button class="nav-item" data-view="providers">Provider 账号</button>
        <button class="nav-item" data-view="models">模型目录</button>
        <button class="nav-item" data-view="routes">全局路由</button>
        <button class="nav-item" data-view="projects">项目策略</button>
        <button class="nav-item" data-view="agent">Agent 规划</button>
        <button class="nav-item" data-view="safety">安全边界</button>
      </nav>
    </aside>

    <div class="main">
      <header>
        <div class="topbar">
          <div class="title">
            <h2 id="page-title">总览</h2>
            <p id="page-subtitle">本机状态、路由配置和 Agent 调用前规划。</p>
          </div>
          <button class="secondary" id="refresh">刷新数据</button>
        </div>
      </header>

      <main class="content">
        <section class="view" data-view-panel="overview">
          <div class="notice">当前控制台只管理万象中枢自己的本地状态，不改写 Codex、Claude、Gemini、Cursor、VS Code 等外部客户端配置。</div>
          <div class="metrics" id="metrics"></div>
          <div class="guide">
            <div class="guide-item"><h4>1. 配置账号</h4><p>登记 provider、base URL 和 secret ref。不要输入 raw API key。</p></div>
            <div class="guide-item"><h4>2. 配置模型</h4><p>登记模型能力、上下文、成本和 batch 支持情况。</p></div>
            <div class="guide-item"><h4>3. 配置路由</h4><p>全局路由是默认值，项目策略会为自有 agent 覆盖优先级。</p></div>
          </div>
          <div class="panel">
            <div class="panel-head"><h3>最近配置概览</h3><span class="subtle">显示前 8 条 Provider 与项目策略</span></div>
            <div id="overviewTable"></div>
          </div>
        </section>

        <section class="view" data-view-panel="providers">
          <div class="split-panel">
            <form data-endpoint="/api/providers">
              <div class="form-help">Provider 账号表示一组 base URL 和凭证引用。Secret 只能填 env:、keychain: 或 runtime: 引用。</div>
              <label>账号 ID<input name="account_id" placeholder="openai-main" required></label>
              <label>Provider<input name="provider" placeholder="openai" required></label>
              <label class="wide">显示名称<input name="name" placeholder="OpenAI 主账号" required></label>
              <label class="wide">Base URL<input name="base_url" placeholder="https://api.openai.com/v1" required></label>
              <label class="wide">Secret Ref<input name="secret_ref" placeholder="env:OPENAI_API_KEY"></label>
              <label>状态<select name="status"><option value="active">active</option><option value="disabled">disabled</option><option value="auto_disabled">auto_disabled</option></select></label>
              <label>分组<input name="account_group" placeholder="default"></label>
              <button class="primary">保存 Provider</button>
            </form>
            <div id="accountsTable"></div>
          </div>
        </section>

        <section class="view" data-view-panel="models">
          <div class="split-panel">
            <form data-endpoint="/api/models">
              <div class="form-help">能力用逗号分隔，例如 text, tools, vision。成本单位是每百万 token 美元。</div>
              <label class="wide">模型 ID<input name="model_id" placeholder="gpt-5.4" required></label>
              <label class="wide">显示名称<input name="display_name" placeholder="GPT 5.4"></label>
              <label class="wide">能力<input name="capabilities" data-list placeholder="text, tools, vision"></label>
              <label>输入成本<input name="input_usd_per_million" type="number" min="0" step="0.0001"></label>
              <label>输出成本<input name="output_usd_per_million" type="number" min="0" step="0.0001"></label>
              <label>上下文窗口<input name="context_window" type="number" min="0" step="1"></label>
              <label>支持 Batch<input name="supports_batch" type="checkbox"></label>
              <button class="primary">保存模型</button>
            </form>
            <div id="modelsTable"></div>
          </div>
        </section>

        <section class="view" data-view-panel="routes">
          <div class="split-panel">
            <form data-endpoint="/api/route-abilities">
              <div class="form-help">全局路由是默认选择规则。项目策略可以覆盖 priority 或 weight，但不能绕过禁用和健康状态。</div>
              <label>账号 ID<input name="account_id" required></label>
              <label>模型 ID<input name="model_id" required></label>
              <label>优先级<input name="priority" type="number" step="1" value="0"></label>
              <label>权重<input name="weight" type="number" min="0" step="0.1" value="1"></label>
              <label class="wide">Provider 侧模型名<input name="model_mapping" placeholder="如果不同于内部模型 ID"></label>
              <label>启用<input name="enabled" type="checkbox" checked></label>
              <button class="primary">保存全局路由</button>
            </form>
            <div id="abilitiesTable"></div>
          </div>
        </section>

        <section class="view" data-view-panel="projects">
          <div class="split-panel">
            <form data-endpoint="/api/project-profiles">
              <div class="form-help">项目 Profile 用来定义默认能力、预算上限和偏好，供万象中枢自有 agent 使用。</div>
              <label class="wide">项目 ID<input name="project_id" placeholder="writing" required></label>
              <label class="wide">默认能力<input name="default_capabilities" data-list placeholder="text, tools"></label>
              <label>预算上限<input name="max_cost_usd" type="number" min="0" step="0.0001"></label>
              <label>要求 Batch<input name="require_batch" type="checkbox"></label>
              <label class="wide">偏好 Provider<input name="preferred_providers" data-list placeholder="anthropic, openai"></label>
              <label class="wide">偏好账号<input name="preferred_accounts" data-list placeholder="anthropic-main"></label>
              <button class="primary">保存项目 Profile</button>
            </form>
            <div id="profilesTable"></div>
          </div>
          <div class="split-panel">
            <form data-endpoint="/api/project-routes">
              <div class="form-help">项目 Override 用于指定某个项目里的 account/model 优先级。</div>
              <label>项目 ID<input name="project_id" required></label>
              <label>账号 ID<input name="account_id" required></label>
              <label>模型 ID<input name="model_id" required></label>
              <label>优先级<input name="priority" type="number" step="1"></label>
              <label>权重<input name="weight" type="number" min="0" step="0.1"></label>
              <label>启用<input name="enabled" type="checkbox" checked></label>
              <button class="primary">保存项目路由</button>
            </form>
            <div id="overridesTable"></div>
          </div>
        </section>

        <section class="view" data-view-panel="agent">
          <div class="split-panel">
            <form data-endpoint="/api/agent-plan">
              <div class="form-help">这里只做调用前规划，不真实请求模型。完整任务不会进入 Operation payload。</div>
              <label>项目 ID<input name="project_id" placeholder="writing"></label>
              <label>输出 token<input name="output_tokens" type="number" min="0" step="1"></label>
              <label class="wide">任务<textarea name="task" placeholder="帮我整理这个项目的上下文"></textarea></label>
              <label class="wide">能力<input name="capabilities" data-list placeholder="text, tools"></label>
              <label>预算上限<input name="max_cost_usd" type="number" min="0" step="0.0001"></label>
              <label>要求 Batch<input name="require_batch" type="checkbox"></label>
              <button class="primary">规划 Agent 调用</button>
            </form>
            <pre id="agent-result">{}</pre>
          </div>
        </section>

        <section class="view" data-view-panel="safety">
          <div class="panel">
            <div class="panel-head"><h3>安全边界</h3><span class="subtle">阶段 1 只运行在本机</span></div>
            <div class="guide" style="padding: 14px;">
              <div class="guide-item"><h4>不保存原始密钥</h4><p>只保存 env:、keychain:、runtime: 形式的引用。</p></div>
              <div class="guide-item"><h4>不改外部客户端</h4><p>不会写 Codex、Claude、Gemini、Cursor、VS Code 配置。</p></div>
              <div class="guide-item"><h4>不真实调用 API</h4><p>当前 GUI 只做配置与规划，真实模型调用后续接入。</p></div>
            </div>
          </div>
        </section>
      </main>
    </div>
  </div>

  <script>
    const PAGE_SIZE = 8;
    const views = {
      overview: ['总览', '本机状态、路由配置和 Agent 调用前规划。'],
      providers: ['Provider 账号', '管理 provider、base URL 和 secret 引用。'],
      models: ['模型目录', '管理模型能力、上下文窗口和成本。'],
      routes: ['全局路由', '管理默认 account/model priority 与 provider 模型名映射。'],
      projects: ['项目策略', '为不同项目设置模型优先级和预算边界。'],
      agent: ['Agent 规划', '为万象中枢自有 agent 生成一次调用计划。'],
      safety: ['安全边界', '本地控制面不会改写外部客户端配置。']
    };
    const state = { data: null, view: 'overview' };
    const tableState = {};

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
    const formPayload = (form) => {
      const out = {};
      for (const el of form.elements) {
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
        provider_accounts: 'Provider',
        model_catalog: '模型',
        route_abilities: '全局路由',
        project_route_profiles: '项目 Profile',
        project_route_overrides: '项目路由',
        provider_health: '健康记录',
        usage_request_logs: '调用日志'
      };
      document.getElementById('metrics').innerHTML = Object.entries(labels).map(([key, label]) => (
        `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(stats[key] || 0)}</strong></div>`
      )).join('');
    }

    function render() {
      const data = state.data;
      if (!data) return;
      renderMetrics(data.stats || {});
      renderDataTable('overviewTable', '概览', [
        {label: '类型', render: r => `<span class="pill info">${escapeHtml(r.type)}</span>`},
        {label: '主键', key: 'id'},
        {label: '说明', key: 'detail'},
        {label: '状态', render: r => r.status ? statusPill(r.status) : ''}
      ], [
        ...data.accounts.slice(0, 4).map(r => ({type: 'Provider', id: r.account_id, detail: `${r.provider} · ${r.base_url}`, status: r.status})),
        ...data.profiles.slice(0, 4).map(r => ({type: '项目', id: r.project_id, detail: `能力: ${(r.default_capabilities || []).join(', ') || '未设置'}`, status: r.max_cost_usd ? `预算 ${r.max_cost_usd}` : ''}))
      ]);
      renderDataTable('accountsTable', 'Provider 账号', [
        {key: 'account_id', label: '账号'},
        {key: 'provider', label: 'Provider'},
        {label: '状态', render: r => statusPill(r.status)},
        {key: 'base_url', label: 'Base URL'},
        {key: 'secret_ref', label: 'Secret Ref'}
      ], data.accounts);
      renderDataTable('modelsTable', '模型目录', [
        {key: 'model_id', label: '模型'},
        {label: '状态', render: r => statusPill(r.status)},
        {label: '能力', render: r => escapeHtml((r.capabilities || []).join(', '))},
        {key: 'context_window', label: '上下文'},
        {key: 'input_usd_per_million', label: '输入成本'},
        {key: 'output_usd_per_million', label: '输出成本'},
        {label: 'Batch', render: r => boolPill(r.supports_batch)}
      ], data.models);
      renderDataTable('abilitiesTable', '全局路由', [
        {key: 'account_id', label: '账号'},
        {key: 'model_id', label: '模型'},
        {label: '启用', render: r => boolPill(r.enabled)},
        {key: 'priority', label: '优先级'},
        {key: 'weight', label: '权重'},
        {key: 'model_mapping', label: 'Provider 模型名'}
      ], data.abilities);
      renderDataTable('profilesTable', '项目 Profile', [
        {key: 'project_id', label: '项目'},
        {label: '默认能力', render: r => escapeHtml((r.default_capabilities || []).join(', '))},
        {key: 'max_cost_usd', label: '预算上限'},
        {label: '偏好 Provider', render: r => escapeHtml((r.preferred_providers || []).join(', '))},
        {label: '偏好账号', render: r => escapeHtml((r.preferred_accounts || []).join(', '))}
      ], data.profiles);
      renderDataTable('overridesTable', '项目路由覆盖', [
        {key: 'project_id', label: '项目'},
        {key: 'account_id', label: '账号'},
        {key: 'model_id', label: '模型'},
        {label: '启用', render: r => boolPill(r.enabled)},
        {key: 'priority', label: '优先级'},
        {key: 'weight', label: '权重'}
      ], data.overrides);
    }

    async function refresh() {
      state.data = await api('/api/state');
      render();
    }

    document.querySelectorAll('[data-view]').forEach(button => {
      button.addEventListener('click', () => setView(button.dataset.view));
    });
    document.getElementById('refresh').addEventListener('click', refresh);
    window.addEventListener('hashchange', () => setView(location.hash.replace('#', '') || 'overview'));

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
    });
    for (const form of document.querySelectorAll('form[data-endpoint]')) {
      form.addEventListener('submit', async event => {
        event.preventDefault();
        const endpoint = form.dataset.endpoint;
        try {
          const data = await api(endpoint, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(formPayload(form))
          });
          if (endpoint === '/api/agent-plan') {
            document.getElementById('agent-result').textContent = JSON.stringify(data, null, 2);
          }
          await refresh();
        } catch (err) {
          if (endpoint === '/api/agent-plan') {
            document.getElementById('agent-result').textContent = JSON.stringify({error: err.message}, null, 2);
          } else {
            alert(err.message);
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
