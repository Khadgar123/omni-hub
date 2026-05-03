from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.gui import INDEX_HTML, create_gui_server


class GuiServerTests(unittest.TestCase):
    def test_gui_index_uses_user_facing_dashboard_terms(self) -> None:
        self.assertIn("渠道模型", INDEX_HTML)
        self.assertIn("项目编组", INDEX_HTML)
        self.assertIn("使用选择", INDEX_HTML)
        self.assertIn("监控检测", INDEX_HTML)
        self.assertIn("按模型配置", INDEX_HTML)
        self.assertIn("模型 ID 默认手写", INDEX_HTML)
        self.assertIn("Skills", INDEX_HTML)
        self.assertIn('id="toast"', INDEX_HTML)
        self.assertNotIn("Base URL", INDEX_HTML)
        self.assertNotIn("Provider 账号", INDEX_HTML)
        self.assertNotIn("Agent 规划", INDEX_HTML)

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
