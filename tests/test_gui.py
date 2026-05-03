from pathlib import Path
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.gui import INDEX_HTML, OFFICIAL_PROVIDER_PRESETS, create_gui_server


class GuiServerTests(unittest.TestCase):
    def test_gui_index_uses_user_facing_dashboard_terms(self) -> None:
        self.assertIn("模型配置", INDEX_HTML)
        self.assertIn("项目编组", INDEX_HTML)
        self.assertIn("监控检测", INDEX_HTML)
        self.assertIn("模型厂商", INDEX_HTML)
        self.assertIn("API Key", INDEX_HTML)
        self.assertIn("添加渠道", INDEX_HTML)
        self.assertIn('id="provider-modal"', INDEX_HTML)
        self.assertIn("默认脚本", INDEX_HTML)
        self.assertIn("拖拽", INDEX_HTML)
        self.assertIn("自动切换队列", INDEX_HTML)
        self.assertIn("模型探测", INDEX_HTML)
        self.assertIn("发现模型", INDEX_HTML)
        self.assertIn("接口地址", INDEX_HTML)
        self.assertIn("API 格式", INDEX_HTML)
        self.assertIn("并发上限", INDEX_HTML)
        self.assertIn("项目模型包", INDEX_HTML)
        self.assertIn("实时延迟", INDEX_HTML)
        self.assertIn("Skills", INDEX_HTML)
        self.assertIn('id="toast"', INDEX_HTML)
        self.assertNotIn("使用选择", INDEX_HTML)
        self.assertNotIn("Provider 账号", INDEX_HTML)
        self.assertNotIn("Agent 规划", INDEX_HTML)

    def test_gui_official_provider_presets_cover_major_model_vendors(self) -> None:
        names = {item["name"] for item in OFFICIAL_PROVIDER_PRESETS}
        self.assertGreaterEqual(
            names,
            {"OpenAI", "Claude", "Qwen", "DeepSeek", "GLM", "MiniMax"},
        )

    def test_gui_api_state_and_agent_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            server = create_gui_server(tmpdir, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
            try:
                state = _get_json(f"{base_url}/api/state")
                self.assertEqual(state["stats"]["provider_accounts"], 0)

                _post_json(
                    f"{base_url}/api/providers",
                    {
                        "account_id": "openai-main",
                        "provider": "openai",
                        "name": "OpenAI Main",
                        "base_url": "https://api.openai.com/v1",
                        "secret_ref": "env:OPENAI_API_KEY",
                        "proxy_url": "http://127.0.0.1:7890",
                    },
                )
                _post_json(
                    f"{base_url}/api/models",
                    {
                        "model_id": "gpt-5.4",
                        "capabilities": ["text"],
                    },
                )
                _post_json(
                    f"{base_url}/api/route-abilities",
                    {
                        "account_id": "openai-main",
                        "model_id": "gpt-5.4",
                        "priority": 10,
                    },
                )
                plan = _post_json(
                    f"{base_url}/api/agent-plan",
                    {
                        "task": "summarize this context",
                        "capabilities": ["text"],
                        "output_tokens": 300,
                    },
                )

                self.assertEqual(plan["status"], "planned")
                self.assertEqual(plan["invocation"]["account_id"], "openai-main")
                self.assertEqual(plan["invocation"]["proxy_mode"], "configured")
                self.assertNotIn("task", plan["request"])
                self.assertIn("task_preview", plan["request"])
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()

    def test_gui_imports_model_pool_and_selects_without_task_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            server = create_gui_server(tmpdir, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
            try:
                _post_json(
                    f"{base_url}/api/providers",
                    {
                        "account_id": "openrouter-main",
                        "provider": "openrouter",
                        "name": "OpenRouter Main",
                        "base_url": "https://openrouter.ai/api/v1",
                        "secret_ref": "env:OPENROUTER_API_KEY",
                    },
                )
                imported = _post_json(
                    f"{base_url}/api/model-pool-import",
                    {"account_id": "openrouter-main"},
                )
                self.assertGreaterEqual(len(imported["imported"]), 4)

                selected = _post_json(
                    f"{base_url}/api/agent-select",
                    {"project_id": "", "mode": "code", "output_tokens": 300},
                )

                self.assertEqual(selected["status"], "planned")
                self.assertEqual(selected["invocation"]["account_id"], "openrouter-main")
                self.assertEqual(selected["invocation"]["proxy_mode"], "unset")
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()

    def test_gui_official_provider_config_stores_local_ref_routes(self) -> None:
        with patch.dict(os.environ, {"OMNI_HUB_SECRET_BACKEND": "memory"}):
            with tempfile.TemporaryDirectory() as tmpdir:
                server = create_gui_server(tmpdir, port=0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
                try:
                    configured = _post_json(
                        f"{base_url}/api/official-provider-config",
                        {
                            "provider": "openai",
                            "account_id": "openai-main",
                            "name": "OpenAI Main",
                            "api_key": "sk-test-raw-secret",
                            "model_ids": "gpt-5.4\ngpt-5.4-mini",
                            "priority": 90,
                            "api_format": "openai_chat",
                            "max_concurrency": "3",
                            "rpm_limit": "120",
                        },
                    )

                    self.assertEqual(
                        configured["account"]["secret_ref"],
                        "local:omni-hub/openai-main",
                    )
                    self.assertEqual(configured["secret_mode"], "local")
                    self.assertNotIn("sk-test", json.dumps(configured))
                    state = _get_json(f"{base_url}/api/state")
                    self.assertNotIn("sk-test", json.dumps(state))
                    self.assertEqual(len(configured["abilities"]), 2)
                    self.assertEqual(configured["abilities"][0]["priority"], 90)
                    self.assertEqual(configured["abilities"][1]["priority"], 85)
                    self.assertIn("api_format=openai_chat", configured["account"]["notes"])
                    self.assertIn("max_concurrency=3", configured["account"]["notes"])
                    self.assertIn("rpm_limit=120", configured["account"]["notes"])
                finally:
                    server.shutdown()
                    thread.join(timeout=2)
                    server.server_close()

    def test_gui_official_provider_config_can_store_local_secret_file_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            secret_file = Path(tmpdir) / "local-secrets.json"
            with patch.dict(
                os.environ,
                {
                    "OMNI_HUB_SECRET_BACKEND": "local",
                    "OMNI_HUB_SECRET_FILE": str(secret_file),
                },
            ):
                server = create_gui_server(tmpdir, port=0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
                try:
                    configured = _post_json(
                        f"{base_url}/api/official-provider-config",
                        {
                            "provider": "openai",
                            "account_id": "openai-local",
                            "name": "OpenAI Local",
                            "api_key": "sk-local-secret",
                            "model_ids": "gpt-5.4",
                        },
                    )

                    self.assertEqual(
                        configured["account"]["secret_ref"],
                        "local:omni-hub/openai-local",
                    )
                    self.assertEqual(configured["secret_mode"], "local")
                    self.assertNotIn("sk-local", json.dumps(configured))
                    state = _get_json(f"{base_url}/api/state")
                    self.assertNotIn("sk-local", json.dumps(state))
                    self.assertIn("sk-local-secret", secret_file.read_text())
                finally:
                    server.shutdown()
                    thread.join(timeout=2)
                    server.server_close()

    def test_gui_stream_check_records_model_health(self) -> None:
        with patch.dict(os.environ, {"OMNI_HUB_SECRET_BACKEND": "memory"}):
            with tempfile.TemporaryDirectory() as tmpdir:
                server = create_gui_server(tmpdir, port=0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
                try:
                    _post_json(
                        f"{base_url}/api/official-provider-config",
                        {
                            "provider": "openai",
                            "account_id": "openai-main",
                            "name": "OpenAI Main",
                            "api_key": "sk-test-raw-secret",
                            "model_ids": "gpt-5.4",
                            "priority": 90,
                        },
                    )
                    with patch(
                        "omni_hub.gui._send_stream_check_once",
                        return_value={
                            "ok": True,
                            "http_status": 200,
                            "latency_ms": 42,
                            "quota": {"x-ratelimit-remaining-requests": "99"},
                            "request_id": "req-test",
                            "api_format": "openai_chat",
                            "endpoint": "https://api.openai.com/v1/chat/completions",
                        },
                    ) as stream_check:
                        checked = _post_json(
                            f"{base_url}/api/model-probe",
                            {"account_id": "openai-main"},
                        )

                    self.assertEqual(checked["health"]["status"], "healthy")
                    self.assertEqual(checked["stream_check"]["status"], "operational")
                    self.assertEqual(checked["probe"]["status"], "operational")
                    self.assertEqual(checked["stream_check"]["api_format"], "openai_chat")
                    self.assertEqual(checked["model_health"]["model_id"], "gpt-5.4")
                    self.assertEqual(checked["model_health"]["latency_ms"], 42)
                    self.assertIn("x-ratelimit-remaining-requests", checked["model_health"]["last_error"])
                    stream_check.assert_called_once()
                    self.assertEqual(stream_check.call_args.args[1], "gpt-5.4")
                    self.assertEqual(stream_check.call_args.args[2], "sk-test-raw-secret")
                finally:
                    server.shutdown()
                    thread.join(timeout=2)
                    server.server_close()

    def test_gui_project_import_exports_runtime_model_bundle_without_raw_key(self) -> None:
        with patch.dict(os.environ, {"OMNI_HUB_SECRET_BACKEND": "memory"}):
            with tempfile.TemporaryDirectory() as tmpdir:
                server = create_gui_server(tmpdir, port=0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
                try:
                    _post_json(
                        f"{base_url}/api/official-provider-config",
                        {
                            "provider": "openai",
                            "account_id": "openai-main",
                            "name": "OpenAI Official",
                            "api_key": "sk-test-raw-secret",
                            "base_url": "https://api.openai.com/v1",
                            "model_ids": "gpt-5.4",
                            "priority": 90,
                            "max_concurrency": "4",
                            "rpm_limit": "100",
                        },
                    )
                    imported = _post_json(
                        f"{base_url}/api/project-import-routes",
                        {
                            "project_id": "omni-hub",
                            "scope": "selected_provider",
                            "provider": "openai",
                        },
                    )

                    self.assertEqual(imported["profile"]["project_id"], "omni-hub")
                    self.assertEqual(len(imported["routes"]), 1)
                    route = imported["bundle"]["routes"][0]
                    self.assertEqual(route["base_url"], "https://api.openai.com/v1")
                    self.assertEqual(route["secret_ref"], "local:omni-hub/openai-main")
                    self.assertEqual(route["max_concurrency"], "4")
                    self.assertIn("slot_routes", imported["bundle"])
                    self.assertEqual(imported["bundle"]["slot_routes"][0]["slot"], "default")
                    self.assertNotIn("sk-test", json.dumps(imported))
                finally:
                    server.shutdown()
                    thread.join(timeout=2)
                    server.server_close()

    def test_gui_model_fetch_candidates_match_cc_switch_shape(self) -> None:
        from omni_hub.gui import _build_models_url_candidates

        self.assertEqual(
            _build_models_url_candidates("https://api.example.com/v1"),
            ["https://api.example.com/v1/models"],
        )
        self.assertEqual(
            _build_models_url_candidates("https://api.deepseek.com/anthropic"),
            [
                "https://api.deepseek.com/anthropic/v1/models",
                "https://api.deepseek.com/v1/models",
                "https://api.deepseek.com/models",
            ],
        )

    def test_gui_adds_model_to_channel_with_manual_model_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            server = create_gui_server(tmpdir, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
            try:
                _post_json(
                    f"{base_url}/api/providers",
                    {
                        "account_id": "openrouter-main",
                        "provider": "openrouter",
                        "name": "OpenRouter Main",
                        "base_url": "https://openrouter.ai/api/v1",
                        "secret_ref": "env:OPENROUTER_API_KEY",
                    },
                )
                created = _post_json(
                    f"{base_url}/api/channel-model",
                    {
                        "account_id": "openrouter-main",
                        "model_id": "claude-sonnet-4.5",
                        "display_name": "Claude Sonnet",
                        "capabilities": ["text", "tools"],
                        "priority": 80,
                    },
                )

                self.assertEqual(created["model"]["model_id"], "claude-sonnet-4.5")
                self.assertEqual(created["ability"]["account_id"], "openrouter-main")
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()

    def test_gui_project_group_stores_agent_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            server = create_gui_server(tmpdir, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
            try:
                _post_json(
                    f"{base_url}/api/providers",
                    {
                        "account_id": "openrouter-main",
                        "provider": "openrouter",
                        "name": "OpenRouter Main",
                        "base_url": "https://openrouter.ai/api/v1",
                        "secret_ref": "env:OPENROUTER_API_KEY",
                    },
                )
                _post_json(
                    f"{base_url}/api/channel-model",
                    {
                        "account_id": "openrouter-main",
                        "model_id": "gpt-5.4",
                        "capabilities": ["text", "tools"],
                        "priority": 80,
                    },
                )
                grouped = _post_json(
                    f"{base_url}/api/project-group",
                    {
                        "project_id": "omni-hub",
                        "routes": [
                            {
                                "role": "代码Agent",
                                "account_id": "openrouter-main",
                                "model_id": "gpt-5.4",
                                "skills": "github, browser",
                                "priority": 90,
                            }
                        ],
                    },
                )

                self.assertEqual(grouped["routes"][0]["notes"], "代码Agent; skills=github, browser")
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()

    def test_gui_rejects_non_localhost_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                create_gui_server(tmpdir, host="0.0.0.0", port=0)

    def test_gui_api_rejects_raw_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            server = create_gui_server(tmpdir, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
            try:
                with self.assertRaises(HTTPError):
                    _post_json(
                        f"{base_url}/api/providers",
                        {
                            "account_id": "openai-main",
                            "provider": "openai",
                            "name": "OpenAI Main",
                            "base_url": "https://api.openai.com/v1",
                            "secret_ref": "sk-test-raw-secret",
                        },
                    )
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()


def _get_json(url: str) -> dict[str, object]:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
