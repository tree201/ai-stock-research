"""Dependency-free local web MVP for the stock research pipeline."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import mimetypes
import os
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .documents import HttpDocumentFetcher, PdfTextExtractor, RawDocument, extract_text
from .domain import ResearchProject, ResearchSession, SessionMessage, utc_now
from .pipeline import ResearchPipeline
from .llm import LLMError, ModelNotConfiguredError, provider_from_config, provider_from_env
from .storage import SQLiteStore
from .workflow import ResearchWorkflow
from .jobs import create_and_enqueue, queue_from_env
from .hk_companies import list_hk_companies
from .web_search import DuckDuckGoSearch, GoogleNewsSearch
from .market_data import MarketDataError, YahooFinanceProvider


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Stock Research</title>
  <style>
    :root { color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #f5f7fb; color: #172033; }
    main { max-width: 1100px; margin: 0 auto; padding: 32px 20px 64px; }
    h1 { margin-bottom: 4px; } .subtle { color: #667085; }
    .workspace { display: grid; grid-template-columns: 260px 1fr; gap: 20px; align-items: start; }
    .card { background: white; border: 1px solid #e4e7ec; border-radius: 12px; padding: 18px; box-shadow: 0 3px 12px #1018280a; }
    label { display: block; margin: 12px 0 6px; font-size: 13px; font-weight: 600; }
    input, textarea { width: 100%; box-sizing: border-box; border: 1px solid #d0d5dd; border-radius: 8px; padding: 9px 10px; font: inherit; }
    textarea { min-height: 150px; resize: vertical; }
    button { margin-top: 16px; border: 0; border-radius: 8px; padding: 10px 14px; background: #2457d6; color: white; font-weight: 600; cursor: pointer; }
    button:disabled { opacity: .6; cursor: wait; }
    #status { margin: 14px 0; color: #475467; min-height: 22px; }
    pre { white-space: pre-wrap; word-break: break-word; background: #101828; color: #e6edf5; border-radius: 8px; padding: 14px; overflow: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; } th, td { text-align: left; border-bottom: 1px solid #eaecf0; padding: 8px 5px; }
    .pill { display: inline-block; padding: 3px 8px; background: #eef4ff; color: #2457d6; border-radius: 999px; font-size: 12px; margin: 3px 4px 3px 0; }
    .side-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; } .side-head button { margin:0; padding:5px 8px; }
    .company { display:block; width:100%; text-align:left; background:#f8faff; color:#172033; margin:7px 0 0; padding:10px; border:1px solid #e4e7ec; } .company span { display:block; color:#667085; font-size:12px; margin-top:4px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .chat-shell { min-height:620px; display:flex; flex-direction:column; } .messages { flex:1; min-height:400px; max-height:600px; overflow:auto; padding:14px 0; } .welcome { color:#475467; padding:30px 12px; text-align:center; }
    .message { display:flex; margin:10px 0; } .message.user { justify-content:flex-end; } .bubble { max-width:82%; border-radius:12px; padding:10px 12px; background:#f2f4f7; } .message.user .bubble { background:#2457d6; color:#fff; }
    .composer { display:flex; gap:8px; border-top:1px solid #eaecf0; padding-top:14px; } .composer input { flex:1; } .composer button { margin:0; }
    .settings { margin-top:12px; color:#667085; font-size:13px; } .settings-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:8px; } .settings label { margin:0; } .settings .wide { grid-column:1 / -1; } .settings textarea { min-height:70px; }
    .report { margin-top:10px; color:inherit; } .report pre { max-height:320px; }
    @media (max-width: 800px) { .workspace { grid-template-columns: 1fr; } .sidebar { order:2; } .settings-grid { grid-template-columns:1fr; } .settings .wide { grid-column:auto; } }
  </style>
</head>
<body>
<main>
  <header><h1>AI Stock Research</h1><p class="subtle">你的长期股票研究助手</p></header>
  <div class="workspace">
    <aside class="card sidebar"><div class="side-head"><strong>最近研究</strong><button id="refresh" type="button">刷新</button></div><div id="companies" class="subtle">正在加载……</div></aside>
    <section class="card chat-shell">
      <div id="chat-title"><strong>选择一家公司开始研究</strong></div>
      <div id="messages" class="messages"><div class="welcome"><h2>从一个问题开始</h2><p>例如：“研究腾讯是否适合长期持有，重点看现金流和估值。”</p><p class="subtle">研究结果、进度、证据和历史报告都会留在这个会话里。</p></div></div>
      <form id="chat-form" class="composer"><input id="message" autocomplete="off" placeholder="输入研究问题或追问……" required /><button id="send" type="submit">发送</button></form>
      <details class="settings"><summary>研究设置（可选）</summary><div class="settings-grid"><label>公司名称<input id="name" value="腾讯（示例）" /></label><label>港股代码<input id="symbol" value="00700" /></label><label>截止日期<input id="as-of-date" type="date" value="2025-12-31" /></label><label class="wide">资料文本<textarea id="document" placeholder="粘贴财报或公告文本；留空使用示例资料。"></textarea></label></div></details>
      <div id="status" class="subtle">正在检查模型配置……</div>
    </section>
  </div>
</main>
<script>
const form = document.getElementById('chat-form');
const button = document.getElementById('send');
const status = document.getElementById('status');
const messages = document.getElementById('messages');
const companies = document.getElementById('companies');
const title = document.getElementById('chat-title');
let currentSessionId = null;
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function addMessage(role, text, extra='') { messages.insertAdjacentHTML('beforeend', `<div class="message ${role}"><div class="bubble">${escape(text).replace(/\n/g,'<br>')}${extra}</div></div>`); messages.scrollTop = messages.scrollHeight; }
function addReport(report) { addMessage('assistant', report.summary.join('\n'), `<details class="report"><summary>打开研究报告</summary><pre>${escape(report.markdown)}</pre></details>`); }
function clearMessages() { messages.innerHTML = ''; }
async function openSession(sessionId, companyName) {
  currentSessionId = sessionId; title.innerHTML = `<strong>${escape(companyName || '研究会话')}</strong>`; clearMessages();
  const data = await (await fetch('/api/sessions/' + sessionId)).json();
  (data.messages || []).forEach(item => addMessage(item.role === 'user' ? 'user' : 'assistant', item.content?.text || item.content?.title || ''));
  if (!data.messages?.length) addMessage('assistant', '这是一个新的研究会话。告诉我你想研究什么。');
}
async function loadCompanies() {
  const rows = await (await fetch('/api/companies')).json();
  if (!rows.length) { companies.textContent = '暂无公司，直接在下方输入问题即可开始。'; return; }
  companies.innerHTML = rows.map(row => `<button type="button" class="company" data-project="${escape(row.id)}"><strong>${escape(row.name)}</strong><span>${escape(row.symbol)} · ${escape(row.latest_event_preview || '暂无活动')}</span></button>`).join('');
  companies.querySelectorAll('[data-project]').forEach(btn => btn.addEventListener('click', async () => {
    const sessions = await (await fetch('/api/projects/' + btn.dataset.project + '/sessions')).json();
    if (sessions.length) await openSession(sessions[0].id, btn.querySelector('strong').textContent);
  }));
}
form.addEventListener('submit', async (event) => {
  event.preventDefault(); button.disabled = true; status.textContent = '正在处理……';
  const text = document.getElementById('message').value.trim(); if (!text) return;
  document.getElementById('message').value = ''; addMessage('user', text);
  try {
    const payload = { content: text, as_of_date: document.getElementById('as-of-date').value };
    let data;
    if (currentSessionId) {
      const response = await fetch('/api/sessions/' + currentSessionId + '/messages', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
      data = await response.json(); if (!response.ok) throw new Error(data.error || '处理失败');
      if (data.report) addReport(data.report); else if (data.message) addMessage('assistant', data.message);
    } else {
      const response = await fetch('/api/chat', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:document.getElementById('name').value, symbol:document.getElementById('symbol').value, as_of_date:document.getElementById('as-of-date').value, content:text}) });
      data = await response.json(); if (!response.ok) throw new Error(data.error || '处理失败'); currentSessionId = data.session_id; title.innerHTML = `<strong>${escape(document.getElementById('name').value)}</strong>`; if (data.report) addReport(data.report); else if (data.message) addMessage('assistant', data.message);
    }
    status.textContent = '已完成'; await loadCompanies();
  } catch (error) { status.textContent = error.message; addMessage('assistant', '处理失败：' + error.message); }
  finally { button.disabled = false; }
});
document.getElementById('refresh').addEventListener('click', loadCompanies); loadCompanies();
fetch('/api/status').then(response => response.json()).then(data => {
  status.textContent = data.llm_enabled ? `模型：${data.model}（${data.provider}）` : '未配置真实模型，请先在设置中接入模型';
}).catch(() => { status.textContent = '模型状态暂时不可用'; });
</script>
</body>
</html>"""


def _frontend_dist() -> Path:
    return Path(__file__).parents[2] / "frontend" / "dist"


def _session_title(text: str, default: str = "长期研究") -> str:
    """Keep greetings from becoming noisy conversation titles."""
    value = " ".join(text.strip().split())
    if not value:
        return default
    if value.casefold() in {"你好", "您好", "嗨", "hello", "hi", "hey", "在吗", "测试"}:
        return default
    return value[:60]


def _web_provider(llm_config: dict[str, Any] | None = None):
    provider = provider_from_config(llm_config)
    if provider is None:
        raise ModelNotConfiguredError("尚未配置真实模型，请打开‘设置 → 模型接入’完成配置。")
    return provider


def provider_status() -> dict[str, Any]:
    provider = provider_from_env()
    if provider is None:
        return {"llm_enabled": False, "provider": "", "model": "", "base_url": "", "api_key_configured": False}
    return {"llm_enabled": True, "provider": getattr(provider, "provider_name", "configured"), "model": getattr(provider, "model", getattr(provider, "model_name", "unknown")), "base_url": getattr(provider, "base_url", ""), "api_key_configured": True}


def configure_provider(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply local web model settings to this process and return safe status.

    The key is intentionally never returned. This endpoint is intended for a
    local development server; production deployments should use environment
    secrets instead.
    """
    config = payload.get("llm") if isinstance(payload.get("llm"), dict) else payload
    key_field_present = "api_key" in config or "apiKey" in config
    key = str(config.get("api_key") or config.get("apiKey") or "").strip()
    base_url = str(config.get("base_url") or config.get("baseUrl") or "").strip()
    model = str(config.get("model") or "deepseek-chat").strip()
    provider = str(config.get("provider") or "deepseek").strip().casefold()
    existing_key = os.getenv("AI_STOCK_LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    effective_key = key or existing_key
    if key or (not key_field_present and existing_key):
        os.environ["AI_STOCK_LLM_API_KEY"] = effective_key
        os.environ["AI_STOCK_LLM_MODEL"] = model
        if base_url and not (provider == "deepseek" and base_url == "https://api.deepseek.com/v1"):
            os.environ["AI_STOCK_LLM_BASE_URL"] = base_url
        elif provider == "deepseek":
            os.environ.pop("AI_STOCK_LLM_BASE_URL", None)
        if provider == "deepseek":
            os.environ["DEEPSEEK_API_KEY"] = effective_key
    elif key_field_present:
        os.environ.pop("AI_STOCK_LLM_API_KEY", None)
        os.environ.pop("DEEPSEEK_API_KEY", None)
        os.environ.pop("AI_STOCK_LLM_BASE_URL", None)
    return provider_status()


def database_path() -> str:
    return os.environ.get("AI_STOCK_DB", "./research.sqlite3")


def _document_hosts() -> tuple[str, ...]:
    configured = os.getenv("AI_STOCK_DOCUMENT_HOSTS", "www1.hkexnews.hk,www.hkexnews.hk,hkexnews.hk")
    hosts = tuple(value.strip().lower() for value in configured.split(",") if value.strip())
    if not hosts:
        raise ValueError("AI_STOCK_DOCUMENT_HOSTS must contain at least one host")
    return hosts


def _source_specs(payload: dict[str, Any]) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    raw_sources = payload.get("document_sources") or []
    if not isinstance(raw_sources, list):
        raise ValueError("document_sources must be a list")
    for item in raw_sources:
        if not isinstance(item, dict) or not str(item.get("url", "")).strip():
            raise ValueError("each document source must contain a URL")
        specs.append({key: str(value) for key, value in item.items() if value is not None})
    raw_urls = payload.get("document_urls") or payload.get("document_url") or []
    if isinstance(raw_urls, str):
        raw_urls = [value.strip() for value in raw_urls.replace(",", "\n").splitlines() if value.strip()]
    if not isinstance(raw_urls, list):
        raise ValueError("document_urls must be a list or newline-separated string")
    specs.extend({"url": str(value).strip()} for value in raw_urls if str(value).strip())
    return specs


def _parse_published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _research_documents(payload: dict[str, Any], company_id: UUID, as_of_date: date) -> list[RawDocument]:
    documents: list[RawDocument] = []
    if payload.get("document"):
        documents.append(RawDocument(
            company_id=company_id,
            source_type="user_text",
            source_url="user://document",
            title="user document",
            content=str(payload["document"]),
            published_at=datetime(as_of_date.year, as_of_date.month, as_of_date.day, tzinfo=timezone.utc),
        ))
    specs = _source_specs(payload)
    if specs:
        fetcher = HttpDocumentFetcher(allowed_hosts=_document_hosts())
        pdf_extractor = PdfTextExtractor()
        for spec in specs:
            url = spec["url"].strip()
            fetched = fetcher.fetch(url)
            page_starts: tuple[int, ...] = ()
            if fetched.content_type == "application/pdf" or fetched.body.startswith(b"%PDF"):
                pages = pdf_extractor.extract_pages(fetched.body)
                content_parts: list[str] = []
                starts: list[int] = []
                next_line = 1
                for page in pages:
                    starts.append(next_line)
                    content_parts.append(page.text)
                    next_line += len(page.text.splitlines())
                content = "\n".join(content_parts)
                page_starts = tuple(starts)
            else:
                content = extract_text(fetched)
            if not content.strip():
                raise ValueError(f"document contains no extractable text: {url}")
            published_at = _parse_published_at(spec.get("published_at") or fetched.last_modified)
            host = (urlparse(url).hostname or "").lower()
            documents.append(RawDocument(
                company_id=company_id,
                source_type="hkex_filing" if "hkexnews.hk" in host else "company_ir",
                source_url=url,
                title=spec.get("title") or Path(urlparse(url).path).name or "public filing",
                content=content,
                published_at=published_at,
                language=spec.get("language"),
                page_starts=page_starts,
            ))
    if not documents:
        raise ValueError("未提供研究资料：请粘贴财报/公告文本，或在研究设置中提供公开的 HKEX/公司 IR 资料 URL。")
    return documents


def _ensure_session(store: SQLiteStore, project: ResearchProject, session_id: UUID | None = None, title: str = "长期研究") -> ResearchSession:
    if session_id:
        session = store.load_session(session_id)
        if session.project_id != project.id:
            raise ValueError("session does not belong to project")
        return session
    return store.find_active_session(project.id) or ResearchSession(project_id=project.id, title=title)


def run_research_payload(payload: dict[str, Any], db_path: str | Path | None = None, session_id: UUID | None = None) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    symbol = str(payload.get("symbol", "")).strip()
    question = str(payload.get("question", "")).strip()
    as_of_date = date.fromisoformat(str(payload.get("as_of_date", "")))
    if not name or not symbol or not question:
        raise ValueError("name, symbol and question are required")
    store = SQLiteStore(db_path or database_path())
    try:
        project = store.find_project(symbol) or ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol=symbol, name=name)
        project.name = name
        project.updated_at = utc_now()
        pipeline = ResearchPipeline(workflow=ResearchWorkflow(store=store), llm_provider=_web_provider(payload.get("llm")))
        pipeline.workflow.create_project(project)
        session = _ensure_session(store, project, session_id, title=_session_title(question))
        pipeline.workflow.create_session(session)
        if not payload.get("_session_message_saved"):
            store.save_session_message(SessionMessage(session.id, "user", "text", {"text": question}))
        requested_urls = payload.get("document_urls") or payload.get("document_url") or []
        if isinstance(requested_urls, str):
            requested_urls = [value.strip() for value in requested_urls.replace(",", "\n").splitlines() if value.strip()]
        registered_urls = store.list_company_source_urls(project.company_id)
        seen: set[str] = set()
        merged_urls: list[str] = []
        for url in [*requested_urls, *registered_urls]:
            value = str(url).strip()
            if value and value not in seen:
                seen.add(value)
                merged_urls.append(value)
        payload = {**payload, "document_urls": merged_urls}
        documents = _research_documents(payload, project.company_id, as_of_date)
        report = pipeline.run(
            project_id=project.id,
            session_id=session.id,
            question=question,
            as_of_date=as_of_date,
            documents=documents,
            dcf_assumptions={
                "revenue_prior": 609_000_000_000,
                "revenue_years": 1,
                "growth_rates": [0.15, 0.12, 0.10, 0.08, 0.06],
                "discount_rate": 0.09,
                "terminal_growth": 0.03,
                "shares": 9_000_000_000,
            },
        )
        report["session_id"] = str(session.id)
        store.save_session_message(SessionMessage(session.id, "assistant", "report_card", {"text": "研究已完成", "report_id": report["report_id"], "summary": report.get("summary", [])}, run_id=UUID(report["run_id"]), report_id=UUID(report["report_id"])))
        session.active_run_id = None
        session.updated_at = utc_now()
        store.save_session(session)
        return report
    finally:
        store.close()


_SEARCH_INTENT_WORDS = ("搜", "联网", "网络", "最新", "最近", "新闻", "资讯", "消息", "公告", "股价", "行情", "价格")


def _answer_follow_up(provider: Any, content: str, report_context: str, project: ResearchProject) -> str:
    """Answer a follow-up, searching the web when the question asks for it."""
    lowered = content.casefold()
    wants_search = any(word in lowered for word in _SEARCH_INTENT_WORDS)
    if wants_search and hasattr(provider, "answer_with_search"):
        results = DuckDuckGoSearch().search(f"{project.name} {project.symbol} {content}", max_results=5)
        if not results:
            # Google News RSS matches the user's own phrasing far better than
            # the stored English company name, so try it first.
            for candidate in (content, project.name):
                results = GoogleNewsSearch().search(candidate, max_results=5)
                if results:
                    break
        if results:
            return provider.answer_with_search(
                content,
                [{"title": item.title, "url": item.url, "snippet": item.snippet} for item in results],
                report_context,
            )
    return provider.answer(content, report_context)


def chat_payload(session_id: UUID, content: str, db_path: str | Path | None = None, as_of_date: date | None = None, llm_config: dict[str, Any] | None = None, document_urls: Any = None) -> dict[str, Any]:
    """Handle one chat message without exposing workflow internals to the UI."""
    content = content.strip()
    if not content:
        raise ValueError("content is required")
    store = SQLiteStore(db_path or database_path())
    try:
        session = store.load_session(session_id)
        project = store.load_project(session.project_id)
        _web_provider(llm_config)
        lowered = content.casefold()
        research_intent = any(word in lowered for word in ("研究", "分析", "估值", "长期持有", "更新研究"))
        if research_intent:
            if as_of_date is None:
                as_of_date = date.today()
            research_payload = {"name": project.name, "symbol": project.symbol, "as_of_date": as_of_date.isoformat(), "question": content, "_session_message_saved": True, "llm": llm_config, "document_urls": document_urls}
            store.save_session_message(SessionMessage(session_id, "user", "text", {"text": content}))
            if os.getenv("AI_STOCK_QUEUE", "inline").casefold() == "rq":
                job = create_and_enqueue(store, session_id, research_payload, db_path or database_path())
                return {"type": "research_queued", "session_id": str(session_id), **job}
            report = run_research_payload(research_payload, db_path=db_path or database_path(), session_id=session_id)
            return {"type": "research_started", "session_id": str(session_id), "run_id": report["run_id"], "report_id": report["report_id"], "report": report}
        store.save_session_message(SessionMessage(session_id, "user", "text", {"text": content}))
        answer = "我会基于当前公司的研究资料回答。你可以问我具体指标，或说‘研究这家公司’启动一次完整研究。"
        provider = _web_provider(llm_config)
        if "模型" in content and hasattr(provider, "model"):
            provider_label = "DeepSeek" if getattr(provider, "provider_name", "") == "deepseek" else "OpenAI 兼容模型"
            answer = f"当前使用的是 {provider_label} 的 {getattr(provider, 'model', 'unknown')}。"
        elif hasattr(provider, "answer"):
            report_context = ""
            for message in reversed(store.list_session_messages(session_id)):
                report_id = message.get("content", {}).get("report_id")
                if report_id:
                    try:
                        report_context = str(store.load_report(UUID(report_id)).get("markdown", ""))
                    except (ValueError, KeyError):
                        report_context = ""
                    if report_context:
                        break
            try:
                answer = _answer_follow_up(provider, content, report_context, project)
            except LLMError:
                raise
        store.save_session_message(SessionMessage(session_id, "assistant", "text", {"text": answer}))
        return {"type": "answer", "session_id": str(session_id), "message": answer}
    finally:
        store.close()


def chat_entry_payload(payload: dict[str, Any], db_path: str | Path | None = None) -> dict[str, Any]:
    """Create/reuse a company session for the first chat message."""
    name = str(payload.get("name", "")).strip()
    symbol = str(payload.get("symbol", "")).strip()
    content = str(payload.get("content", "")).strip()
    if not name or not symbol or not content:
        raise ValueError("name, symbol and content are required")
    _web_provider(payload.get("llm"))
    store = SQLiteStore(db_path or database_path())
    try:
        project = store.find_project(symbol) or ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol=symbol, name=name)
        project.name = name; project.updated_at = utc_now(); store.save_project(project)
        session = _ensure_session(store, project, title=_session_title(content))
        store.save_session(session)
        session_id = session.id
    finally:
        store.close()
    return chat_payload(session_id, content, db_path=db_path, as_of_date=date.fromisoformat(payload["as_of_date"]) if payload.get("as_of_date") else None, llm_config=payload.get("llm"))


def _yahoo_symbol(symbol: str, market: str) -> str:
    digits = "".join(ch for ch in symbol if ch.isdigit())
    if market.upper() == "HK" and digits:
        return f"{int(digits):04d}.HK"
    return symbol


def _quote_payload(symbol: str, market: str) -> dict[str, Any]:
    """Best-effort delayed price snapshot; never raises for provider issues."""
    yahoo = _yahoo_symbol(symbol, market)
    today = date.today()
    try:
        bars = YahooFinanceProvider().get_daily_bars(yahoo, today - timedelta(days=380), today)
    except (MarketDataError, ValueError):
        return {"available": False, "symbol": yahoo, "delayed": True}
    if not bars:
        return {"available": False, "symbol": yahoo, "delayed": True}
    last = bars[-1]
    prev = bars[-2] if len(bars) >= 2 else None
    change = round(last.close - prev.close, 4) if prev else None
    change_pct = round((last.close - prev.close) / prev.close, 6) if prev and prev.close else None
    return {
        "available": True,
        "symbol": yahoo,
        "delayed": True,
        "provider": last.provider,
        "currency": "HKD" if market.upper() == "HK" else "",
        "last": last.close,
        "change": change,
        "change_pct": change_pct,
        "high_52w": max(bar.high for bar in bars),
        "low_52w": min(bar.low for bar in bars),
        "as_of": last.trading_date.isoformat(),
    }


def _news_payload(name: str, symbol: str) -> list[dict[str, str]]:
    candidates = [f"{name} 最新", name, symbol]
    for candidate in candidates:
        if not candidate.strip():
            continue
        results = GoogleNewsSearch().search(candidate.strip(), max_results=8)
        if results:
            return [{"title": item.title, "url": item.url, "source": item.snippet} for item in results]
    return []


def _company_panel_payload(symbol: str, market: str) -> dict[str, Any]:
    store = SQLiteStore(database_path())
    try:
        project = store.find_project(symbol, market)
        if project is None:
            return {"available": False}
        return {
            "available": True,
            "project": {"id": str(project.id), "name": project.name, "symbol": project.symbol, "market": project.market},
            "sources": store.list_company_sources(project.company_id),
            "documents": store.list_company_documents(project.company_id),
            "reports": store.list_project_reports(project.id),
        }
    finally:
        store.close()


def _add_company_source(symbol: str, market: str, url: str, title: str | None = None) -> dict[str, Any]:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("资料 URL 必须是 http(s) 链接")
    store = SQLiteStore(database_path())
    try:
        project = store.find_project(symbol, market)
        if project is None:
            raise ValueError("company not found")
        source = store.add_company_source(project.company_id, url, (title or "").strip() or None)
        return {"available": True, "source": source, "sources": store.list_company_sources(project.company_id)}
    finally:
        store.close()


def _remove_company_source(source_id: str) -> None:
    store = SQLiteStore(database_path())
    try:
        store.remove_company_source(UUID(source_id))
    finally:
        store.close()


def history_payload(path: str) -> dict[str, Any] | list[dict[str, Any]]:
    parsed = urlparse(path)
    if parsed.path == "/api/company-panel":
        query = parse_qs(parsed.query)
        symbol = (query.get("symbol") or [""])[0].strip()
        market = (query.get("market") or ["HK"])[0].strip().upper() or "HK"
        if not symbol:
            raise ValueError("symbol is required")
        return _company_panel_payload(symbol, market)
    if parsed.path == "/api/news":
        query = parse_qs(parsed.query)
        name = (query.get("name") or [""])[0].strip()
        symbol = (query.get("symbol") or [""])[0].strip()
        if not name:
            raise ValueError("name is required")
        return _news_payload(name, symbol)
    if parsed.path.startswith("/api/quote/"):
        raw = parsed.path.removeprefix("/api/quote/")
        market = (parse_qs(parsed.query).get("market") or ["HK"])[0].strip().upper() or "HK"
        if not raw:
            raise ValueError("symbol is required")
        return _quote_payload(raw, market)
    store = SQLiteStore(database_path())
    try:
        if parsed.path == "/api/companies":
            return store.list_companies()
        if parsed.path == "/api/company-catalog":
            return list_hk_companies()
        if parsed.path == "/api/search":
            query = (parse_qs(parsed.query).get("q") or [""])[0].strip()
            if not query:
                return []
            pattern = f"%{query}%"
            rows = store.connection.execute(
                """SELECT s.*, p.id AS company_id, p.name AS company_name, p.symbol AS company_symbol, p.market AS company_market
                   FROM sessions s JOIN projects p ON p.id=s.project_id
                   WHERE s.title LIKE ? OR s.last_event_preview LIKE ?
                      OR EXISTS (SELECT 1 FROM session_messages m WHERE m.session_id=s.id AND m.content_json LIKE ?)
                   ORDER BY s.latest_event_at DESC, s.created_at DESC LIMIT 50""",
                (pattern, pattern, pattern),
            ).fetchall()
            return [{"session": {"id": row["id"], "project_id": row["project_id"], "title": row["title"], "status": row["status"], "latest_event_at": row["latest_event_at"], "last_event_preview": row["last_event_preview"]}, "company": {"id": row["company_id"], "name": row["company_name"], "symbol": row["company_symbol"], "market": row["company_market"]}} for row in rows]
        if parsed.path == "/api/projects":
            return store.list_projects()
        if parsed.path.startswith("/api/projects/") and parsed.path.endswith("/sessions"):
            project_id = UUID(parsed.path.split("/")[3])
            return store.list_sessions(project_id)
        if parsed.path == "/api/runs":
            values = parse_qs(parsed.query).get("project_id")
            return store.list_runs(UUID(values[0]) if values else None)
        if parsed.path.startswith("/api/jobs/"):
            return store.load_job(UUID(parsed.path.removeprefix("/api/jobs/")))
        if parsed.path.startswith("/api/runs/"):
            run_id = UUID(parsed.path.removeprefix("/api/runs/"))
            run = store.load_run(run_id)
            return {
                "run": {
                    "id": str(run.id), "project_id": str(run.project_id), "question": run.question,
                    "as_of_date": run.as_of_date.isoformat(), "run_type": run.run_type,
                    "status": run.status.value, "plan_version": run.plan_version,
                    "created_at": run.created_at.isoformat(),
                    "started_at": run.started_at.isoformat() if run.started_at else None,
                    "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                    "steps": [
                        {"step_key": step.step_key, "order": step.order, "status": step.status,
                         "attempt": step.attempt, "input": step.input_data, "output": step.output_data,
                         "error": step.error}
                        for step in run.steps
                    ],
                },
                "events": [
                    {"seq": event.seq, "event_type": event.event_type, "payload": event.payload,
                     "created_at": event.created_at.isoformat()}
                    for event in store.events_for_run(run_id)
                ],
                "artifacts": store.load_run_artifacts(run_id),
            }
        if parsed.path.startswith("/api/sessions/"):
            session_id = UUID(parsed.path.removeprefix("/api/sessions/"))
            session = store.load_session(session_id)
            session_runs = []
            for run_row in store.list_runs(session_id=session_id):
                run_obj = store.load_run(UUID(run_row["id"]))
                session_runs.append({**run_row, "steps": [{"step_key": step.step_key, "order": step.order, "status": step.status, "attempt": step.attempt, "output": step.output_data} for step in run_obj.steps]})
            return {
                "session": {"id": str(session.id), "project_id": str(session.project_id), "title": session.title,
                            "status": session.status, "active_run_id": str(session.active_run_id) if session.active_run_id else None,
                            "latest_event_at": session.latest_event_at.isoformat(), "last_event_type": session.last_event_type,
                            "last_event_preview": session.last_event_preview},
                "messages": store.list_session_messages(session_id),
                "events": store.list_session_events(session_id),
                "runs": session_runs,
                "jobs": store.list_jobs(session_id=session_id),
            }
        if parsed.path.startswith("/api/reports/"):
            return store.load_report(UUID(parsed.path.removeprefix("/api/reports/")))
        raise KeyError("not found")
    finally:
        store.close()


class ResearchRequestHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/" or self.path == "/index.html":
            built_index = _frontend_dist() / "index.html"
            if built_index.is_file():
                self._send(200, built_index.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path.startswith("/assets/"):
            asset = (_frontend_dist() / self.path.removeprefix("/" )).resolve()
            root = _frontend_dist().resolve()
            if asset.is_file() and root in asset.parents:
                self._send(200, asset.read_bytes(), mimetypes.guess_type(str(asset))[0] or "application/octet-stream")
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        elif self.path in {"/api/status", "/api/settings"}:
            self._send(200, json.dumps(provider_status(), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        elif self.path.startswith(("/api/companies", "/api/company-catalog", "/api/company-panel", "/api/quote/", "/api/news", "/api/projects", "/api/search", "/api/runs", "/api/jobs/", "/api/reports/", "/api/sessions/")):
            try:
                response = history_payload(self.path)
                self._send(200, json.dumps(response, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")
            except KeyError as exc:
                self._send(404, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            except (ValueError, TypeError) as exc:
                self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
                self._send(502, json.dumps({"error": f"港股公司列表暂时不可用：{exc}"}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path not in {"/api/research", "/api/chat", "/api/settings", "/api/projects"} and not self.path.startswith(("/api/projects/", "/api/sessions/", "/api/company-panel")):
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 2_000_000:
                raise ValueError("request body is too large")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if self.path == "/api/settings":
                response = configure_provider(payload)
            elif self.path == "/api/projects":
                name = str(payload.get("name", "")).strip()
                symbol = str(payload.get("symbol", "")).strip()
                market = str(payload.get("market", "HK")).strip().upper() or "HK"
                if not name or not symbol:
                    raise ValueError("name and symbol are required")
                store = SQLiteStore(database_path())
                try:
                    project = store.find_project(symbol, market) or ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol=symbol, name=name, market=market)
                    project.name = name; project.updated_at = utc_now(); store.save_project(project)
                    session = ResearchSession(project_id=project.id, title="新研究")
                    store.save_session(session)
                    response = {"project": {"id": str(project.id), "name": project.name, "symbol": project.symbol, "market": project.market}, "session": {"id": str(session.id), "project_id": str(session.project_id), "title": session.title, "status": session.status, "latest_event_at": session.latest_event_at.isoformat()}}
                finally:
                    store.close()
            elif self.path.startswith("/api/sessions/") and self.path.endswith("/messages"):
                session_id = UUID(self.path.split("/")[3])
                response = chat_payload(session_id, str(payload.get("content", "")), as_of_date=date.fromisoformat(payload["as_of_date"]) if payload.get("as_of_date") else None, llm_config=payload.get("llm"), document_urls=payload.get("document_urls"))
            elif self.path.startswith("/api/projects/") and self.path.endswith("/sessions"):
                project_id = UUID(self.path.split("/")[3]); store = SQLiteStore(database_path())
                try:
                    project = store.load_project(project_id)
                    session = ResearchSession(project_id=project.id, title=str(payload.get("title") or "新研究"))
                    store.save_session(session)
                    response = {"session_id": str(session.id), "session": {"id": str(session.id), "project_id": str(project.id), "title": session.title, "status": session.status}}
                finally:
                    store.close()
            elif self.path.startswith("/api/sessions/") and self.path.endswith("/archive"):
                session = SQLiteStore(database_path())
                try:
                    updated = session.set_session_status(UUID(self.path.split("/")[3]), "archived")
                    response = {"session_id": str(updated.id), "status": updated.status}
                finally:
                    session.close()
            elif self.path.startswith("/api/sessions/") and self.path.endswith("/activate"):
                session = SQLiteStore(database_path())
                try:
                    updated = session.set_session_status(UUID(self.path.split("/")[3]), "active")
                    response = {"session_id": str(updated.id), "status": updated.status}
                finally:
                    session.close()
            elif self.path == "/api/research":
                response = run_research_payload(payload)
            elif self.path == "/api/company-panel/sources":
                symbol = str(payload.get("symbol", "")).strip()
                market = str(payload.get("market", "HK")).strip().upper() or "HK"
                url = str(payload.get("url", "")).strip()
                title = str(payload.get("title", "")).strip() or None
                if not symbol or not url:
                    raise ValueError("symbol and url are required")
                response = _add_company_source(symbol, market, url, title)
            elif self.path == "/api/chat":
                response = chat_entry_payload(payload)
            else:
                self._send(404, b'{"error":"not found"}', "application/json")
                return
            self._send(200, json.dumps(response, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")
        except Exception as exc:
            self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler API
        if not self.path.startswith("/api/company-panel/sources/"):
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        try:
            _remove_company_source(self.path.rsplit("/", 1)[-1])
            self._send(200, b'{"ok": true}', "application/json; charset=utf-8")
        except Exception as exc:
            self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), ResearchRequestHandler)
    print(f"AI Stock Research MVP running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
