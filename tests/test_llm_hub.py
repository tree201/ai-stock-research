from tempfile import TemporaryDirectory
import json
import os
import unittest
from unittest import mock

from stock_research.llm import OpenAICompatibleProvider
from stock_research.llm_catalog import (
    THINKING_LEVELS,
    levels_for,
    normalize_level,
    params_for_level,
    parse_thinking_levels,
)
from stock_research.storage import SQLiteStore


class LlmCatalogTests(unittest.TestCase):
    def test_normalize_level_clamps_unknown_values(self) -> None:
        self.assertEqual(normalize_level("HIGH"), "high")
        self.assertEqual(normalize_level(""), "off")
        self.assertEqual(normalize_level(None), "off")
        self.assertEqual(normalize_level("extreme"), "off")

    def test_parse_thinking_levels_drops_invalid_entries(self) -> None:
        parsed = parse_thinking_levels({"off": {"enable_thinking": False}, "bogus": {"x": 1}, "low": "oops"})
        self.assertEqual(parsed, {"off": {"enable_thinking": False}})

    def test_parse_thinking_levels_accepts_json_string(self) -> None:
        raw = json.dumps({"low": {"thinking": {"type": "enabled"}}})
        self.assertEqual(levels_for(raw), ["low"])

    def test_params_for_level_returns_mapped_fragment(self) -> None:
        levels = {"off": {"enable_thinking": False}, "high": {"reasoning_effort": "high"}}
        self.assertEqual(params_for_level(levels, "high"), {"reasoning_effort": "high"})
        self.assertEqual(params_for_level(levels, "low"), {})
        self.assertEqual(params_for_level({}, "off"), {})

    def test_thinking_levels_are_canonical(self) -> None:
        self.assertEqual(THINKING_LEVELS, ("off", "low", "medium", "high"))


class LlmHubStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = TemporaryDirectory()
        self.store = SQLiteStore(f"{self._dir.name}/research.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self._dir.cleanup()

    def test_builtin_catalog_is_seeded_once_and_user_edits_survive(self) -> None:
        providers = self.store.list_llm_providers()
        self.assertTrue(any(provider["name"] == "DeepSeek" for provider in providers))
        deepseek = next(p for p in providers if p["name"] == "DeepSeek")
        models = self.store.list_llm_models(deepseek["id"])
        self.assertTrue(any(model["model_id"] == "deepseek-chat" for model in models))
        self.store.update_llm_provider(deepseek["id"], api_key="sk-user")
        self.store.remove_llm_model(
            next(m["id"] for m in models if m["model_id"] == "deepseek-reasoner")
        )
        reopened = SQLiteStore(f"{self._dir.name}/research.sqlite3")
        try:
            again = next(p for p in reopened.list_llm_providers() if p["name"] == "DeepSeek")
            self.assertEqual(again["api_key"], "sk-user")
            model_ids = [m["model_id"] for m in reopened.list_llm_models(again["id"])]
            self.assertNotIn("deepseek-reasoner", model_ids)
            self.assertIn("deepseek-chat", model_ids)
        finally:
            reopened.close()

    def test_add_remove_provider_and_models(self) -> None:
        provider = self.store.add_llm_provider("SiliconFlow", "https://api.siliconflow.cn/v1/", "sk-x")
        self.assertEqual(provider["base_url"], "https://api.siliconflow.cn/v1")
        model = self.store.add_llm_model(provider["id"], "Qwen/Qwen3-32B", "Qwen3-32B", {"off": {"enable_thinking": False}})
        self.assertEqual(model["display_name"], "Qwen3-32B")
        with self.assertRaises(ValueError):
            self.store.add_llm_model(provider["id"], "Qwen/Qwen3-32B")
        self.store.remove_llm_provider(provider["id"])
        self.assertIsNone(self.store.get_llm_provider(provider["id"]))
        self.assertIsNone(self.store.get_llm_model(model["id"]))

    def test_settings_roundtrip(self) -> None:
        self.assertIsNone(self.store.get_setting("llm.selection"))
        self.store.set_setting("llm.selection", {"model_row_id": 1, "level": "high"})
        self.assertEqual(self.store.get_setting("llm.selection"), {"model_row_id": 1, "level": "high"})

    def test_provider_api_key_required_for_create(self) -> None:
        from stock_research import service

        with self.assertRaises(ValueError):
            service.save_llm_provider_payload({"name": "NoKey", "base_url": "https://example.com/v1"})


class LlmSelectionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = TemporaryDirectory()
        self.db_path = f"{self._dir.name}/research.sqlite3"
        self._env_patch = mock.patch.dict(os.environ, {"AI_STOCK_DB": self.db_path}, clear=False)
        self._env_patch.start()
        # 清掉可能来自外层环境的关键字，保证测试自洽
        os.environ.pop("AI_STOCK_LLM_API_KEY", None)
        os.environ.pop("DEEPSEEK_API_KEY", None)
        self.store = SQLiteStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self._env_patch.stop()
        self._dir.cleanup()

    def test_selection_resolves_extra_params(self) -> None:
        from stock_research import service

        provider = next(p for p in self.store.list_llm_providers() if "智谱" in p["name"])
        self.store.update_llm_provider(provider["id"], api_key="sk-zhipu")
        model = next(m for m in self.store.list_llm_models(provider["id"]) if m["model_id"] == "glm-4.6")
        self.store.set_setting("llm.selection", {"model_row_id": model["id"], "provider_id": provider["id"], "model_id": "glm-4.6", "level": "high"})

        stored = service.stored_llm_selection()
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["model"], "glm-4.6")
        self.assertEqual(stored["level"], "high")
        self.assertEqual(stored["extra_params"], {"thinking": {"type": "enabled"}})

        resolved = service.resolve_provider()
        self.assertIsInstance(resolved, OpenAICompatibleProvider)
        assert isinstance(resolved, OpenAICompatibleProvider)
        self.assertEqual(resolved.model, "glm-4.6")
        self.assertEqual(resolved.extra_params, {"thinking": {"type": "enabled"}})

    def test_selection_without_api_key_falls_back_to_env(self) -> None:
        from stock_research import service

        self.assertIsNone(service.stored_llm_selection())
        with self.assertRaises(service.ModelNotConfiguredError):
            service.resolve_provider()

    def test_config_payload_masks_keys(self) -> None:
        from stock_research import service

        provider = self.store.list_llm_providers()[0]
        self.store.update_llm_provider(provider["id"], api_key="sk-secret")
        payload = service.llm_config_payload()
        serialized = json.dumps(payload)
        self.assertNotIn("sk-secret", serialized)
        for entry in payload["providers"]:
            if entry["id"] == provider["id"]:
                self.assertIsNone(entry["api_key"])
                self.assertTrue(entry["has_api_key"])
            else:
                self.assertFalse(entry["has_api_key"])

    def test_set_selection_updates_recent(self) -> None:
        from stock_research import service

        provider = self.store.list_llm_providers()[0]
        models = self.store.list_llm_models(provider["id"])
        service.set_llm_selection_payload({"model_row_id": models[0]["id"], "level": "low"})
        service.set_llm_selection_payload({"model_row_id": models[1]["id"], "level": "off"})
        payload = service.llm_config_payload()
        self.assertEqual(payload["selection"]["model_row_id"], models[1]["id"])
        self.assertEqual(payload["selection"]["level"], "off")
        self.assertEqual([e["model_row_id"] for e in payload["recent"]], [models[1]["id"], models[0]["id"]])


class LlmExtraParamsRequestTests(unittest.TestCase):
    def test_extra_params_merge_into_payload(self) -> None:
        captured: dict = {}

        def fake_opener(request, timeout=None):  # noqa: ANN001
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            raise RuntimeError("stop before network")

        provider = OpenAICompatibleProvider(
            "https://api.example.com/v1", "sk-test", "glm-4.6",
            opener=fake_opener, timeout=1,
            extra_params={"thinking": {"type": "enabled"}},
        )
        from stock_research.llm import LLMError

        with self.assertRaises(LLMError):
            provider.answer("问题", "背景")
        self.assertEqual(captured["payload"]["thinking"], {"type": "enabled"})
        self.assertEqual(captured["payload"]["model"], "glm-4.6")

    def test_no_extra_params_keeps_payload_clean(self) -> None:
        captured: dict = {}

        def fake_opener(request, timeout=None):  # noqa: ANN001
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            raise RuntimeError("stop before network")

        provider = OpenAICompatibleProvider(
            "https://api.example.com/v1", "sk-test", "deepseek-chat",
            opener=fake_opener, timeout=1,
        )
        from stock_research.llm import LLMError

        with self.assertRaises(LLMError):
            provider.answer("问题", "背景")
        self.assertNotIn("thinking", captured["payload"])
        self.assertNotIn("enable_thinking", captured["payload"])


if __name__ == "__main__":
    unittest.main()
