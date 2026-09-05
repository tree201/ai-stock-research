from datetime import date
from io import BytesIO
import json
import unittest
import urllib.error

from stock_research.llm import AnalysisRequest, DeepSeekProvider, HeuristicLLMProvider, LLMError, OpenAICompatibleProvider


class _Response:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return self.payload


class _HttpErrorResponse:
    """urlopen double that raises HTTPError carrying a JSON error body."""

    def __init__(self, status: int, body: dict):
        self._error = urllib.error.HTTPError(
            "https://example.test/v1/chat/completions", status, "Bad Request", {},
            BytesIO(json.dumps(body).encode("utf-8")),
        )

    def __call__(self, *_args, **_kwargs):
        raise self._error


class LLMTests(unittest.TestCase):
    def test_heuristic_provider_returns_structured_claims(self) -> None:
        response = HeuristicLLMProvider().analyze(AnalysisRequest("研究公司", date(2025, 12, 31), ({"evidence_id": "e1", "text": "Risk: competition"},)))
        self.assertEqual(response.claims[0].category, "risk")
        self.assertEqual(response.claims[0].evidence_ids, ("e1",))

    def test_openai_compatible_provider_validates_citations(self) -> None:
        payload = {
            "choices": [{"message": {"content": json.dumps({"claims": [{"category": "business", "text": "Cloud growth matters", "evidence_ids": ["e1"], "confidence": 0.8}]})}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        }
        provider = OpenAICompatibleProvider("https://example.test/v1", "key", "model", opener=lambda *_args, **_kwargs: _Response(payload))
        response = provider.analyze(AnalysisRequest("研究公司", date(2025, 12, 31), ({"evidence_id": "e1", "text": "Cloud growth"},)))
        self.assertEqual(response.claims[0].evidence_ids, ("e1",))
        self.assertEqual(response.usage["prompt_tokens"], 120)
        self.assertEqual(response.usage["completion_tokens"], 30)
        self.assertEqual(response.usage["prompt_version"], "analyze-v1")
        self.assertIn("elapsed_ms", response.usage)

        invalid = {"choices": [{"message": {"content": json.dumps({"claims": [{"category": "business", "text": "unsupported", "evidence_ids": ["missing"], "confidence": 0.8}]})}}]}
        provider = OpenAICompatibleProvider("https://example.test/v1", "key", "model", opener=lambda *_args, **_kwargs: _Response(invalid))
        with self.assertRaises(LLMError):
            provider.analyze(AnalysisRequest("研究公司", date(2025, 12, 31), ({"evidence_id": "e1", "text": "Cloud growth"},)))

    def test_http_error_body_is_surfaced_in_llm_error(self) -> None:
        """供应商 400 的响应体（如 DeepSeek 内容风控）必须透传，不能只给 Bad Request。"""
        provider = OpenAICompatibleProvider(
            "https://example.test/v1", "key", "model",
            opener=_HttpErrorResponse(400, {"error": {"message": "Content Exists Risk", "type": "invalid_request_error"}}),
        )
        with self.assertRaises(LLMError) as ctx:
            provider.analyze(AnalysisRequest("研究公司", date(2025, 12, 31), ({"evidence_id": "e1", "text": "Cloud growth"},)))
        self.assertIn("Content Exists Risk", str(ctx.exception))
        self.assertNotIn("Bad Request", str(ctx.exception))

    def test_deepseek_provider_uses_official_compatible_base_url(self) -> None:
        provider = DeepSeekProvider("key")
        self.assertEqual(provider.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(provider.model, "deepseek-chat")

    def test_openai_compatible_provider_answers_follow_up(self) -> None:
        payload = {"choices": [{"message": {"content": "报告显示净利润率约为 20%。"}}]}
        provider = OpenAICompatibleProvider("https://example.test/v1", "key", "model", opener=lambda *_args, **_kwargs: _Response(payload))
        self.assertEqual(provider.answer("利润率怎么看？", "净利润率：20%"), "报告显示净利润率约为 20%。")
