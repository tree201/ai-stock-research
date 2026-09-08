from datetime import date
from io import BytesIO
import json
import unittest
import urllib.error

from http.client import RemoteDisconnected

from stock_research.llm import (
    AnalysisRequest,
    DeepSeekProvider,
    HeuristicLLMProvider,
    LLMError,
    OpenAICompatibleProvider,
    retry_policy,
)


class _Response:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return self.payload


class _SseResponse:
    """chat_json 流式双打：按 SSE data: 行逐块产出，并捕获请求体供断言。"""

    last_request: dict | None = None

    def __init__(self, chunks: list[dict]):
        lines = ["data: " + json.dumps(chunk) for chunk in chunks] + ["data: [DONE]"]
        self._lines = "\n\n".join(lines).encode("utf-8")

    def __call__(self, req, *_args, **_kwargs):
        _SseResponse.last_request = json.loads(req.data.decode("utf-8"))
        return self

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def __iter__(self):
        return iter(self._lines.split(b"\n"))


def _sse_chunk(delta_content: str | None, finish_reason: str | None = None) -> dict:
    choice: dict = {"delta": {"content": delta_content} if delta_content is not None else {}}
    if finish_reason:
        choice["finish_reason"] = finish_reason
    return {"choices": [choice]}


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

    def test_chat_json_streams_sse_and_accepts_fenced_json(self) -> None:
        """决策调用走流式 SSE（思考模式防挂死），```json 围栏内容剥离后解析。"""
        provider = OpenAICompatibleProvider(
            "https://example.test/v1", "key", "model",
            opener=_SseResponse([_sse_chunk("```json\n{\"action\": \"final\","), _sse_chunk(" \"answer\": \"完成\"}\n```")]),
        )
        self.assertEqual(provider.chat_json("system", "user"), {"action": "final", "answer": "完成"})
        request = _SseResponse.last_request or {}
        self.assertTrue(request.get("stream"))

    def test_chat_json_forces_thinking_off_for_decisions(self) -> None:
        """模型档位开启思考时，决策调用强制 thinking disabled（高频廉价操作）。"""
        provider = OpenAICompatibleProvider(
            "https://example.test/v1", "key", "model",
            extra_params={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
            opener=_SseResponse([_sse_chunk("{\"action\": \"final\", \"answer\": \"好\"}")]),
        )
        self.assertEqual(provider.chat_json("system", "user"), {"action": "final", "answer": "好"})
        request = _SseResponse.last_request or {}
        self.assertEqual(request.get("thinking"), {"type": "disabled"})
        self.assertNotIn("reasoning_effort", request)

    def test_chat_json_empty_content_reports_finish_reason(self) -> None:
        """空内容不能再报 "Expecting value: char 0"，要带 finish_reason 指向风控/瞬时故障。"""
        provider = OpenAICompatibleProvider(
            "https://example.test/v1", "key", "model",
            opener=_SseResponse([_sse_chunk(None, finish_reason="content_filter")]),
        )
        with self.assertRaises(LLMError) as ctx:
            provider.chat_json("system", "user")
        self.assertIn("finish_reason=content_filter", str(ctx.exception))

    def test_chat_json_surfaces_transient_disconnect_as_retryable(self) -> None:
        """服务端掐线不回话（RemoteDisconnected）按瞬态可重试透出。"""
        provider = OpenAICompatibleProvider(
            "https://example.test/v1", "key", "model",
            opener=lambda *_a, **_k: (_ for _ in ()).throw(RemoteDisconnected("remote end closed")),
        )
        with self.assertRaises(LLMError) as ctx:
            provider.chat_json("system", "user")
        self.assertTrue(ctx.exception.retryable)
        self.assertIn("remote end closed", str(ctx.exception))

    def test_chat_json_marks_content_block_as_non_retryable(self) -> None:
        """400 内容风控重试无意义：retryable=False 让 agent 循环立即失败。"""
        provider = OpenAICompatibleProvider(
            "https://example.test/v1", "key", "model",
            opener=_HttpErrorResponse(400, {"error": {"message": "Content Exists Risk"}}),
        )
        with self.assertRaises(LLMError) as ctx:
            provider.chat_json("system", "user")
        self.assertFalse(ctx.exception.retryable)

    def test_openai_compatible_provider_answers_follow_up(self) -> None:
        payload = {"choices": [{"message": {"content": "报告显示净利润率约为 20%。"}}]}
        provider = OpenAICompatibleProvider("https://example.test/v1", "key", "model", opener=lambda *_args, **_kwargs: _Response(payload))
        self.assertEqual(provider.answer("利润率怎么看？", "净利润率：20%"), "报告显示净利润率约为 20%。")


class RetryPolicyTests(unittest.TestCase):
    """供应商错误分类：429/5xx/网络瞬断可重试，其余 4xx 确定性失败。"""

    def test_429_is_retryable_and_honors_retry_after_header(self) -> None:
        exc = urllib.error.HTTPError("https://x", 429, "Too Many Requests", {"Retry-After": "7"}, None)
        self.assertEqual(retry_policy(exc), (True, 7.0))

    def test_429_without_retry_after_still_retryable(self) -> None:
        exc = urllib.error.HTTPError("https://x", 429, "Too Many Requests", {}, None)
        self.assertEqual(retry_policy(exc), (True, None))

    def test_5xx_is_retryable(self) -> None:
        exc = urllib.error.HTTPError("https://x", 503, "Service Unavailable", {}, None)
        self.assertEqual(retry_policy(exc), (True, None))

    def test_other_4xx_is_deterministic(self) -> None:
        exc = urllib.error.HTTPError("https://x", 401, "Unauthorized", {}, None)
        self.assertEqual(retry_policy(exc), (False, None))

    def test_connection_and_timeout_errors_are_transient(self) -> None:
        self.assertEqual(retry_policy(ConnectionResetError()), (True, None))
        self.assertEqual(retry_policy(RemoteDisconnected("closed")), (True, None))
        self.assertEqual(retry_policy(TimeoutError()), (True, None))
