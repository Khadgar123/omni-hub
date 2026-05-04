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

from omni_hub.gui import (
    INDEX_HTML,
    OFFICIAL_PROVIDER_PRESETS,
    _fetch_models_from_payload,
    _parse_kimi_balance,
    _model_entries,
    _parse_cursorlink_balance,
    _parse_newapi_balance,
    _provider_script_response,
    create_gui_server,
)
from omni_hub.provider_router import ProviderAccount, ProviderRouterStore
from omni_hub.secrets import store_api_key


class GuiServerTests(unittest.TestCase):
    def test_gui_index_uses_user_facing_dashboard_terms(self) -> None:
        self.assertIn("模型配置", INDEX_HTML)
        self.assertIn("项目编组", INDEX_HTML)
        self.assertIn("监控检测", INDEX_HTML)
        self.assertIn("模型厂商", INDEX_HTML)
        self.assertIn("API Key", INDEX_HTML)
        self.assertIn("添加渠道", INDEX_HTML)
        self.assertIn("默认模型", INDEX_HTML)
        self.assertIn("Codex 配置", INDEX_HTML)
        self.assertIn('id="provider-modal"', INDEX_HTML)
        self.assertIn("导出 export 脚本", INDEX_HTML)
        self.assertIn("拖拽", INDEX_HTML)
        self.assertIn("测试连接", INDEX_HTML)
        self.assertIn("发现模型", INDEX_HTML)
        self.assertIn("接口地址", INDEX_HTML)
        self.assertIn("API 格式", INDEX_HTML)
        self.assertIn("用量超时秒数", INDEX_HTML)
        self.assertIn("用量重试次数", INDEX_HTML)
        self.assertIn("并发上限", INDEX_HTML)
        self.assertIn("项目模型包", INDEX_HTML)
        self.assertIn("保存模型顺序", INDEX_HTML)
        self.assertIn("项目模型配置", INDEX_HTML)
        self.assertIn("可选模型", INDEX_HTML)
        self.assertIn("project-list", INDEX_HTML)
        self.assertIn("data-model-add", INDEX_HTML)
        self.assertIn("/api/project-model-orders", INDEX_HTML)
        self.assertIn("项目接入文件", INDEX_HTML)
        self.assertIn("最近模型探测延迟", INDEX_HTML)
        self.assertIn("开始定期查额度", INDEX_HTML)
        self.assertIn("模型级健康", INDEX_HTML)
        self.assertIn("额度状态", INDEX_HTML)
        self.assertIn("data-model-action", INDEX_HTML)
        self.assertIn("全部查额度", INDEX_HTML)
        self.assertIn("复制条目", INDEX_HTML)
        self.assertIn("删除", INDEX_HTML)
        self.assertIn("CursorLink", INDEX_HTML)
        self.assertIn("待填写 Key", INDEX_HTML)
        self.assertIn("测0-10并发/RPS", INDEX_HTML)
        self.assertIn("button-spin", INDEX_HTML)
        self.assertIn("withButtonLoading", INDEX_HTML)
        self.assertIn('data-loading="true"', INDEX_HTML)
        self.assertIn('data-channel-action="configure"', INDEX_HTML)
        self.assertNotIn("渠道模板", INDEX_HTML)
        self.assertNotIn("当前厂商自动切换队列", INDEX_HTML)
        self.assertIn("Skills", INDEX_HTML)
        self.assertIn('id="toast"', INDEX_HTML)
        self.assertNotIn("使用选择", INDEX_HTML)
        self.assertNotIn("Provider 账号", INDEX_HTML)
        self.assertNotIn("Agent 规划", INDEX_HTML)

    def test_gui_official_provider_presets_cover_major_model_vendors(self) -> None:
        names = {item["name"] for item in OFFICIAL_PROVIDER_PRESETS}
        self.assertGreaterEqual(
            names,
            {"OpenAI", "Claude", "Qwen", "DeepSeek", "Kimi", "GLM", "MiniMax"},
        )

    def test_gui_official_presets_include_cursorlink_starter_channels(self) -> None:
        presets = {item["name"]: item for item in OFFICIAL_PROVIDER_PRESETS}
        openai_channels = presets["OpenAI"].get("starter_channels", [])
        claude_channels = presets["Claude"].get("starter_channels", [])

        self.assertEqual(openai_channels[0]["base_url"], "https://apicursor.com/v1")
        self.assertIn("cx-5.5", openai_channels[0]["models"])
        self.assertEqual(openai_channels[0]["usage_template"], "cursorlink")
        self.assertEqual(claude_channels[0]["api_format"], "openai_chat")
        self.assertIn("so-4.6", claude_channels[0]["models"])

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

    def test_gui_relay_channel_does_not_overwrite_official_default_slot(self) -> None:
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
                            "name": "OpenAI Relay",
                            "base_url": "https://api.vip1129.cc",
                            "api_key": "sk-relay-secret",
                            "model_ids": "gpt-5.5",
                        },
                    )

                    self.assertEqual(
                        configured["account"]["account_id"],
                        "openai-api-vip1129-cc",
                    )
                    self.assertEqual(configured["account"]["account_group"], "relay")
                    self.assertIn("channel_group=relay", configured["account"]["notes"])
                    state = _get_json(f"{base_url}/api/state")
                    account_ids = {item["account_id"] for item in state["accounts"]}
                    self.assertNotIn("openai-main", account_ids)
                finally:
                    server.shutdown()
                    thread.join(timeout=2)
                    server.server_close()

    def test_gui_can_duplicate_and_delete_provider_channel(self) -> None:
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
                            "base_url": "https://api.openai.com/v1",
                            "api_key": "sk-test",
                            "model_ids": "gpt-a\ngpt-b",
                            "priority": 90,
                        },
                    )
                    copied = _post_json(
                        f"{base_url}/api/provider-duplicate",
                        {"account_id": configured["account"]["account_id"]},
                    )

                    self.assertEqual(copied["account"]["account_id"], "openai-main-copy")
                    self.assertEqual(
                        copied["account"]["secret_ref"],
                        configured["account"]["secret_ref"],
                    )
                    self.assertEqual(len(copied["abilities"]), 2)
                    self.assertEqual(copied["abilities"][0]["priority"], 89)

                    _post_json(
                        f"{base_url}/api/provider-delete",
                        {"account_id": "openai-main-copy"},
                    )
                    state = _get_json(f"{base_url}/api/state")
                    account_ids = {account["account_id"] for account in state["accounts"]}
                    self.assertNotIn("openai-main-copy", account_ids)
                finally:
                    server.shutdown()
                    thread.join(timeout=2)
                    server.server_close()

    def test_gui_channel_capability_probe_records_concurrency_and_batch(self) -> None:
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
                            "base_url": "https://api.openai.com/v1",
                            "api_key": "sk-test",
                            "model_ids": "gpt-a",
                            "priority": 90,
                            "max_concurrency": "9",
                            "rpm_limit": "999",
                        },
                    )
                    with patch(
                        "omni_hub.gui._send_stream_check_once",
                        return_value={
                            "ok": True,
                            "http_status": 200,
                            "latency_ms": 12,
                            "api_format": "openai_chat",
                            "endpoint": "https://api.openai.com/v1/chat/completions",
                        },
                    ), patch(
                        "omni_hub.gui._probe_batch_support",
                        return_value={
                            "supported": True,
                            "status": "ok",
                            "endpoint": "https://api.openai.com/v1/batches",
                            "http_status": 200,
                        },
                    ):
                        result = _post_json(
                            f"{base_url}/api/channel-capability-probe",
                            {
                                "account_id": configured["account"]["account_id"],
                                "max_concurrency": 3,
                                "max_rps": 3,
                                "rate_window_secs": 0.01,
                            },
                        )

                    self.assertTrue(result["success"])
                    self.assertEqual(result["concurrency"]["max_passed"], 3)
                    self.assertEqual(result["rate"]["max_passed"], 3)
                    self.assertTrue(result["batch"]["supported"])
                    self.assertIn("max_concurrency=3", result["account"]["notes"])
                    self.assertIn("rps_limit=3", result["account"]["notes"])
                    self.assertIn("rpm_limit=180", result["account"]["notes"])
                    self.assertNotIn("max_concurrency=9", result["account"]["notes"])
                    self.assertNotIn("rpm_limit=999", result["account"]["notes"])
                    self.assertIn("batch_support=true", result["account"]["notes"])
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

    def test_gui_project_model_orders_resolve_by_global_channel_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            server = create_gui_server(tmpdir, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
            try:
                for account_id, name in [
                    ("openai-low", "OpenAI Low"),
                    ("openai-high", "OpenAI High"),
                ]:
                    _post_json(
                        f"{base_url}/api/providers",
                        {
                            "account_id": account_id,
                            "provider": "openai",
                            "name": name,
                            "base_url": "https://api.openai.com/v1",
                            "secret_ref": f"env:{account_id.upper().replace('-', '_')}_KEY",
                        },
                    )
                _post_json(
                    f"{base_url}/api/channel-model",
                    {
                        "account_id": "openai-low",
                        "model_id": "gpt-5.5-mini",
                        "capabilities": ["text"],
                        "priority": 40,
                    },
                )
                _post_json(
                    f"{base_url}/api/channel-model",
                    {
                        "account_id": "openai-high",
                        "model_id": "gpt-5.5-mini",
                        "capabilities": ["text"],
                        "priority": 90,
                    },
                )

                saved = _post_json(
                    f"{base_url}/api/project-model-orders",
                    {
                        "project_id": "omni-hub",
                        "orders": [
                            {
                                "slot": "default",
                                "model_ids": ["gpt-5.5-mini"],
                            }
                        ],
                    },
                )
                resolved = _post_json(
                    f"{base_url}/api/project-resolve",
                    {"project_id": "omni-hub", "slot": "default"},
                )

                self.assertEqual(saved["orders"][0]["model_ids"], ["gpt-5.5-mini"])
                self.assertEqual(
                    saved["bundle"]["slot_routes"][0]["selected"]["account_id"],
                    "openai-high",
                )
                self.assertEqual(
                    saved["bundle"]["integration"]["manifest_path"],
                    ".omni/omni-hub.project.json",
                )
                self.assertEqual(
                    saved["bundle"]["integration"]["manifest"]["slots"]["default"][
                        "selected"
                    ]["account_id"],
                    "openai-high",
                )
                self.assertEqual(resolved["selected"]["account_id"], "openai-high")
                self.assertEqual(resolved["selected"]["secret_ref"], "env:OPENAI_HIGH_KEY")
                self.assertNotIn("sk-", json.dumps(saved))
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

    def test_gui_model_fetch_prefers_saved_local_secret_over_stale_form_ref(self) -> None:
        with patch.dict(os.environ, {"OMNI_HUB_SECRET_BACKEND": "memory"}):
            with tempfile.TemporaryDirectory() as tmpdir:
                store = ProviderRouterStore(tmpdir)
                secret_ref = store_api_key("openai-main", "sk-local-secret")
                store.upsert_account(
                    ProviderAccount(
                        account_id="openai-main",
                        provider="openai",
                        name="OpenAI Main",
                        base_url="https://api.openai.com/v1",
                        secret_ref=secret_ref,
                    )
                )

                with patch(
                    "omni_hub.gui._fetch_models_from_candidates",
                    return_value=[{"id": "gpt-test", "name": "gpt-test"}],
                ) as fetch:
                    fetched = _fetch_models_from_payload(
                        {
                            "account_id": "openai-main",
                            "provider": "openai",
                            "base_url": "https://relay.example.com/v1",
                            "secret_ref": "env:OPENAI_API_KEY",
                        },
                        store,
                    )

                self.assertEqual(fetched["models"][0]["id"], "gpt-test")
                self.assertEqual(fetch.call_args.args[1], "sk-local-secret")
                self.assertEqual(
                    fetch.call_args.args[0].secret_ref,
                    "local:omni-hub/openai-main",
                )
                self.assertEqual(
                    fetch.call_args.args[0].base_url,
                    "https://relay.example.com/v1",
                )

    def test_gui_model_fetch_allows_unsaved_draft_with_raw_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ProviderRouterStore(tmpdir)
            with patch(
                "omni_hub.gui._fetch_models_from_candidates",
                return_value=[{"id": "draft-model", "name": "draft-model"}],
            ) as fetch:
                fetched = _fetch_models_from_payload(
                    {
                        "account_id": "openai-main",
                        "provider": "openai",
                        "name": "OpenAI Draft",
                        "base_url": "https://api.example.com/v1",
                        "api_key": "sk-draft-secret",
                    },
                    store,
                )

            self.assertEqual(fetched["models"][0]["id"], "draft-model")
            self.assertEqual(fetch.call_args.args[1], "sk-draft-secret")
            self.assertEqual(fetch.call_args.args[0].account_id, "openai-main")

    def test_gui_model_entries_accept_common_model_response_shapes(self) -> None:
        self.assertEqual(_model_entries({"data": [{"id": "gpt-test"}]})[0]["id"], "gpt-test")
        self.assertEqual(_model_entries({"models": ["gpt-list"]})[0], "gpt-list")
        self.assertEqual(
            _model_entries({"data": {"models": [{"id": "nested"}]}})[0]["id"],
            "nested",
        )

    def test_gui_provider_script_resolves_local_secret_and_codex_toml(self) -> None:
        with patch.dict(os.environ, {"OMNI_HUB_SECRET_BACKEND": "memory"}):
            with tempfile.TemporaryDirectory() as tmpdir:
                store = ProviderRouterStore(tmpdir)
                secret_ref = store_api_key("openai-main", "sk-script-secret")
                account = store.upsert_account(
                    ProviderAccount(
                        account_id="openai-main",
                        provider="openai",
                        name="OpenAI Main",
                        base_url="https://api.example.com",
                        secret_ref=secret_ref,
                        notes="default_model=gpt-5.5\nmodel_reasoning_effort=xhigh",
                    )
                )
                shell = _provider_script_response(
                    account,
                    store,
                    {"account_id": "openai-main", "format": "shell"},
                )
                toml = _provider_script_response(
                    account,
                    store,
                    {"account_id": "openai-main", "format": "codex_toml"},
                )

                self.assertTrue(shell["contains_secret"])
                self.assertIn("sk-script-secret", shell["script"])
                self.assertFalse(toml["contains_secret"])
                self.assertNotIn("sk-script-secret", toml["script"])
                self.assertIn('model = "gpt-5.5"', toml["script"])
                self.assertIn('model_reasoning_effort = "xhigh"', toml["script"])

    def test_gui_newapi_balance_parser_matches_cc_switch_template(self) -> None:
        parsed = _parse_newapi_balance(
            {
                "success": True,
                "data": {
                    "group": "default",
                    "quota": 1_000_000,
                    "used_quota": 500_000,
                },
            }
        )

        self.assertEqual(parsed[0]["plan_name"], "default")
        self.assertEqual(parsed[0]["remaining"], 2.0)
        self.assertEqual(parsed[0]["used"], 1.0)
        self.assertEqual(parsed[0]["total"], 3.0)

    def test_gui_generic_balance_parser_matches_cc_switch_usage_script(self) -> None:
        from omni_hub.gui import _parse_generic_balance

        parsed = _parse_generic_balance(
            {"quota": {"remaining": 68.73, "unit": "USD"}, "is_active": True}
        )

        self.assertEqual(parsed[0]["remaining"], 68.73)
        self.assertEqual(parsed[0]["unit"], "USD")
        self.assertTrue(parsed[0]["is_valid"])

    def test_gui_cursorlink_balance_parser_matches_query_credits(self) -> None:
        parsed = _parse_cursorlink_balance(
            {
                "code": 0,
                "credits": 2260.86,
                "totalCreditsUsed": 2454.17,
                "totalRequests": 6673,
                "plan": "pro",
                "expiresAt": "2026-06-02 21:37:46",
                "status": "active",
                "remainDays": 30,
            }
        )

        self.assertEqual(parsed[0]["plan_name"], "pro")
        self.assertEqual(parsed[0]["remaining"], 2260.86)
        self.assertEqual(parsed[0]["used"], 2454.17)
        self.assertAlmostEqual(parsed[0]["total"], 4715.03)
        self.assertEqual(parsed[0]["extra"]["total_requests"], 6673)

    def test_gui_kimi_balance_parser_matches_official_shape(self) -> None:
        parsed = _parse_kimi_balance(
            {
                "code": 0,
                "data": {
                    "available_balance": 49.58894,
                    "voucher_balance": 46.58893,
                    "cash_balance": 3.00001,
                },
                "scode": "0x0",
                "status": True,
            }
        )

        self.assertEqual(parsed[0]["plan_name"], "Kimi")
        self.assertEqual(parsed[0]["remaining"], 49.58894)
        self.assertEqual(parsed[0]["unit"], "USD")
        self.assertEqual(parsed[0]["extra"]["voucher_balance"], 46.58893)

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
