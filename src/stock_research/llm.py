"""Model-provider seam for qualitative research analysis.

The core pipeline can run without a model.  Providers return structured
claims, so a remote model is never allowed to write directly into a report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import os
import time
from typing import Any, Callable, Iterable, Protocol
from urllib.request import Request, urlopen


class LLMError(RuntimeError):
    """Raised when a provider cannot return a valid structured response."""


class ModelNotConfiguredError(LLMError):
    """Raised when a production request has no configured real model."""


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    question: str
    as_of_date: date
    evidence: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class AnalysisClaim:
    category: str
    text: str
    evidence_ids: tuple[str, ...]
    confidence: float
    counter_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalysisResponse:
    claims: tuple[AnalysisClaim, ...]
    provider: str
    model: str
    usage: dict[str, Any] | None = None


class LLMProvider(Protocol):
    def analyze(self, request: AnalysisRequest) -> AnalysisResponse: ...


class HeuristicLLMProvider:
    """Deterministic local provider used for tests and keyless development."""

    provider_name = "heuristic"
    model_name = "rules-v1"

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        claims: list[AnalysisClaim] = []
        for item in request.evidence:
            text = item.get("text", "").strip()
            evidence_id = item.get("evidence_id", "")
            lowered = text.casefold()
            if any(keyword in lowered for keyword in ("business", "业务", "cloud", "云业务", "growth", "增长")):
                claims.append(AnalysisClaim("business", text, (evidence_id,), 0.68))
            if any(keyword in lowered for keyword in ("risk", "风险", "competition", "竞争", "regulatory", "监管")):
                claims.append(AnalysisClaim("risk", text, (evidence_id,), 0.68))
        return AnalysisResponse(tuple(claims), self.provider_name, self.model_name)


class OpenAICompatibleProvider:
    """Call providers exposing the OpenAI chat-completions wire shape.

    This works with compatible deployments such as DeepSeek, OpenAI and
    other gateways.  The provider requires an explicit API key and never
    silently falls back to an unauthenticated request.
    """

    provider_name = "openai_compatible"
    prompt_version = "analyze-v1"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        opener: Callable[..., Any] = urlopen,
        timeout: float = 60.0,
        extra_params: dict[str, Any] | None = None,
    ) -> None:
        if not base_url.strip() or not api_key.strip() or not model.strip():
            raise ValueError("base_url, api_key and model are required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.extra_params = dict(extra_params or {})
        self._opener = opener
        self.timeout = timeout

    def _payload(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> dict[str, Any]:
        """Base request body; extra_params (强度档位映射) merge in last."""
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "messages": messages,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        payload.update(self.extra_params)
        return payload

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        evidence_text = "\n".join(
            f"[{item.get('evidence_id')}]{(' ' + item['trust']) if item.get('trust') else ''} {item.get('text', '')}"
            for item in request.evidence
        )
        instruction = (
            "You are a financial research analyst. Return JSON only in the shape "
            '{"claims":[{"category":"business|risk|industry","text":"...",'
            '"evidence_ids":["..."],"confidence":0.0,"counter_evidence_ids":[]}]}.'
            " Do not invent facts. Every claim must cite one or more supplied evidence IDs. "
            "Evidence items carry a source-trust tag: [私有已验证] = user-verified private material, "
            "[白名单来源] = whitelisted public source, [未验证来源] = unverified web source. "
            "Prefer higher-trust evidence; say so explicitly when a claim relies on [未验证来源]; "
            "when evidence conflicts, trust private material over public web sources. "
            f"Research cutoff: {request.as_of_date.isoformat()}. Question: {request.question}\n"
            f"Evidence:\n{evidence_text}"
        )
        payload = self._payload(
            [
                {"role": "system", "content": "Produce conservative, evidence-grounded financial analysis."},
                {"role": "user", "content": instruction},
            ],
            json_mode=True,
        )
        started = time.monotonic()
        req = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(req, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        try:
            content = body["choices"][0]["message"]["content"]
            decoded = json.loads(content) if isinstance(content, str) else content
            claims = tuple(self._parse_claim(item, request.evidence) for item in decoded["claims"])
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMError(f"LLM returned invalid analysis JSON: {exc}") from exc
        usage_raw = body.get("usage") if isinstance(body, dict) else None
        usage = {
            "prompt_version": self.prompt_version,
            "provider": self.provider_name,
            "model": self.model,
            "prompt_tokens": int(usage_raw.get("prompt_tokens", 0)) if isinstance(usage_raw, dict) else 0,
            "completion_tokens": int(usage_raw.get("completion_tokens", 0)) if isinstance(usage_raw, dict) else 0,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
        return AnalysisResponse(claims, self.provider_name, self.model, usage)

    def answer(self, question: str, context: str = "") -> str:
        """Answer a follow-up question using previously verified report context."""
        prompt = (
            "你是严谨的股票研究助手。请基于给定的历史研究报告回答用户追问。"
            "只使用报告中已有的信息；如果资料不足，明确说资料不足，不要编造数字。"
            "用简洁中文回答，并在涉及数字时保留原有单位。\n\n"
            f"历史研究报告：\n{context[:24000]}\n\n用户追问：{question}"
        )
        payload = self._payload(
            [
                {"role": "system", "content": "回答必须以提供的研究报告为依据。"},
                {"role": "user", "content": prompt},
            ],
        )
        req = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(req, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty answer")
            return content.strip()
        except Exception as exc:
            raise LLMError(f"LLM follow-up failed: {exc}") from exc

    def answer_with_search(self, question: str, search_results: list[dict[str, str]], report_context: str = "") -> str:
        """Answer a follow-up using web search snippets plus report context."""
        sources = "\n".join(
            f"[{index}]{(' ' + item['trust']) if item.get('trust') else ''} {item.get('title', '')}\n{item.get('url', '')}\n{item.get('snippet', '')}"
            for index, item in enumerate(search_results, 1)
        )
        prompt = (
            "你是严谨的股票研究助手。请结合联网搜索结果回答用户追问。"
            "只使用提供的搜索摘要和历史报告信息，不要编造数字；信息不足时明确说明。"
            "涉及数字时保留原有单位。"
            "搜索结果带有可信度标注：[白名单来源] 表示来自可信白名单域名，[未验证来源] 表示未经核实的网络来源。"
            "优先引用可信来源；引用未验证来源时必须注明「据未经核实的网络来源」。"
            "回答末尾用 Markdown 列出参考来源链接（[标题](URL)），并为每条来源标注可信度。\n\n"
            f"历史研究报告：\n{report_context[:12000]}\n\n"
            f"联网搜索结果：\n{sources}\n\n"
            f"用户追问：{question}"
        )
        payload = self._payload(
            [
                {"role": "system", "content": "回答必须以提供的搜索摘要和研究报告为依据，并附来源链接。"},
                {"role": "user", "content": prompt},
            ],
        )
        req = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(req, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty answer")
            return content.strip()
        except Exception as exc:
            raise LLMError(f"LLM search follow-up failed: {exc}") from exc

    @staticmethod
    def _parse_claim(raw: dict[str, Any], evidence: Iterable[dict[str, str]]) -> AnalysisClaim:
        if not isinstance(raw, dict):
            raise ValueError("claim must be an object")
        known_ids = {item.get("evidence_id") for item in evidence}
        evidence_ids = tuple(str(value) for value in raw.get("evidence_ids", []))
        if not evidence_ids or any(value not in known_ids for value in evidence_ids):
            raise ValueError("claim must cite supplied evidence IDs")
        confidence = float(raw.get("confidence", 0))
        if not 0 <= confidence <= 1:
            raise ValueError("claim confidence must be between 0 and 1")
        counter_ids = tuple(str(value) for value in raw.get("counter_evidence_ids", []))
        if any(value not in known_ids for value in counter_ids):
            raise ValueError("counter evidence ID is unknown")
        return AnalysisClaim(str(raw.get("category", "inference")), str(raw["text"]), evidence_ids, confidence, counter_ids)


def list_remote_models(base_url: str, api_key: str, timeout: int = 10) -> list[str]:
    """Fetch the model catalog from an OpenAI-compatible ``GET /models`` endpoint.

    Mirrors deepseek-harness's discovery: normalize remote entries to model ids,
    sorted; supports both ``{"data": [...]}`` and ``{"models": [...]}`` shapes.
    """
    url = f"{base_url.rstrip('/')}/models"
    req = Request(url, headers={"Authorization": f"Bearer {api_key}"}, method="GET")
    try:
        with urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise LLMError(f"拉取模型列表失败: {exc}") from exc
    entries: Any = None
    if isinstance(body, dict):
        entries = body.get("data")
        if not isinstance(entries, list):
            entries = body.get("models")
    if not isinstance(entries, list):
        raise LLMError("模型列表响应格式无法识别")
    ids = {str(entry.get("id") or entry.get("name") or "").strip() for entry in entries if isinstance(entry, dict)}
    return sorted(item for item in ids if item)


class DeepSeekProvider(OpenAICompatibleProvider):
    """Convenience configuration for the DeepSeek API."""

    provider_name = "deepseek"

    def __init__(self, api_key: str, model: str = "deepseek-chat", **kwargs: Any) -> None:
        super().__init__("https://api.deepseek.com/v1", api_key, model, **kwargs)


def provider_from_env() -> LLMProvider | None:
    """Build a provider only when an explicit API key is configured.

    `DEEPSEEK_API_KEY` is the convenient default.  Generic variables allow a
    compatible gateway (including another vendor) without code changes.
    """

    key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("AI_STOCK_LLM_API_KEY")
    if not key:
        return None
    base_url = os.getenv("AI_STOCK_LLM_BASE_URL")
    model = os.getenv("AI_STOCK_LLM_MODEL", "deepseek-chat")
    if base_url:
        return OpenAICompatibleProvider(base_url, key, model)
    return DeepSeekProvider(key, model)


def provider_from_config(config: dict[str, Any] | None) -> LLMProvider | None:
    """Build a provider from request/UI configuration.

    The web app uses this seam so a local user can configure a real model from
    the settings panel without editing source code. Missing values continue to
    use the process environment, while an explicitly empty API key disables
    remote calls for that request.
    """
    if not config:
        return provider_from_env()
    key = str(config.get("api_key") or config.get("apiKey") or "").strip()
    if not key:
        return None
    base_url = str(config.get("base_url") or config.get("baseUrl") or "").strip()
    model = str(config.get("model") or "deepseek-chat").strip()
    provider = str(config.get("provider") or "openai_compatible").casefold()
    extra = config.get("extra_params")
    extra_params = dict(extra) if isinstance(extra, dict) else None
    if provider == "deepseek" and (not base_url or base_url == "https://api.deepseek.com/v1"):
        return DeepSeekProvider(key, model)
    return OpenAICompatibleProvider(base_url or "https://api.deepseek.com/v1", key, model, extra_params=extra_params)
