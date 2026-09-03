"""Application service layer shared by the web adapter and future CLI entry points."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

from .documents import HttpDocumentFetcher, PdfTextExtractor, RawDocument, extract_text
from .domain import ResearchProject, ResearchSession, SessionMessage, utc_now
from .pipeline import ResearchPipeline
from .llm import LLMError, ModelNotConfiguredError, provider_from_config, provider_from_env
from .storage import SQLiteStore
from .workflow import ResearchWorkflow
from .jobs import create_and_enqueue
from .hk_companies import list_hk_companies
from .web_search import DuckDuckGoSearch, GoogleNewsSearch
from .market_data import MarketDataError, YahooFinanceProvider


def session_title(text: str, default: str = "长期研究") -> str:
    """Keep greetings from becoming noisy conversation titles."""
    value = " ".join(text.strip().split())
    if not value:
        return default
    if value.casefold() in {"你好", "您好", "嗨", "hello", "hi", "hey", "在吗", "测试"}:
        return default
    return value[:60]


def resolve_provider(llm_config: dict[str, Any] | None = None):
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


def document_hosts() -> tuple[str, ...]:
    configured = os.getenv("AI_STOCK_DOCUMENT_HOSTS", "www1.hkexnews.hk,www.hkexnews.hk,hkexnews.hk")
    hosts = tuple(value.strip().lower() for value in configured.split(",") if value.strip())
    if not hosts:
        raise ValueError("AI_STOCK_DOCUMENT_HOSTS must contain at least one host")
    return hosts


def source_specs(payload: dict[str, Any]) -> list[dict[str, str]]:
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


def parse_published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def research_documents(payload: dict[str, Any], company_id: UUID, as_of_date: date) -> list[RawDocument]:
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
    specs = source_specs(payload)
    if specs:
        fetcher = HttpDocumentFetcher(allowed_hosts=document_hosts())
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
            published_at = parse_published_at(spec.get("published_at") or fetched.last_modified)
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


def ensure_session(store: SQLiteStore, project: ResearchProject, session_id: UUID | None = None, title: str = "长期研究") -> ResearchSession:
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
        pipeline = ResearchPipeline(workflow=ResearchWorkflow(store=store), llm_provider=resolve_provider(payload.get("llm")))
        pipeline.workflow.create_project(project)
        session = ensure_session(store, project, session_id, title=session_title(question))
        pipeline.workflow.create_session(session)
        if not payload.get("_session_message_saved"):
            store.save_session_message(SessionMessage(session.id, "user", "text", {"text": question}))
        documents = research_documents(payload, project.company_id, as_of_date)
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


SEARCH_INTENT_WORDS = ("搜", "联网", "网络", "最新", "最近", "新闻", "资讯", "消息", "公告", "股价", "行情", "价格")


def answer_follow_up(provider: Any, content: str, report_context: str, project: ResearchProject) -> str:
    """Answer a follow-up, searching the web when the question asks for it."""
    lowered = content.casefold()
    wants_search = any(word in lowered for word in SEARCH_INTENT_WORDS)
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
        resolve_provider(llm_config)
        lowered = content.casefold()
        research_intent = any(word in lowered for word in ("研究", "分析", "估值", "长期持有", "更新研究"))
        if research_intent:
            if as_of_date is None:
                previous_runs = store.list_runs(session_id=session_id)
                as_of_date = date.fromisoformat(previous_runs[0]["as_of_date"]) if previous_runs else date.today()
            research_payload = {"name": project.name, "symbol": project.symbol, "as_of_date": as_of_date.isoformat(), "question": content, "_session_message_saved": True, "llm": llm_config, "document_urls": document_urls}
            store.save_session_message(SessionMessage(session_id, "user", "text", {"text": content}))
            if os.getenv("AI_STOCK_QUEUE", "inline").casefold() == "rq":
                job = create_and_enqueue(store, session_id, research_payload, db_path or database_path())
                return {"type": "research_queued", "session_id": str(session_id), **job}
            report = run_research_payload(research_payload, db_path=db_path or database_path(), session_id=session_id)
            return {"type": "research_started", "session_id": str(session_id), "run_id": report["run_id"], "report_id": report["report_id"], "report": report}
        store.save_session_message(SessionMessage(session_id, "user", "text", {"text": content}))
        answer = "我会基于当前公司的研究资料回答。你可以问我具体指标，或说‘研究这家公司’启动一次完整研究。"
        provider = resolve_provider(llm_config)
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
                answer = answer_follow_up(provider, content, report_context, project)
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
    resolve_provider(payload.get("llm"))
    store = SQLiteStore(db_path or database_path())
    try:
        project = store.find_project(symbol) or ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol=symbol, name=name)
        project.name = name; project.updated_at = utc_now(); store.save_project(project)
        session = ensure_session(store, project, title=session_title(content))
        store.save_session(session)
        session_id = session.id
    finally:
        store.close()
    return chat_payload(session_id, content, db_path=db_path, as_of_date=date.fromisoformat(payload["as_of_date"]) if payload.get("as_of_date") else None, llm_config=payload.get("llm"))


def create_project_with_session(payload: dict[str, Any]) -> dict[str, Any]:
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
        return {"project": {"id": str(project.id), "name": project.name, "symbol": project.symbol, "market": project.market}, "session": {"id": str(session.id), "project_id": str(project.id), "title": session.title, "status": session.status, "latest_event_at": session.latest_event_at.isoformat()}}
    finally:
        store.close()


def create_session_for_project(project_id: UUID, title: str | None = None) -> dict[str, Any]:
    store = SQLiteStore(database_path())
    try:
        project = store.load_project(project_id)
        session = ResearchSession(project_id=project.id, title=title or "新研究")
        store.save_session(session)
        return {"session_id": str(session.id), "session": {"id": str(session.id), "project_id": str(project.id), "title": session.title, "status": session.status}}
    finally:
        store.close()


def set_session_status(session_id: UUID, status: str) -> dict[str, Any]:
    store = SQLiteStore(database_path())
    try:
        updated = store.set_session_status(session_id, status)
        return {"session_id": str(updated.id), "status": updated.status}
    finally:
        store.close()


def yahoo_symbol(symbol: str, market: str) -> str:
    digits = "".join(ch for ch in symbol if ch.isdigit())
    if market.upper() == "HK" and digits:
        return f"{int(digits):04d}.HK"
    return symbol


def quote_payload(symbol: str, market: str) -> dict[str, Any]:
    """Best-effort delayed price snapshot; never raises for provider issues."""
    yahoo = yahoo_symbol(symbol, market)
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


def news_payload(name: str, symbol: str) -> list[dict[str, str]]:
    candidates = [f"{name} 最新", name, symbol]
    for candidate in candidates:
        if not candidate.strip():
            continue
        results = GoogleNewsSearch().search(candidate.strip(), max_results=8)
        if results:
            return [{"title": item.title, "url": item.url, "source": item.snippet} for item in results]
    return []


def company_panel_payload(symbol: str, market: str) -> dict[str, Any]:
    store = SQLiteStore(database_path())
    try:
        project = store.find_project(symbol, market)
        if project is None:
            return {"available": False}
        return {
            "available": True,
            "project": {"id": str(project.id), "name": project.name, "symbol": project.symbol, "market": project.market},
            "documents": store.list_company_documents(project.company_id),
            "reports": store.list_project_reports(project.id),
        }
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
        return company_panel_payload(symbol, market)
    if parsed.path == "/api/news":
        query = parse_qs(parsed.query)
        name = (query.get("name") or [""])[0].strip()
        symbol = (query.get("symbol") or [""])[0].strip()
        if not name:
            raise ValueError("name is required")
        return news_payload(name, symbol)
    if parsed.path.startswith("/api/quote/"):
        raw = parsed.path.removeprefix("/api/quote/")
        market = (parse_qs(parsed.query).get("market") or ["HK"])[0].strip().upper() or "HK"
        if not raw:
            raise ValueError("symbol is required")
        return quote_payload(raw, market)
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
