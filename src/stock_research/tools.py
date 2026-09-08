"""Company-scoped exploration tools for the chat agent.

每个工具都绑定当前研究公司，返回带可信度标注的 Observation，供 ReAct
循环把中间结果喂回模型。探索过程不再由关键词表驱动——是否调用工具、
调用顺序都由模型在循环中自行决定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import json
from typing import Any
from urllib.parse import urlparse

from .documents import DocumentFetchError, HttpDocumentFetcher, RawDocument, extract_text
from .domain import ResearchProject
from .market_data import MarketDataError, YahooFinanceProvider
from .storage import SQLiteStore
from .trust import CLASS_PUBLIC, DEFAULT_TRUSTED_HOSTS, host_in_set
from .web_search import DuckDuckGoSearch, GoogleNewsSearch

MAX_OBSERVATION_CHARS = 1600


@dataclass(frozen=True, slots=True)
class Observation:
    """一次工具调用的结构化结果。"""

    tool: str
    args: dict[str, Any]
    text: str
    citations: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.text.startswith("工具执行失败")


class ToolError(RuntimeError):
    """Raised when a tool cannot produce a usable observation."""


def _yahoo_symbol(symbol: str, market: str) -> str:
    # 与 service.yahoo_symbol 保持一致；独立实现避免 service <-> tools 循环导入。
    digits = "".join(ch for ch in symbol if ch.isdigit())
    if market.upper() == "HK" and digits:
        return f"{int(digits):04d}.HK"
    return symbol


class CompanyTools:
    """Bound to one project so every tool result stays inside the company's identity."""

    def __init__(
        self,
        project: ResearchProject,
        store: SQLiteStore | None = None,
        trusted_hosts: set[str] | None = None,
    ) -> None:
        self.project = project
        self.store = store
        self.trusted_hosts = trusted_hosts if trusted_hosts is not None else DEFAULT_TRUSTED_HOSTS

    # -- registry ---------------------------------------------------------

    def specs(self) -> list[dict[str, str]]:
        """Tool catalog rendered into the agent prompt."""
        return [
            {"name": "query_report", "description": "在当前公司已验证的研究报告/事实中检索", "args": '{"keyword": "检索词，如 现金流 或 净利润"}'},
            {"name": "search_news", "description": "联网搜索当前公司的最新新闻/资讯", "args": '{"query": "新闻关键词"}'},
            {"name": "get_quote", "description": "获取当前公司的延迟行情快照（价格/涨跌/52周高低）", "args": "{}"},
            {"name": "fetch_filings", "description": "抓取一个公开 URL（公告/文件）并提取正文", "args": '{"url": "https://..."}'},
        ]

    def run(self, name: str, args: dict[str, Any]) -> Observation:
        handlers = {
            "query_report": self.query_report,
            "search_news": self.search_news,
            "get_quote": self.get_quote,
            "fetch_filings": self.fetch_filings,
        }
        handler = handlers.get(name)
        if handler is None:
            return Observation(name, args, f"工具执行失败：未知工具 {name}，可用工具：{', '.join(handlers)}")
        try:
            return handler(args)
        except ToolError as exc:
            return Observation(name, args, f"工具执行失败：{exc}")

    # -- tools ------------------------------------------------------------

    def query_report(self, args: dict[str, Any]) -> Observation:
        if self.store is None:
            raise ToolError("没有可用的本地存储")
        keyword = str(args.get("keyword", "")).strip()
        report = self.store.latest_report_for_project(self.project.id)
        if report is None:
            return Observation("query_report", args, "当前公司尚无已完成的研究报告，可以说“研究这家公司”先启动一次完整研究。")
        markdown = str(report.get("markdown", ""))
        lines = [line.strip() for line in markdown.splitlines() if line.strip()]
        lowered = keyword.casefold()
        hits = [line for line in lines if lowered in line.casefold()] if lowered else lines
        body = "\n".join(hits[:12])[:MAX_OBSERVATION_CHARS]
        if not body:
            body = f"报告中没有匹配「{keyword}」的内容。"
        ref = "R1"
        citation = {
            "ref": ref,
            "title": f"{self.project.name} 研究报告（已验证）",
            "url": f"report://{report.get('report_id', '')}",
            "trust": "verified",
        }
        text = f"来自已验证报告的摘录（引用为 [{ref}]）：\n{body}"
        return Observation("query_report", args, text, [citation])

    def search_news(self, args: dict[str, Any]) -> Observation:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ToolError("query 不能为空")
        scoped = f"{self.project.name} {query}"
        results = GoogleNewsSearch().search(scoped, max_results=5)
        if not results:
            results = DuckDuckGoSearch().search(scoped, max_results=5)
        if not results:
            return Observation("search_news", args, f"没有搜到「{scoped}」的相关结果。")
        citations: list[dict[str, str]] = []
        lines: list[str] = []
        for index, item in enumerate(results, 1):
            trust = "whitelist" if host_in_set(item.url, self.trusted_hosts) else "unverified"
            ref = f"N{index}"
            citations.append({"ref": ref, "title": item.title, "url": item.url, "trust": trust})
            tag = "[白名单来源]" if trust == "whitelist" else "[未验证来源]"
            lines.append(f"[{ref}]{tag} {item.title}\n{item.url}\n{item.snippet}")
        return Observation("search_news", args, "\n\n".join(lines)[:MAX_OBSERVATION_CHARS], citations)

    def get_quote(self, args: dict[str, Any]) -> Observation:
        yahoo = _yahoo_symbol(self.project.symbol, self.project.market)
        today = date.today()
        try:
            bars = YahooFinanceProvider().get_daily_bars(yahoo, today - timedelta(days=380), today)
        except (MarketDataError, ValueError) as exc:
            raise ToolError(f"行情获取失败：{exc}") from exc
        if not bars:
            raise ToolError(f"{yahoo} 暂无行情数据")
        last = bars[-1]
        prev = bars[-2] if len(bars) >= 2 else None
        change_pct = (
            f"{(last.close - prev.close) / prev.close * 100:+.2f}%" if prev and prev.close else "N/A"
        )
        body = (
            f"{self.project.name}（{yahoo}）延迟行情：最新收盘 {last.close}"
            f"（{last.trading_date.isoformat()}），日涨跌 {change_pct}，"
            f"52周区间 {min(bar.low for bar in bars)} ~ {max(bar.high for bar in bars)}。"
            "数据来自 Yahoo Finance，为延迟数据。"
        )
        return Observation("get_quote", args, body)

    def fetch_filings(self, args: dict[str, Any]) -> Observation:
        url = str(args.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            raise ToolError("url 必须以 http(s):// 开头")
        save = bool(args.get("save"))
        try:
            # 探索允许任意来源，但必须标注可信度；save=true 时登记为研究资料，
            # 供 collect_filings 收录进已验证语料（引用时保留来源标注）。
            fetched = HttpDocumentFetcher(allowed_hosts=(), allow_any_host=True).fetch(url)
            text = extract_text(fetched)
        except DocumentFetchError as exc:
            raise ToolError(f"抓取失败：{exc}") from exc
        host = urlparse(url).hostname or ""
        trust = "whitelist" if host_in_set(url, self.trusted_hosts) else "unverified"
        tag = "[白名单来源]" if trust == "whitelist" else "[未验证来源]"
        ref = "F1"
        body = text.strip()[:MAX_OBSERVATION_CHARS]
        citation = {"ref": ref, "title": url, "url": url, "trust": trust}
        note = self._save_as_document(url, text, trust) if save else ""
        return Observation("fetch_filings", args, f"{tag} {url} 的正文摘录（引用为 [{ref}]）：\n{body}{note}", [citation])

    def _save_as_document(self, url: str, text: str, trust: str) -> str:
        if self.store is None:
            raise ToolError("没有可用的本地存储，无法保存资料")
        host = urlparse(url).hostname or ""
        title = next((line.strip() for line in text.splitlines() if line.strip()), "") or url
        document = RawDocument(
            company_id=self.project.company_id,
            source_type="hkex_filing" if "hkexnews.hk" in host else "company_ir",
            source_url=url,
            title=title[:120],
            content=text,
            source_class=CLASS_PUBLIC,
            trust=trust,
        )
        self.store.save_document(document)
        # 同步登记为公司来源，下一次研究激活时自动发现该资料。
        self.store.add_company_source(self.project.company_id, url, title[:120], source_class=CLASS_PUBLIC)
        self.store.connection.commit()
        return "\n\n（已登记为该公司研究资料；研究时会自动收录，可信度按来源标注。）"


def render_tool_catalog(tools: CompanyTools) -> str:
    return json.dumps(
        [{"name": spec["name"], "description": spec["description"], "args_example": spec["args"]} for spec in tools.specs()],
        ensure_ascii=False,
    )
