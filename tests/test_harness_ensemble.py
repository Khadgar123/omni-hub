from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.harness.ensemble import (
    EnsembleConfig,
    ModelSpec,
    load_default_models,
    run_ensemble,
)


def _stub_http_factory(text_by_model: dict[str, str]):
    """Return a fake http_call that decodes the request body and returns the
    pre-canned assistant text for the model in the payload."""

    captured: list[dict] = []

    def fake_http(request: Request, timeout: float) -> dict:
        body = json.loads(request.data.decode("utf-8"))
        model = body["model"]
        captured.append({"model": model, "messages": body["messages"], "timeout": timeout})
        text = text_by_model.get(model, f"[no-stub:{model}]")
        return {
            "id": f"chatcmpl-{model}",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
        }

    fake_http.captured = captured  # type: ignore[attr-defined]
    return fake_http


class RunEnsembleTests(unittest.TestCase):
    def test_three_models_three_candidates(self) -> None:
        cfg = EnsembleConfig(
            models=[
                ModelSpec(name="claude"),
                ModelSpec(name="deepseek"),
                ModelSpec(name="codex"),
            ],
            system_prompt="be terse",
        )
        http = _stub_http_factory(
            {
                "claude": "C-answer",
                "deepseek": "D-answer",
                "codex": "X-answer",
            }
        )
        record = run_ensemble(
            "hello?",
            cfg,
            task_id="t-1",
            secret_resolver=lambda ref: "",  # no auth in tests
            http_call=http,
        )

        self.assertEqual(record.task_id, "t-1")
        self.assertEqual(len(record.candidates), 3)
        models = sorted(c.model for c in record.candidates)
        self.assertEqual(models, ["claude", "codex", "deepseek"])
        texts = {c.model: c.text for c in record.candidates}
        self.assertEqual(texts["claude"], "C-answer")
        self.assertEqual(texts["deepseek"], "D-answer")
        self.assertEqual(texts["codex"], "X-answer")
        for cand in record.candidates:
            self.assertIsNone(cand.error)
            self.assertGreaterEqual(cand.elapsed_ms, 0)
            self.assertEqual(cand.failure_tags, [])

    def test_system_prompt_is_first_message_when_set(self) -> None:
        cfg = EnsembleConfig(
            models=[ModelSpec(name="m1")],
            system_prompt="be terse",
        )
        http = _stub_http_factory({"m1": "ok"})
        run_ensemble("hi", cfg, secret_resolver=lambda ref: "", http_call=http)
        messages = http.captured[0]["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "be terse")
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "hi")

    def test_system_prompt_omitted_when_empty(self) -> None:
        cfg = EnsembleConfig(models=[ModelSpec(name="m1")], system_prompt="")
        http = _stub_http_factory({"m1": "ok"})
        run_ensemble("hi", cfg, secret_resolver=lambda ref: "", http_call=http)
        messages = http.captured[0]["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")

    def test_one_failing_model_does_not_break_others(self) -> None:
        cfg = EnsembleConfig(
            models=[
                ModelSpec(name="good"),
                ModelSpec(name="bad"),
            ]
        )

        def http(request: Request, timeout: float) -> dict:
            body = json.loads(request.data.decode("utf-8"))
            if body["model"] == "bad":
                raise json.JSONDecodeError("nope", "", 0)
            return {
                "choices": [
                    {"message": {"role": "assistant", "content": "G"}}
                ]
            }

        record = run_ensemble(
            "hi", cfg, secret_resolver=lambda ref: "", http_call=http
        )
        by_model = {c.model: c for c in record.candidates}
        self.assertEqual(by_model["good"].text, "G")
        self.assertIsNone(by_model["good"].error)
        self.assertIsNotNone(by_model["bad"].error)
        self.assertIn("invalid_response", by_model["bad"].failure_tags)

    def test_empty_response_tagged(self) -> None:
        cfg = EnsembleConfig(models=[ModelSpec(name="m1")])
        http = _stub_http_factory({"m1": ""})
        record = run_ensemble(
            "hi", cfg, secret_resolver=lambda ref: "", http_call=http
        )
        cand = record.candidates[0]
        self.assertEqual(cand.text, "")
        self.assertIn("empty_response", cand.failure_tags)

    def test_run_ensemble_requires_at_least_one_model(self) -> None:
        with self.assertRaises(ValueError):
            run_ensemble(
                "hi",
                EnsembleConfig(models=[]),
                secret_resolver=lambda ref: "",
                http_call=lambda r, t: {},
            )


class LoadDefaultModelsTests(unittest.TestCase):
    def test_uses_defaults_json_provider_models(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = root / "api-management"
            api.mkdir(parents=True)
            (api / "defaults.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "default_provider": "deepseek",
                        "default_model": "deepseek-v4-pro",
                        "providers": {
                            "deepseek": {
                                "secret_ref": "local:test/key",
                                "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            models = load_default_models(root)
            names = [m.name for m in models]
            self.assertEqual(names, ["deepseek-v4-pro", "deepseek-v4-flash"])
            for spec in models:
                self.assertEqual(spec.secret_ref, "local:test/key")
                self.assertEqual(spec.base_url, "http://127.0.0.1:8080")

    def test_falls_back_when_defaults_missing(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            models = load_default_models(Path(tmp))
            self.assertEqual([m.name for m in models], ["deepseek-v4-pro"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
