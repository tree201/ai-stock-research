"""Application service layer shared by the web adapter and future CLI entry points."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import base64
import ipaddress
import json
import re
import os
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from .documents import DocumentFetchError, FetchedDocument, HttpDocumentFetcher, PdfTextExtractor, RawDocument, UnsupportedDocumentType, extract_text
from .domain import ResearchProject, ResearchSession, SessionMessage, utc_now
from .facts import FactCandidate
from .pipeline import ResearchPipeline
from .report import ReportBuilder, diff_reports
from .llm import LLMError, ModelNotConfiguredError, OpenAICompatibleProvider, list_remote_models, provider_from_config, provider_from_env
from .llm_catalog import BUILTIN_PROVIDERS, LEVEL_LABELS, THINKING_LEVELS, default_level_for, levels_for, normalize_level, params_for_level, parse_thinking_levels
from .storage import SQLiteStore
from .trust import DEFAULT_TRUSTED_HOSTS, classify_document, host_in_set
from .workflow import ResearchWorkflow
from .jobs import create_and_enqueue
from .hk_companies import list_hk_companies, list_hk_company_names_zh
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
    if llm_config:
        provider = provider_from_config(llm_config)
    else:
        stored = stored_llm_selection()
        if stored:
            return OpenAICompatibleProvider(
                stored["base_url"], stored["api_key"], stored["model"], extra_params=stored["extra_params"]
            )
        provider = provider_from_env()
    if provider is None:
        raise ModelNotConfiguredError("尚未配置真实模型，请打开‘设置 → 模型接入’完成配置。")
    return provider


def stored_llm_selection() -> dict[str, Any] | None:
    """当前选中的 (供应商, 模型, 强度档位)；未配置或无密钥时返回 None。"""
    store = SQLiteStore(database_path())
    try:
        selection = store.get_setting("llm.selection")
        if not isinstance(selection, dict) or not selection.get("model_row_id"):
            return None
        model = store.get_llm_model(int(selection["model_row_id"]))
        if not model or not model.get("enabled"):
            return None
        provider = store.get_llm_provider(int(model["provider_id"]))
        if not provider or not provider.get("api_key"):
            return None
        level = normalize_level(selection.get("level"))
        return {
            "provider_id": int(provider["id"]),
            "provider_name": provider["name"],
            "base_url": provider["base_url"],
            "api_key": provider["api_key"],
            "model": model["model_id"],
            "display_name": model.get("display_name") or model["model_id"],
            "level": level,
            "extra_params": params_for_level(model.get("thinking_levels"), level),
        }
    finally:
        store.close()


def provider_status() -> dict[str, Any]:
    display = company_name_display()
    provider = provider_from_env()
    if provider is None:
        return {"llm_enabled": False, "provider": "", "model": "", "base_url": "", "api_key_configured": False, "company_name_display": display}
    return {"llm_enabled": True, "provider": getattr(provider, "provider_name", "configured"), "model": getattr(provider, "model", getattr(provider, "model_name", "unknown")), "base_url": getattr(provider, "base_url", ""), "api_key_configured": True, "company_name_display": display}


# 公司名称全局显示偏好：zh 中文名优先 / en 英文名优先 / bilingual 双语
DISPLAY_MODES = ("zh", "en", "bilingual")
DEFAULT_DISPLAY_MODE = "zh"
DISPLAY_SETTING_KEY = "company.display"


def company_name_display() -> str:
    store = SQLiteStore(database_path())
    try:
        value = store.get_setting(DISPLAY_SETTING_KEY)
    finally:
        store.close()
    return value if value in DISPLAY_MODES else DEFAULT_DISPLAY_MODE


def set_display_preference_payload(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("display", payload.get("mode", ""))).strip()
    if mode not in DISPLAY_MODES:
        raise ValueError(f"未知显示偏好：{mode or '<missing>'}")
    store = SQLiteStore(database_path())
    try:
        store.set_setting(DISPLAY_SETTING_KEY, mode)
    finally:
        store.close()
    return {"ok": True, "company_name_display": mode}


def hk_zh_name(symbol: str, market: str = "HK") -> str | None:
    """按 symbol 从 HKEX 官方中文目录查简体中文名；best-effort。"""
    if market.upper() != "HK":
        return None
    digits = "".join(ch for ch in symbol if ch.isdigit())
    if not digits:
        return None
    try:
        return list_hk_company_names_zh().get(digits.zfill(5))
    except OSError:
        return None


def backfill_company_names() -> int:
    """启动时为缺失中文名的公司按官方目录回填；网络失败静默跳过。"""
    try:
        mapping = list_hk_company_names_zh()
    except OSError:
        return 0
    store = SQLiteStore(database_path())
    try:
        return store.backfill_name_zh(mapping)
    finally:
        store.close()


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


# 抓取授权模式（输入框左下角权限选择器）：
#   manual 手动审批 — 仅白名单域名（内置 HKEX + trusted_hosts）可抓取
#   auto   自动审批 — 白名单自动抓取；用户消息中明确给出的链接视为已授权
#   full   完全访问 — 任意来源自动抓取，不经审批
APPROVAL_MODES = ("manual", "auto", "full")
DEFAULT_APPROVAL_MODE = "manual"
APPROVAL_SETTING_KEY = "llm.approval"


def normalize_approval_mode(value: Any) -> str:
    return value if value in APPROVAL_MODES else DEFAULT_APPROVAL_MODE


def set_approval_mode_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """保存抓取授权模式；随 config 一并返回供前端刷新选择器。"""
    mode = str(payload.get("mode", "")).strip()
    if mode not in APPROVAL_MODES:
        raise ValueError(f"未知授权模式：{mode or '<missing>'}")
    store = SQLiteStore(database_path())
    try:
        store.set_setting(APPROVAL_SETTING_KEY, mode)
        return {"ok": True, "config": llm_config_payload()}
    finally:
        store.close()


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


def research_documents(payload: dict[str, Any], company_id: UUID, as_of_date: date, store: SQLiteStore | None = None) -> list[RawDocument]:
    documents: list[RawDocument] = []
    # Trust is judged once at ingestion from the registered source class and
    # the trusted-host whitelist, then persisted with the document.
    registered_classes: dict[str, str] = store.company_source_classes(company_id) if store is not None else {}
    trusted_hosts = store.trusted_hosts_set() if store is not None else DEFAULT_TRUSTED_HOSTS
    checker = store.is_trusted_host if store is not None else (lambda host: host_in_set(host, trusted_hosts))
    if payload.get("document"):
        documents.append(RawDocument(
            company_id=company_id,
            source_type="user_text",
            source_url="user://document",
            title="user document",
            content=str(payload["document"]),
            published_at=datetime(as_of_date.year, as_of_date.month, as_of_date.day, tzinfo=timezone.utc),
            source_class="private",
            trust="verified",
        ))
    specs = source_specs(payload)
    if specs:
        mode = normalize_approval_mode(store.get_setting(APPROVAL_SETTING_KEY) if store is not None else None)
        whitelist = set(document_hosts())
        if store is not None:
            whitelist |= store.trusted_hosts_set()
        fetcher = HttpDocumentFetcher(
            allowed_hosts=sorted(whitelist),
            allow_any_host=mode == "full",
        )
        pdf_extractor = PdfTextExtractor()
        for spec in specs:
            url = spec["url"].strip()
            try:
                fetched = fetcher.fetch(url)
            except DocumentFetchError as exc:
                # auto 模式：用户消息中明确给出的链接视为已授权，白名单外放行重试一次
                if mode != "auto" or "not allowlisted" not in str(exc):
                    if mode == "manual" and "not allowlisted" in str(exc):
                        host = (urlparse(url).hostname or "").lower()
                        raise ValueError(
                            f"来源 {host or url} 不在抓取白名单：请先在「公司详情 → 来源信任」添加，"
                            "或将输入框左下角的权限授权切换为自动审批/完全访问"
                        ) from exc
                    raise
                fetched = HttpDocumentFetcher(allowed_hosts=(), allow_any_host=True).fetch(url)
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
            source_class, trust = classify_document("url", url, registered_classes.get(url), checker)
            documents.append(RawDocument(
                company_id=company_id,
                source_type="hkex_filing" if "hkexnews.hk" in host else "company_ir",
                source_url=url,
                title=spec.get("title") or Path(urlparse(url).path).name or "public filing",
                content=content,
                published_at=published_at,
                language=spec.get("language"),
                source_class=source_class,
                trust=trust,
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


def default_dcf_assumptions() -> dict[str, Any]:
    return {
        "revenue_prior": 609_000_000_000,
        "revenue_years": 1,
        "growth_rates": [0.15, 0.12, 0.10, 0.08, 0.06],
        "discount_rate": 0.09,
        "terminal_growth": 0.03,
        "shares": 9_000_000_000,
    }


def merged_research_payload(store: SQLiteStore, project: ResearchProject, payload: dict[str, Any]) -> dict[str, Any]:
    """Merge requested document URLs with the company's registered sources."""
    requested_urls = payload.get("document_urls") or payload.get("document_url") or []
    if isinstance(requested_urls, str):
        requested_urls = [value.strip() for value in requested_urls.replace(",", "\n").splitlines() if value.strip()]
    if not isinstance(requested_urls, list):
        requested_urls = []
    registered_urls = store.list_company_source_urls(project.company_id)
    seen: set[str] = set()
    merged: list[str] = []
    for url in [*requested_urls, *registered_urls]:
        value = str(url).strip()
        if value and value not in seen:
            seen.add(value)
            merged.append(value)
    return {**payload, "document_urls": merged}


def run_research_payload(payload: dict[str, Any], db_path: str | Path | None = None, session_id: UUID | None = None) -> dict[str, Any]:
    if str(payload.get("mode", "")).strip().casefold() == "update":
        return run_update_payload(payload, db_path=db_path, session_id=session_id)
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
        payload = merged_research_payload(store, project, payload)
        documents = research_documents(payload, project.company_id, as_of_date, store=store)
        resume_raw = payload.get("_resume_run_id")
        resume_run_id = UUID(str(resume_raw)) if resume_raw else None
        report = pipeline.run(
            project_id=project.id,
            session_id=session.id,
            question=question,
            as_of_date=as_of_date,
            documents=documents,
            dcf_assumptions=default_dcf_assumptions(),
            resume_run_id=resume_run_id,
        )
        report["session_id"] = str(session.id)
        store.save_session_message(SessionMessage(session.id, "assistant", "report_card", {"text": "研究已完成", "report_id": report["report_id"], "summary": report.get("summary", [])}, run_id=UUID(report["run_id"]), report_id=UUID(report["report_id"])))
        session.active_run_id = None
        session.updated_at = utc_now()
        store.save_session(session)
        return report
    finally:
        store.close()


def run_update_payload(payload: dict[str, Any], db_path: str | Path | None = None, session_id: UUID | None = None) -> dict[str, Any]:
    """Incremental update: ingest only documents unseen in previous runs.

    New documents are identified by content hash against everything already
    ingested for the company.  The fresh run then produces a report that is
    diffed against the most recent one (new facts, changed values and the
    base-scenario valuation move).
    """
    name = str(payload.get("name", "")).strip()
    symbol = str(payload.get("symbol", "")).strip()
    question = str(payload.get("question", "")).strip()
    as_of_date = date.fromisoformat(str(payload.get("as_of_date", "")))
    if not name or not symbol or not question:
        raise ValueError("name, symbol and question are required")
    store = SQLiteStore(db_path or database_path())
    try:
        project = store.find_project(symbol)
        if project is None:
            raise ValueError("没有找到该公司的研究档案，请先完成一次初始研究再运行更新。")
        report_rows = store.list_project_reports(project.id)
        if not report_rows:
            raise ValueError("没有历史报告可对比，请先完成一次初始研究。")
        previous = store.load_report(UUID(report_rows[0]["id"]))
        project.updated_at = utc_now()
        pipeline = ResearchPipeline(workflow=ResearchWorkflow(store=store), llm_provider=resolve_provider(payload.get("llm")))
        pipeline.workflow.create_project(project)
        session = ensure_session(store, project, session_id, title=session_title(question))
        pipeline.workflow.create_session(session)
        if not payload.get("_session_message_saved"):
            store.save_session_message(SessionMessage(session.id, "user", "text", {"text": question}))
        merged = merged_research_payload(store, project, payload)
        documents = research_documents(merged, project.company_id, as_of_date, store=store)
        seen_hashes = store.list_seen_content_hashes(project.company_id)
        new_documents = [document for document in documents if document.content_hash not in seen_hashes]
        if not new_documents:
            raise ValueError("未检测到新资料：登记源中没有上次研究之后的新文档；可先在「公司档案 → 资料」登记新的公告链接，或粘贴新的财报文本。")

        def attach_diff(report: dict[str, Any]) -> dict[str, Any]:
            report["diff"] = diff_reports(previous, report)
            report["update_of"] = str(previous.get("report_id"))
            report["markdown"] = ReportBuilder.to_markdown(report)
            return report

        report = pipeline.run(
            project_id=project.id,
            session_id=session.id,
            question=question,
            as_of_date=as_of_date,
            documents=new_documents,
            dcf_assumptions=default_dcf_assumptions(),
            run_type="update",
            report_transform=attach_diff,
        )
        report["session_id"] = str(session.id)
        store.save_session_message(SessionMessage(session.id, "assistant", "report_card", {"text": "增量更新完成", "report_id": report["report_id"], "summary": report.get("summary", [])}, run_id=UUID(report["run_id"]), report_id=UUID(report["report_id"])))
        session.active_run_id = None
        session.updated_at = utc_now()
        store.save_session(session)
        return report
    finally:
        store.close()


SEARCH_INTENT_WORDS = ("搜", "联网", "网络", "最新", "最近", "新闻", "资讯", "消息", "公告", "股价", "行情", "价格")


ASSUMPTION_KEYS = frozenset({"revenue_prior", "revenue_years", "growth_rates", "discount_rate", "terminal_growth", "shares", "base_fcf", "net_cash"})


def parse_dcf_assumptions(payload: Any) -> dict[str, Any]:
    """Validate user-supplied valuation assumptions at the service boundary."""
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("dcf_assumptions must be an object")
    unknown = set(payload) - ASSUMPTION_KEYS
    if unknown:
        raise ValueError(f"unknown assumption keys: {', '.join(sorted(str(key) for key in unknown))}")
    assumptions: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "growth_rates":
            if not isinstance(value, list) or not value or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
                raise ValueError("growth_rates must be a non-empty list of numbers")
            assumptions[key] = [float(item) for item in value]
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a number")
            assumptions[key] = float(value)
    return assumptions


def recalculate_report(report_id: str, assumptions: dict[str, Any] | None = None, db_path: str | Path | None = None) -> dict[str, Any]:
    """Re-run valuation and report assembly from a previous report's facts.

    Facts, qualitative signals and model claims are reused verbatim; only the
    code-driven calculation and report compilation run again.  The result is
    persisted as a new report version on the same run, so no model call and
    no document download is required.
    """
    dcf_assumptions = parse_dcf_assumptions(assumptions)
    store = SQLiteStore(db_path or database_path())
    try:
        previous = store.load_report(UUID(str(report_id)))
        run_id = UUID(previous["run_id"])
        run = store.load_run(run_id)
        project = store.load_project(run.project_id)
        facts: list[FactCandidate] = []
        evidence_sources: dict[str, dict[str, Any]] = {}
        for row in previous.get("facts", []):
            citation = row.get("citation") or {}
            evidence_id = citation.get("evidence_id")
            if evidence_id:
                evidence_sources[str(evidence_id)] = {key: citation[key] for key in ("source_url", "source_title", "page", "source_class", "trust") if key in citation}
            facts.append(FactCandidate(
                metric=str(row["metric"]),
                value=float(row["value"]),
                currency=row.get("currency"),
                unit=row.get("unit"),
                period_end=date.fromisoformat(row["period_end"]) if row.get("period_end") else None,
                evidence_id=UUID(str(evidence_id)) if evidence_id else uuid4(),
                source_line=int(citation.get("source_line", 0)),
                raw_text=str(citation.get("raw_text", "")),
                confidence=float(row.get("confidence", 0.0)),
            ))
        calculations = ResearchPipeline._calculate(facts, dcf_assumptions)
        review = ResearchPipeline._review(facts, calculations, run.as_of_date)
        report = ReportBuilder().build(
            company={"symbol": project.symbol, "name": project.name, "market": project.market},
            question=run.question,
            as_of_date=run.as_of_date,
            facts=facts,
            calculations=calculations,
            qualitative_signals=previous.get("qualitative_signals") or {},
            llm_claims=previous.get("llm_claims") or [],
            review=review,
            evidence_sources=evidence_sources,
        )
        report["report_id"] = str(uuid4())
        report["recalculated_from"] = str(previous["report_id"])
        report["dcf_assumptions"] = dcf_assumptions
        saved_id = store.save_report(run_id, report, UUID(report["report_id"]))
        version_row = store.connection.execute("SELECT version FROM reports WHERE id=?", (str(saved_id),)).fetchone()
        report["version"] = int(version_row[0]) if version_row else None
        store.append_event(run_id, "report/recalculated", {
            "report_id": str(saved_id),
            "previous_report_id": str(previous["report_id"]),
            "dcf_assumptions": dcf_assumptions,
        }, utc_now())
        if run.session_id:
            store.save_session_message(SessionMessage(
                run.session_id, "assistant", "report_card",
                {"text": "估值假设已调整，报告已基于同一批事实重新计算", "report_id": str(saved_id), "summary": report.get("summary", [])},
                run_id=run_id, report_id=saved_id,
            ))
        return report
    finally:
        store.close()


def answer_follow_up(provider: Any, content: str, report_context: str, project: ResearchProject, trusted_hosts: Iterable[str] | None = None) -> str:
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
            annotated = [
                {
                    "title": item.title,
                    "url": item.url,
                    "snippet": item.snippet,
                    "trust": "whitelist" if host_in_set(item.url, trusted_hosts if trusted_hosts is not None else DEFAULT_TRUSTED_HOSTS) else "unverified",
                }
                for item in results
            ]
            return provider.answer_with_search(content, annotated, report_context)
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
        research_intent = any(word in lowered for word in ("研究", "分析", "估值", "长期持有", "更新"))
        if research_intent:
            if as_of_date is None:
                as_of_date = date.today()
            research_payload = {"name": project.name, "symbol": project.symbol, "as_of_date": as_of_date.isoformat(), "question": content, "_session_message_saved": True, "llm": llm_config, "document_urls": document_urls, "mode": "update" if "更新" in lowered else "initial"}
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
                answer = answer_follow_up(provider, content, report_context, project, trusted_hosts=store.trusted_hosts_set())
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
        project.name = name
        if not project.name_zh:
            project.name_zh = hk_zh_name(symbol, market)  # best-effort 附带官方简体中文名
        project.updated_at = utc_now(); store.save_project(project)
        session = ResearchSession(project_id=project.id, title="新研究")
        store.save_session(session)
        return {"project": {"id": str(project.id), "name": project.name, "name_zh": project.name_zh, "symbol": project.symbol, "market": project.market}, "session": {"id": str(session.id), "project_id": str(project.id), "title": session.title, "status": session.status, "latest_event_at": session.latest_event_at.isoformat()}}
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


DEFAULT_NEWS_LIMIT = 60


def news_payload(name: str, symbol: str, query: str | None = None) -> list[dict[str, Any]]:
    """Company news from Google News RSS.

    Without a query, ``"{name} 最新"`` and ``{name}`` are merged and deduped
    by URL for broader coverage (capped at ``DEFAULT_NEWS_LIMIT``); the bare
    symbol is a last-resort fallback.  With a query the search stays scoped
    to this company (``"{name} {query}"``) — no raw-keyword fallback, because
    unscoped results are noise inside a company panel.  An empty result means
    "nothing found for this company", not an invitation to search the web.
    """
    store = SQLiteStore(database_path())
    try:
        trusted_hosts = store.trusted_hosts_set()
    finally:
        store.close()
    google = GoogleNewsSearch()
    trimmed = (query or "").strip()
    if trimmed:
        return [_news_item(item, trusted_hosts) for item in google.search(f"{name} {trimmed}", max_results=40)]
    collected: list[Any] = []
    seen: set[str] = set()
    for candidate in (f"{name} 最新", name):
        for item in google.search(candidate, max_results=40):
            resolved = resolve_google_news_url(item.url)
            if resolved in seen:
                continue
            seen.add(resolved)
            collected.append(item)
    if not collected:
        collected = list(google.search(symbol, max_results=20))
    return [_news_item(item, trusted_hosts) for item in collected[:DEFAULT_NEWS_LIMIT]]


def _news_item(item: Any, trusted_hosts: Iterable[str] = ()) -> dict[str, Any]:
    """Resolve Google News redirects upfront and flag unresolvable ones.

    Old-format redirect IDs resolve to the publisher URL and stay readable
    in-app; new-format IDs cannot be resolved server-side, so they are marked
    ``external`` and the UI opens them directly in a new tab.  Resolved
    publishers on the trusted-host whitelist additionally get ``trust:
    'whitelist'``; everything else is ``unverified`` (news is never persisted,
    so this is judged live per request).
    """
    resolved = resolve_google_news_url(item.url)
    external = "news.google.com" in urlparse(resolved).netloc
    trusted = (not external) and host_in_set(resolved, trusted_hosts)
    return {
        "title": item.title,
        "url": resolved,
        "source": item.snippet,
        "time": item.published,
        "external": external,
        "trust": "whitelist" if trusted else "unverified",
    }


def resolve_google_news_url(url: str) -> str:
    """Resolve old-format Google News redirect IDs to the publisher URL.

    New-format IDs encode an internal story key that only resolves via an
    undocumented Google endpoint; those stay as-is and the reader falls back
    to the new-tab affordance.
    """
    if "news.google.com" not in urlparse(url).netloc:
        return url
    if "/articles/" not in url:
        return url
    article_id = url.split("/articles/")[1].split("?")[0]
    padded = article_id + "=" * (-len(article_id) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
    except Exception:
        return url
    match = re.search(rb"https://[\x21-\x7e]+", decoded)
    if match:
        return match.group(0).decode("ascii", "ignore")
    return url


def article_payload(url: str) -> dict[str, Any]:
    """Fetch a public article and extract readable text for the in-app reader."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("阅读地址必须是 http(s) 链接")
    resolved = resolve_google_news_url(url)
    if "news.google.com" in urlparse(resolved).netloc:
        raise ValueError("该新闻由 Google 新闻中转，暂不支持站内阅读，请在新标签页打开原文")
    host = (urlparse(resolved).hostname or "").lower()
    blocked = not host or host in {"localhost"} or host.endswith(".local")
    if not blocked:
        try:
            parsed_ip = ipaddress.ip_address(host)
            blocked = parsed_ip.is_private or parsed_ip.is_loopback or parsed_ip.is_link_local
        except ValueError:
            blocked = False
    if blocked:
        raise ValueError("该地址不支持在阅读器中打开，请使用新标签页访问")
    request = Request(
        resolved,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            body = response.read(2_000_000)
            content_type = response.headers.get("Content-Type", "text/html")
            final_url = response.geturl()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"正文抓取失败：{exc}") from exc
    lowered_head = body[:2048].lstrip().lower()
    if "html" not in content_type and (lowered_head.startswith(b"<!doctype") or lowered_head.startswith(b"<html")):
        content_type = "text/html"
    try:
        text = extract_text(FetchedDocument(final_url, content_type, body, datetime.now(timezone.utc)))
    except UnsupportedDocumentType:
        raise ValueError("该链接的内容类型暂不支持站内阅读，请在新标签页打开原文")
    if not text.strip():
        raise ValueError("未能提取到正文，请使用新标签页访问原文")
    return {"ok": True, "url": final_url, "text": text[:60000]}


def company_panel_payload(symbol: str, market: str) -> dict[str, Any]:
    store = SQLiteStore(database_path())
    try:
        project = store.find_project(symbol, market)
        if project is None:
            return {"available": False}
        return {
            "available": True,
            "project": {"id": str(project.id), "name": project.name, "name_zh": project.name_zh, "symbol": project.symbol, "market": project.market},
            "sources": store.list_company_sources(project.company_id),
            "documents": store.list_company_documents(project.company_id),
            "reports": store.list_project_reports(project.id),
        }
    finally:
        store.close()


def add_company_source(symbol: str, market: str, url: str, title: str | None = None, source_class: str = "private") -> dict[str, Any]:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("资料 URL 必须是 http(s) 链接")
    if source_class not in ("private", "public"):
        raise ValueError("source_class 必须是 private 或 public")
    store = SQLiteStore(database_path())
    try:
        project = store.find_project(symbol, market)
        if project is None:
            raise ValueError("company not found")
        source = store.add_company_source(project.company_id, url, (title or "").strip() or None, source_class)
        return {"available": True, "source": source, "sources": store.list_company_sources(project.company_id)}
    finally:
        store.close()


def remove_company_source(source_id: str) -> None:
    store = SQLiteStore(database_path())
    try:
        store.remove_company_source(UUID(source_id))
    finally:
        store.close()


def remove_company(symbol: str, market: str, db_path: str | Path | None = None) -> dict[str, Any]:
    """Delete a company and all of its research data permanently."""
    symbol = symbol.strip()
    market = (market or "HK").strip().upper() or "HK"
    store = SQLiteStore(db_path or database_path())
    try:
        project = store.find_project(symbol, market)
        if project is None:
            raise ValueError("company not found")
        counts = store.delete_company(project.company_id)
        return {"ok": True, "symbol": symbol, "market": market, "deleted": counts}
    finally:
        store.close()


def trusted_hosts_payload() -> dict[str, Any]:
    store = SQLiteStore(database_path())
    try:
        return {"hosts": store.list_trusted_hosts()}
    finally:
        store.close()


def add_trusted_host_payload(payload: dict[str, Any]) -> dict[str, Any]:
    host = str(payload.get("host", "")).strip()
    if not host:
        raise ValueError("host is required")
    label = str(payload.get("label", "")).strip() or None
    store = SQLiteStore(database_path())
    try:
        stored = store.add_trusted_host(host, label)
        return {"host": stored, "hosts": store.list_trusted_hosts()}
    finally:
        store.close()


def remove_trusted_host_payload(host_id: str) -> None:
    store = SQLiteStore(database_path())
    try:
        store.remove_trusted_host(int(host_id))
    finally:
        store.close()


# --- LLM hub: 供应商/模型统一接入 + 选择状态 -------------------------------------

# Provider ID（机器身份）格式，与 deepseek-harness 的 ROUTE_PATTERN 一致。
_LLM_ROUTE_PATTERN = re.compile(r"[a-z][a-z0-9-]*")


def llm_config_payload() -> dict[str, Any]:
    """模型接入总览：供应商（密钥脱敏）、模型目录、当前选择与最近使用。"""
    store = SQLiteStore(database_path())
    try:
        providers = store.list_llm_providers()
        models = store.list_llm_models()
        selection = store.get_setting("llm.selection")
        if not isinstance(selection, dict):
            selection = {}
        recent_ids = store.get_setting("llm.recent")
        if not isinstance(recent_ids, list):
            recent_ids = []
        approval_mode = normalize_approval_mode(store.get_setting(APPROVAL_SETTING_KEY))
    finally:
        store.close()

    providers_out = [
        {**provider, "api_key": None, "has_api_key": bool(provider.get("api_key"))}
        for provider in providers
    ]
    models_out = [
        {
            "id": model["id"],
            "provider_id": model["provider_id"],
            "model_id": model["model_id"],
            "display_name": model.get("display_name") or model["model_id"],
            "thinking_levels": parse_thinking_levels(model.get("thinking_levels")),
            "levels": levels_for(model.get("thinking_levels")),
            "default_level": default_level_for(model.get("thinking_levels"), model.get("default_level")),
            "context_window": model.get("context_window"),
            "max_tokens": model.get("max_tokens"),
        }
        for model in models
    ]
    by_row = {model["id"]: model for model in models_out}
    # 尚未添加的内置供应商目录（deepseek-harness configurable directory 语义）：
    # 行列表之外的预设出现在「添加提供方」里，而不是默认铺满整页。
    known_routes = {provider["route"] for provider in providers}
    catalog = [
        {
            "key": preset["route"],
            "route": preset["route"],
            "protocol": preset["protocol"],
            "name": preset["name"],
            "base_url": preset["base_url"],
            "model_count": len(preset["models"]),
            "models": [{"model_id": model["model_id"], "display_name": model["display_name"]} for model in preset["models"]],
        }
        for preset in BUILTIN_PROVIDERS
        if preset["route"] not in known_routes
    ]

    def _entry(model_row_id: Any, level: Any = None) -> dict[str, Any] | None:
        model = by_row.get(int(model_row_id)) if model_row_id is not None else None
        if not model:
            return None
        provider = next((p for p in providers_out if p["id"] == model["provider_id"]), None)
        if not provider:
            return None
        resolved = level if level else model["default_level"]
        return {
            "model_row_id": model["id"],
            "provider_id": provider["id"],
            "provider_name": provider["name"],
            "model_id": model["model_id"],
            "display_name": model["display_name"],
            "level": normalize_level(resolved),
            "has_api_key": provider["has_api_key"],
        }

    current = _entry(selection.get("model_row_id"), selection.get("level")) if selection else None
    recent = [entry for entry in (_entry(row_id) for row_id in recent_ids) if entry]
    return {
        "providers": providers_out,
        "models": models_out,
        "catalog": catalog,
        "levels": [{"value": level, "label": LEVEL_LABELS[level]} for level in THINKING_LEVELS],
        "selection": current,
        "recent": recent,
        "approval_mode": approval_mode,
    }


def save_llm_provider_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """创建或更新供应商；带 id 时为更新（api_key 留空表示不改）。

    创建分两种（deepseek-harness 两种获得提供方的方式）：带 preset 时从内置
    目录落地一行（密钥可留空，行上显示缺失圆点）；否则为自定义提供方，必须
    带 route（机器身份，唯一）+ name（显示名，可改）+ protocol（线路协议）。
    """
    store = SQLiteStore(database_path())
    try:
        name = str(payload.get("name", "")).strip()
        base_url = str(payload.get("base_url", "")).strip()
        api_key = str(payload.get("api_key", "")).strip()
        route = str(payload.get("route", "")).strip()
        protocol = str(payload.get("protocol", "")).strip()
        provider_id = payload.get("id")
        if provider_id:
            fields: dict[str, Any] = {}
            if name:
                fields["name"] = name
            if base_url:
                fields["base_url"] = base_url
            if api_key:
                fields["api_key"] = api_key
            stored = store.update_llm_provider(int(provider_id), **fields)
        else:
            preset = str(payload.get("preset") or "").strip()
            if preset:
                match = next((item for item in BUILTIN_PROVIDERS if item["route"] == preset), None)
                if match is None:
                    raise KeyError("预设供应商不存在")
                stored = store.add_llm_provider(
                    name or match["name"],
                    base_url or match["base_url"],
                    api_key or None,
                    builtin=True,
                    route=match["route"],
                    protocol=match["protocol"],
                )
            else:
                if not name or not base_url:
                    raise ValueError("name 和 base_url 必填")
                if not _LLM_ROUTE_PATTERN.fullmatch(route):
                    raise ValueError("Provider ID 需以小写字母开头，之后可用小写字母、数字和短横线")
                stored = store.add_llm_provider(
                    name,
                    base_url,
                    api_key or None,
                    route=route,
                    protocol=protocol or "openai-compatible",
                )
        return {"provider": {**stored, "api_key": None, "has_api_key": bool(stored.get("api_key"))}, "config": llm_config_payload()}
    finally:
        store.close()


def remove_llm_provider_payload(provider_id: str) -> dict[str, Any]:
    store = SQLiteStore(database_path())
    try:
        store.remove_llm_provider(int(provider_id))
        return {"ok": True, "config": llm_config_payload()}
    finally:
        store.close()


def add_llm_model_payload(payload: dict[str, Any]) -> dict[str, Any]:
    store = SQLiteStore(database_path())
    try:
        store.add_llm_model(
            int(payload.get("provider_id")),
            str(payload.get("model_id", "")),
            str(payload.get("display_name") or "") or None,
            payload.get("thinking_levels"),
            payload.get("default_level"),
        )
        return {"ok": True, "config": llm_config_payload()}
    finally:
        store.close()


def sync_llm_models_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """以草稿数组为准同步供应商的模型目录（deepseek-harness 整数组替换语义）。

    前端在编辑器里整表编辑（增删改行、发现导入），保存时一次提交最终列表。
    """
    provider_id = int(payload.get("provider_id"))
    entries = payload.get("models")
    if not isinstance(entries, list):
        raise ValueError("models 必须是列表")
    store = SQLiteStore(database_path())
    try:
        store.sync_llm_models(provider_id, entries)
        return {"ok": True, "config": llm_config_payload()}
    finally:
        store.close()


def discover_llm_models_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """调提供方的 /models 接口发现可用模型（deepseek-harness discovery 语义）。

    询问的是「表单当前显示」的端点：编辑已有提供方时按 provider_id 回落读取
    存储值；创建卡片则直接带未保存的 base_url 与密钥来问，省去先存再返。
    """
    provider_id = payload.get("provider_id")
    base_url = str(payload.get("base_url") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    store = SQLiteStore(database_path())
    try:
        existing: set[str] = set()
        if provider_id:
            provider = store.get_llm_provider(int(provider_id))
            if not provider:
                raise KeyError("供应商不存在")
            base_url = base_url or str(provider.get("base_url") or "").strip()
            api_key = api_key or str(provider.get("api_key") or "").strip()
            existing = {model["model_id"] for model in store.list_llm_models(int(provider_id))}
    finally:
        store.close()
    if not base_url:
        raise ValueError("请先填写接口地址，再获取")
    if not api_key:
        raise ValueError("请先填写 API 密钥，再获取")
    remote = list_remote_models(base_url, api_key)
    return {
        "models": [{"model_id": model_id, "added": model_id in existing} for model_id in remote],
    }


def remove_llm_model_payload(model_row_id: str) -> dict[str, Any]:
    store = SQLiteStore(database_path())
    try:
        store.remove_llm_model(int(model_row_id))
        return {"ok": True, "config": llm_config_payload()}
    finally:
        store.close()


def set_llm_selection_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """切换当前模型与强度档位；未指定档位时用模型默认档（deepseek-harness defaultEffort 语义）。"""
    model_row_id = int(payload.get("model_row_id"))
    store = SQLiteStore(database_path())
    try:
        model = store.get_llm_model(model_row_id)
        if not model:
            raise KeyError("模型不存在")
        raw_level = str(payload.get("level") or "").strip()
        level = normalize_level(raw_level) if raw_level else default_level_for(model.get("thinking_levels"), model.get("default_level"))
        selection = {"model_row_id": model_row_id, "provider_id": model["provider_id"], "model_id": model["model_id"], "level": level}
        store.set_setting("llm.selection", selection)
        recent = store.get_setting("llm.recent")
        if not isinstance(recent, list):
            recent = []
        recent = [model_row_id] + [int(row) for row in recent if int(row) != model_row_id]
        store.set_setting("llm.recent", recent[:8])
        return {"ok": True, "config": llm_config_payload()}
    finally:
        store.close()


def history_payload(path: str) -> dict[str, Any] | list[dict[str, Any]]:
    parsed = urlparse(path)
    if parsed.path == "/api/article":
        url = (parse_qs(parsed.query).get("url") or [""])[0].strip()
        if not url:
            raise ValueError("url is required")
        return article_payload(url)
    if parsed.path == "/api/company-panel":
        query = parse_qs(parsed.query)
        symbol = (query.get("symbol") or [""])[0].strip()
        market = (query.get("market") or ["HK"])[0].strip().upper() or "HK"
        if not symbol:
            raise ValueError("symbol is required")
        return company_panel_payload(symbol, market)
    if parsed.path == "/api/trusted-hosts":
        return trusted_hosts_payload()
    if parsed.path == "/api/news":
        query = parse_qs(parsed.query)
        name = (query.get("name") or [""])[0].strip()
        symbol = (query.get("symbol") or [""])[0].strip()
        search = (query.get("q") or [""])[0].strip()
        if not name:
            raise ValueError("name is required")
        return news_payload(name, symbol, query=search or None)
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
                    """SELECT s.*, p.id AS company_id, p.name AS company_name, p.name_zh AS company_name_zh, p.symbol AS company_symbol, p.market AS company_market
                       FROM sessions s JOIN projects p ON p.id=s.project_id
                       WHERE s.title LIKE ? OR s.last_event_preview LIKE ?
                          OR EXISTS (SELECT 1 FROM session_messages m WHERE m.session_id=s.id AND m.content_json LIKE ?)
                       ORDER BY s.latest_event_at DESC, s.created_at DESC LIMIT 50""",
                    (pattern, pattern, pattern),
                ).fetchall()
            return [{"session": {"id": row["id"], "project_id": row["project_id"], "title": row["title"], "status": row["status"], "latest_event_at": row["latest_event_at"], "last_event_preview": row["last_event_preview"]}, "company": {"id": row["company_id"], "name": row["company_name"], "name_zh": row["company_name_zh"], "symbol": row["company_symbol"], "market": row["company_market"]}} for row in rows]
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
