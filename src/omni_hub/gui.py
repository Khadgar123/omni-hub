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
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Omni Hub Control</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f8;
      --panel: #ffffff;
      --ink: #172026;
      --muted: #66727d;
      --line: #d7dde3;
      --blue: #2457d6;
      --green: #157f5b;
      --red: #b42318;
      --amber: #9a6700;
      --soft-blue: #eef3ff;
      --soft-green: #edf8f3;
      --soft-red: #fff1f0;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }
    header {
      position: sticky;
      top: 0;
      z-index: 2;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, .96);
      backdrop-filter: blur(12px);
    }
    .bar {
      max-width: 1280px;
      margin: 0 auto;
      padding: 14px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      letter-spacing: 0;
    }
    .subtle { color: var(--muted); }
    main {
      max-width: 1280px;
      margin: 0 auto;
      padding: 20px;
      display: grid;
      gap: 18px;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(6, minmax(120px, 1fr));
      gap: 10px;
    }
    .metric, section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .metric {
      padding: 12px;
      min-height: 72px;
    }
    .metric strong {
      display: block;
      font-size: 22px;
      line-height: 1.1;
      margin-top: 6px;
    }
    section { overflow: hidden; }
    .section-head {
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    h2 {
      margin: 0;
      font-size: 15px;
      letter-spacing: 0;
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(260px, 360px) 1fr;
      gap: 0;
    }
    form {
      padding: 16px;
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
    }
    label.wide, textarea, button, .form-note { grid-column: 1 / -1; }
    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 9px;
      color: var(--ink);
      background: #fff;
      font: inherit;
      min-width: 0;
    }
    textarea {
      min-height: 112px;
      resize: vertical;
    }
    input[type="checkbox"] {
      width: auto;
      justify-self: start;
    }
    button {
      height: 36px;
      border: 1px solid #1e4fc5;
      border-radius: 6px;
      color: #fff;
      background: var(--blue);
      font-weight: 600;
      cursor: pointer;
    }
    button.secondary {
      color: var(--ink);
      background: #fff;
      border-color: var(--line);
      width: auto;
      padding: 0 12px;
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
      background: #fbfcfd;
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
    }
    .ok { color: var(--green); background: var(--soft-green); }
    .bad { color: var(--red); background: var(--soft-red); }
    .warn { color: var(--amber); background: #fff8e6; }
    .scroll { overflow: auto; max-height: 520px; }
    pre {
      margin: 0;
      padding: 16px;
      min-height: 220px;
      max-height: 520px;
      overflow: auto;
      background: #101820;
      color: #e7edf3;
      font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .notice {
      border: 1px solid #b9c7e8;
      background: var(--soft-blue);
      color: #17346f;
      border-radius: 8px;
      padding: 12px 14px;
    }
    @media (max-width: 900px) {
      .metrics { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
      .grid { grid-template-columns: 1fr; }
      form { border-right: 0; border-bottom: 1px solid var(--line); }
    }
  </style>
</head>
<body>
  <header>
    <div class="bar">
      <div>
        <h1>Omni Hub Control</h1>
        <div class="subtle">Local provider router and agent planner</div>
      </div>
      <button class="secondary" id="refresh">Refresh</button>
    </div>
  </header>
  <main>
    <div class="notice">This GUI only changes Omni Hub local state. It does not rewrite Codex, Claude, Gemini, Cursor, VS Code, or other external client configs.</div>
    <div class="metrics" id="metrics"></div>

    <section>
      <div class="section-head"><h2>Provider Accounts</h2><span class="subtle">Secret values must be refs, not raw keys.</span></div>
      <div class="grid">
        <form data-endpoint="/api/providers">
          <label>Account ID<input name="account_id" placeholder="openai-main" required></label>
          <label>Provider<input name="provider" placeholder="openai" required></label>
          <label class="wide">Name<input name="name" placeholder="OpenAI Main" required></label>
          <label class="wide">Base URL<input name="base_url" placeholder="https://api.openai.com/v1" required></label>
          <label class="wide">Secret Ref<input name="secret_ref" placeholder="env:OPENAI_API_KEY"></label>
          <label>Status<select name="status"><option>active</option><option>disabled</option><option>auto_disabled</option></select></label>
          <label>Group<input name="account_group" placeholder="default"></label>
          <button>Save Provider</button>
        </form>
        <div class="scroll"><table id="accounts"></table></div>
      </div>
    </section>

    <section>
      <div class="section-head"><h2>Models</h2><span class="subtle">Capabilities are comma separated.</span></div>
      <div class="grid">
        <form data-endpoint="/api/models">
          <label class="wide">Model ID<input name="model_id" placeholder="gpt-5.4" required></label>
          <label class="wide">Display Name<input name="display_name" placeholder="GPT 5.4"></label>
          <label class="wide">Capabilities<input name="capabilities" data-list placeholder="text, tools, vision"></label>
          <label>Input Cost<input name="input_usd_per_million" type="number" min="0" step="0.0001"></label>
          <label>Output Cost<input name="output_usd_per_million" type="number" min="0" step="0.0001"></label>
          <label>Context<input name="context_window" type="number" min="0" step="1"></label>
          <label>Batch<input name="supports_batch" type="checkbox"></label>
          <button>Save Model</button>
        </form>
        <div class="scroll"><table id="models"></table></div>
      </div>
    </section>

    <section>
      <div class="section-head"><h2>Global Routes</h2><span class="subtle">Default account/model priority.</span></div>
      <div class="grid">
        <form data-endpoint="/api/route-abilities">
          <label>Account ID<input name="account_id" required></label>
          <label>Model ID<input name="model_id" required></label>
          <label>Priority<input name="priority" type="number" step="1" value="0"></label>
          <label>Weight<input name="weight" type="number" min="0" step="0.1" value="1"></label>
          <label class="wide">Provider Model ID<input name="model_mapping" placeholder="provider-side model name"></label>
          <label>Enabled<input name="enabled" type="checkbox" checked></label>
          <button>Save Route</button>
        </form>
        <div class="scroll"><table id="abilities"></table></div>
      </div>
    </section>

    <section>
      <div class="section-head"><h2>Project Routing</h2><span class="subtle">Profiles and per-project overrides for Omni Hub agents.</span></div>
      <div class="grid">
        <form data-endpoint="/api/project-profiles">
          <label class="wide">Project ID<input name="project_id" placeholder="writing" required></label>
          <label class="wide">Default Capabilities<input name="default_capabilities" data-list placeholder="text, tools"></label>
          <label>Max Cost<input name="max_cost_usd" type="number" min="0" step="0.0001"></label>
          <label>Batch<input name="require_batch" type="checkbox"></label>
          <label class="wide">Preferred Providers<input name="preferred_providers" data-list placeholder="anthropic, openai"></label>
          <label class="wide">Preferred Accounts<input name="preferred_accounts" data-list placeholder="anthropic-main"></label>
          <button>Save Profile</button>
        </form>
        <div class="scroll"><table id="profiles"></table></div>
      </div>
      <div class="grid">
        <form data-endpoint="/api/project-routes">
          <label>Project ID<input name="project_id" required></label>
          <label>Account ID<input name="account_id" required></label>
          <label>Model ID<input name="model_id" required></label>
          <label>Priority<input name="priority" type="number" step="1"></label>
          <label>Weight<input name="weight" type="number" min="0" step="0.1"></label>
          <label>Enabled<input name="enabled" type="checkbox" checked></label>
          <button>Save Override</button>
        </form>
        <div class="scroll"><table id="overrides"></table></div>
      </div>
    </section>

    <section>
      <div class="section-head"><h2>Agent Planner</h2><span class="subtle">Plans a route, does not call external APIs.</span></div>
      <div class="grid">
        <form id="agent-form" data-endpoint="/api/agent-plan">
          <label>Project ID<input name="project_id" placeholder="writing"></label>
          <label>Output Tokens<input name="output_tokens" type="number" min="0" step="1"></label>
          <label class="wide">Task<textarea name="task" placeholder="Summarize this project context"></textarea></label>
          <label class="wide">Capabilities<input name="capabilities" data-list placeholder="text, tools"></label>
          <label>Max Cost<input name="max_cost_usd" type="number" min="0" step="0.0001"></label>
          <label>Batch<input name="require_batch" type="checkbox"></label>
          <button>Plan Agent Call</button>
        </form>
        <pre id="agent-result">{}</pre>
      </div>
    </section>
  </main>
  <script>
    const state = { data: null };
    const api = async (url, options = {}) => {
      const res = await fetch(url, options);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Request failed');
      return data;
    };
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
      const cls = value === 'active' ? 'ok' : (value === 'down' || value === 'disabled' ? 'bad' : 'warn');
      return `<span class="pill ${cls}">${escapeHtml(String(value || ''))}</span>`;
    };
    const escapeHtml = (value) => String(value).replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
    const renderTable = (id, columns, rows) => {
      const table = document.getElementById(id);
      table.innerHTML = [
        '<thead><tr>' + columns.map(c => `<th>${escapeHtml(c.label)}</th>`).join('') + '</tr></thead>',
        '<tbody>' + rows.map(row => '<tr>' + columns.map(c => `<td>${c.render ? c.render(row) : escapeHtml(row[c.key] ?? '')}</td>`).join('') + '</tr>').join('') + '</tbody>'
      ].join('');
    };
    const render = () => {
      const data = state.data;
      const stats = data.stats || {};
      document.getElementById('metrics').innerHTML = Object.entries(stats).map(([key, value]) => (
        `<div class="metric"><span class="subtle">${escapeHtml(key)}</span><strong>${escapeHtml(value)}</strong></div>`
      )).join('');
      renderTable('accounts', [
        {key: 'account_id', label: 'Account'},
        {key: 'provider', label: 'Provider'},
        {key: 'status', label: 'Status', render: r => statusPill(r.status)},
        {key: 'base_url', label: 'Base URL'},
        {key: 'secret_ref', label: 'Secret Ref'}
      ], data.accounts);
      renderTable('models', [
        {key: 'model_id', label: 'Model'},
        {key: 'status', label: 'Status', render: r => statusPill(r.status)},
        {key: 'capabilities', label: 'Capabilities', render: r => escapeHtml((r.capabilities || []).join(', '))},
        {key: 'input_usd_per_million', label: 'Input'},
        {key: 'output_usd_per_million', label: 'Output'},
        {key: 'supports_batch', label: 'Batch'}
      ], data.models);
      renderTable('abilities', [
        {key: 'account_id', label: 'Account'},
        {key: 'model_id', label: 'Model'},
        {key: 'enabled', label: 'Enabled'},
        {key: 'priority', label: 'Priority'},
        {key: 'weight', label: 'Weight'},
        {key: 'model_mapping', label: 'Provider Model'}
      ], data.abilities);
      renderTable('profiles', [
        {key: 'project_id', label: 'Project'},
        {key: 'default_capabilities', label: 'Capabilities', render: r => escapeHtml((r.default_capabilities || []).join(', '))},
        {key: 'max_cost_usd', label: 'Max Cost'},
        {key: 'preferred_providers', label: 'Providers', render: r => escapeHtml((r.preferred_providers || []).join(', '))},
        {key: 'preferred_accounts', label: 'Accounts', render: r => escapeHtml((r.preferred_accounts || []).join(', '))}
      ], data.profiles);
      renderTable('overrides', [
        {key: 'project_id', label: 'Project'},
        {key: 'account_id', label: 'Account'},
        {key: 'model_id', label: 'Model'},
        {key: 'enabled', label: 'Enabled'},
        {key: 'priority', label: 'Priority'},
        {key: 'weight', label: 'Weight'}
      ], data.overrides);
    };
    const refresh = async () => {
      state.data = await api('/api/state');
      render();
    };
    document.getElementById('refresh').addEventListener('click', refresh);
    for (const form of document.querySelectorAll('form[data-endpoint]')) {
      form.addEventListener('submit', async (event) => {
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
    refresh();
  </script>
</body>
</html>
"""
