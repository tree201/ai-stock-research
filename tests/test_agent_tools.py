"""Tests for the unified agent loop, company-scoped tools and chat trace."""

import os
from datetime import date, datetime, timezone
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from stock_research.agent_core import UnifiedTools, run_agent_turn
from stock_research.domain import ResearchProject
from stock_research.market_data import PriceBar
from stock_research.service import chat_payload, run_research_payload
from stock_research.storage import SQLiteStore
from stock_research.tools import CompanyTools, Observation
from stock_research.web_search import SearchResult


def _load_project(db_path: str, create: bool = False) -> ResearchProject:
    """Load the seeded project (real id) or create a fresh detached one."""
    if not create:
        store = SQLiteStore(db_path)
        try:
            rows = store.list_projects()
            if rows:
                return store.load_project(UUID(rows[0]["id"]))
        finally:
            store.close()
    return ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯控股", market="HK")


class StubTools:
    """Deterministic tool double for loop-mechanics tests."""

    def specs(self) -> list[dict[str, str]]:
        return [{"name": "stub", "description": "stub tool", "args": "{}"}]

    def run(self, name: str, args: dict) -> Observation:
        return Observation(name, args, f"{name} 的观察内容", [
            {"ref": "x", "title": "来源", "url": "https://example.com/a", "trust": "whitelist"},
        ])


class FakeAgentProvider:
    """Scripted JSON decisions; records prompts for identity assertions."""

    def __init__(self, decisions: list[dict]) -> None:
        self.decisions = list(decisions)
        self.system_prompts: list[str] = []
        self.user_prompts: list[str] = []

    def chat_json(self, system: str, user: str) -> dict:
        self.system_prompts.append(system)
        self.user_prompts.append(user)
        if not self.decisions:
            raise AssertionError("provider ran out of scripted decisions")
        return self.decisions.pop(0)


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self._db_env = patch.dict(os.environ, {"AI_STOCK_DB": f"{self._dir.name}/research.sqlite3"})
        self._db_env.start()
        self.addCleanup(self._db_env.stop)
        self.db_path = f"{self._dir.name}/research.sqlite3"

    def _seed_report(self) -> dict:
        with patch("stock_research.service.resolve_provider", return_value=None):
            return run_research_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million\nNet income FY2024 HK$ 20 million",
            }, db_path=self.db_path)

    def test_query_report_returns_verified_excerpt(self) -> None:
        self._seed_report()
        project = _load_project(self.db_path)
        observation = CompanyTools(project, store=SQLiteStore(self.db_path)).query_report({"keyword": "revenue"})
        self.assertTrue(observation.ok)
        self.assertIn("revenue", observation.text.lower())
        self.assertEqual(observation.citations[0]["trust"], "verified")

    def test_query_report_without_report_is_friendly(self) -> None:
        project = _load_project(self.db_path, create=True)
        observation = CompanyTools(project, store=SQLiteStore(self.db_path)).query_report({"keyword": "现金流"})
        self.assertTrue(observation.ok)
        self.assertIn("尚无已完成的研究报告", observation.text)

    def test_search_news_scopes_to_company_and_annotates_trust(self) -> None:
        project = _load_project(self.db_path, create=True)
        fake_results = [SearchResult(title="t", url="https://example.com/news", snippet="s")]

        class FakeNews:
            def search(self, query: str, max_results: int = 5):
                self.query = query
                return fake_results

        news = FakeNews()
        tools = CompanyTools(project, trusted_hosts={"example.com"})
        with patch("stock_research.tools.GoogleNewsSearch", return_value=news):
            observation = tools.search_news({"query": "回购"})
        self.assertIn("腾讯控股", news.query)
        self.assertIn("[白名单来源]", observation.text)
        self.assertEqual(observation.citations[0]["trust"], "whitelist")

    def test_get_quote_formats_snapshot(self) -> None:
        project = _load_project(self.db_path, create=True)
        bar = PriceBar(
            symbol="00700.HK", trading_date=date(2025, 12, 31),
            open=100.0, high=110.0, low=95.0, close=105.0, volume=1000,
            provider="fake", fetched_at=datetime(2025, 12, 31, tzinfo=timezone.utc),
        )
        captured: dict[str, str] = {}

        class FakeYahoo:
            def get_daily_bars(self, symbol: str, start: date, end: date):
                captured["symbol"] = symbol
                return [bar]

        with patch("stock_research.tools.YahooFinanceProvider", return_value=FakeYahoo()):
            observation = CompanyTools(project).get_quote({})
        self.assertEqual(captured["symbol"], "0700.HK")
        self.assertIn("105.0", observation.text)
        self.assertIn("52周区间", observation.text)

    def test_fetch_filings_annotates_unverified_host(self) -> None:
        project = _load_project(self.db_path, create=True)

        class FakeFetcher:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def fetch(self, url: str):
                return object()

        with patch("stock_research.tools.HttpDocumentFetcher", FakeFetcher), \
             patch("stock_research.tools.extract_text_with_pages", return_value=("公告正文内容", ())):
            observation = CompanyTools(project).fetch_filings({"url": "https://unknown-host.example.com/a.pdf"})
        self.assertIn("[未验证来源]", observation.text)
        self.assertEqual(observation.citations[0]["trust"], "unverified")

    def test_fetch_filings_rejects_non_http_url(self) -> None:
        project = _load_project(self.db_path, create=True)
        observation = CompanyTools(project).run("fetch_filings", {"url": "file:///etc/passwd"})
        self.assertFalse(observation.ok)

    def test_fetch_filings_save_registers_document_and_source(self) -> None:
        project = _load_project(self.db_path, create=True)
        store = SQLiteStore(self.db_path)

        class FakeFetcher:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def fetch(self, url: str):
                return object()

        with patch("stock_research.tools.HttpDocumentFetcher", FakeFetcher), \
             patch("stock_research.tools.extract_text_with_pages", return_value=("中国食品 2024 年度业绩公告\n净利润 25.8 亿港元", ())):
            observation = CompanyTools(project, store=store).fetch_filings(
                {"url": "https://www1.hkexnews.hk/listedco/listconews/a.pdf", "save": True},
            )
        self.assertIn("已登记为该公司研究资料", observation.text)
        rows = store.connection.execute("SELECT title, trust, source_type FROM documents WHERE company_id=?", (str(project.company_id),)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trust"], "whitelist")
        self.assertEqual(rows[0]["source_type"], "hkex_filing")
        self.assertIn("中国食品 2024 年度业绩公告", rows[0]["title"])
        sources = store.list_company_source_urls(project.company_id)
        self.assertEqual(sources, ["https://www1.hkexnews.hk/listedco/listconews/a.pdf"])

    def test_fetch_filings_save_without_store_fails_gracefully(self) -> None:
        project = _load_project(self.db_path, create=True)

        class FakeFetcher:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def fetch(self, url: str):
                return object()

        with patch("stock_research.tools.HttpDocumentFetcher", FakeFetcher), \
             patch("stock_research.tools.extract_text_with_pages", return_value=("正文", ())):
            observation = CompanyTools(project).run("fetch_filings", {"url": "https://a.example.com/x.pdf", "save": True})
        self.assertFalse(observation.ok)
        self.assertIn("无法保存资料", observation.text)

    def test_unknown_tool_returns_error_observation(self) -> None:
        project = _load_project(self.db_path, create=True)
        observation = CompanyTools(project).run("nope", {})
        self.assertFalse(observation.ok)


class AgentLoopTests(unittest.TestCase):
    """循环机制迁移到 agent_core.run_agent_turn 后行为保持不变。"""

    def _explore(self, provider, project, question, tools, max_steps):
        unified = UnifiedTools(tools, None)
        outcome = run_agent_turn(provider, project, question, unified, max_steps=max_steps)
        return {"answer": outcome.answer, "steps": outcome.steps, "citations": outcome.citations}

    def test_loop_collects_observations_and_finalizes(self) -> None:
        project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯控股")
        provider = FakeAgentProvider([
            {"thought": "先查报告", "action": "stub", "args": {"keyword": "revenue"}},
            {"thought": "够了", "action": "final", "answer": "根据 [O1]，收入为 1 亿。"},
        ])
        result = self._explore(provider, project, "收入多少？", StubTools(), max_steps=3)
        self.assertEqual([step["ref"] for step in result["steps"]], ["O1"])
        self.assertIn("1 亿", result["answer"])
        self.assertEqual(result["citations"][0]["observation"], "O1")
        # identity 必须注入 agent 的每一轮 system prompt。
        self.assertTrue(provider.system_prompts)
        self.assertTrue(all("腾讯控股" in prompt for prompt in provider.system_prompts))

    def test_loop_forces_final_when_steps_exhausted(self) -> None:
        project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯控股")
        provider = FakeAgentProvider([
            {"action": "stub", "args": {}},
            {"action": "stub", "args": {}},
            {"action": "final", "answer": "结论见 [O1] [O2]。"},
        ])
        result = self._explore(provider, project, "现在股价多少？", StubTools(), max_steps=2)
        self.assertEqual(len(result["steps"]), 2)
        self.assertIn("结论", result["answer"])
        self.assertIn("立即给出最终回答", provider.user_prompts[1])

    def test_unknown_tool_observation_feeds_back_into_loop(self) -> None:
        project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯控股")
        provider = FakeAgentProvider([
            {"action": "bogus_tool", "args": {}},
            {"action": "final", "answer": "已修正。"},
        ])
        result = self._explore(provider, project, "问题", CompanyTools(project), max_steps=3)
        self.assertIn("未知工具", result["steps"][0]["observation"])

    def test_final_without_answer_raises(self) -> None:
        project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯控股")
        provider = FakeAgentProvider([{"action": "final", "answer": ""}])
        from stock_research.llm import LLMError
        with self.assertRaises(LLMError):
            self._explore(provider, project, "问题", StubTools(), max_steps=2)


class ChatIntegrationTests(unittest.TestCase):
    def test_chat_follow_up_persists_tool_trace_and_citations(self) -> None:
        from stock_research.llm import HeuristicLLMProvider
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            with patch("stock_research.service.resolve_provider", return_value=HeuristicLLMProvider()):
                report = run_research_payload({
                    "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                    "document": "Revenue FY2024 HK$ 100 million",
                }, db_path=f"{directory}/research.sqlite3")
            provider = FakeAgentProvider([
                {"thought": "查报告", "action": "query_report", "args": {"keyword": "revenue"}},
                {"action": "final", "answer": "收入 1 亿港元 [O1]。"},
            ])
            with patch("stock_research.service.resolve_provider", return_value=provider):
                answer = chat_payload(UUID(report["session_id"]), "收入多少？", db_path=f"{directory}/research.sqlite3")
            self.assertEqual(answer["message"], "收入 1 亿港元 [O1]。")
            store = SQLiteStore(f"{directory}/research.sqlite3")
            try:
                messages = store.list_session_messages(UUID(report["session_id"]))
            finally:
                store.close()
            tool_messages = [m for m in messages if m["message_type"] == "tool"]
            self.assertEqual(len(tool_messages), 1)
            self.assertEqual(tool_messages[0]["content"]["tool"], "query_report")
            final = messages[-1]
            self.assertEqual(final["message_type"], "text")
            self.assertEqual(final["content"]["citations"][0]["observation"], "O1")
            self.assertEqual(final["content"]["observation_refs"], ["O1"])

    def test_workspace_activate_retries_after_fetch_save(self) -> None:
        """无资料激活返回 None 且不锁死；fetch_filings(save=true) 登记资料后可再次激活。"""
        from datetime import date as date_cls

        from stock_research.service import ChatResearchWorkspace, create_project_with_session
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            db_path = f"{directory}/research.sqlite3"
            store = SQLiteStore(db_path)
            try:
                created = create_project_with_session({"name": "中国食品", "symbol": "00506"})
                project = store.load_project(UUID(created["project"]["id"]))
                session = store.load_session(UUID(created["session"]["id"]))
                workspace = ChatResearchWorkspace(store, project, session, as_of_date=date_cls(2025, 12, 31), llm_config={})

                class FakeFetcher:
                    def __init__(self, *args, **kwargs) -> None:
                        pass

                    def fetch(self, url: str):
                        from datetime import datetime, timezone

                        from stock_research.documents import FetchedDocument

                        return FetchedDocument(url, "text/html", fake_text.encode("utf-8"), datetime.now(timezone.utc))

                fake_text = (
                    "中国食品 2024 年度业绩公告\n净利润 25.8 亿港元\n收入 280.5 亿港元\n"
                    "经营活动现金流 18.0 亿港元\n资本开支 4.0 亿港元\n现金 60.0 亿港元\n债务 15.0 亿港元\n"
                    "主要风险为市场竞争和原材料成本波动。"
                )
                with patch("stock_research.service.HttpDocumentFetcher", FakeFetcher), \
                     patch("stock_research.tools.HttpDocumentFetcher", FakeFetcher), \
                     patch("stock_research.service.GoogleNewsSearch.search", return_value=[]), \
                     patch("stock_research.service.DuckDuckGoSearch.search", return_value=[]):
                    # 无资料：激活返回 None 且不创建 run、不锁死
                    self.assertIsNone(workspace.activate("研究公司"))
                    self.assertEqual(len(workspace.workflow.runs if workspace.workflow else {}), 0, "无资料激活不得创建 run")
                    # 模拟 fetch_filings(save=true)：登记文档 + 公司来源
                    tools = CompanyTools(project, store=store)
                    observation = tools.fetch_filings({"url": "https://www1.hkexnews.hk/x.pdf", "save": True})
                    self.assertTrue(observation.ok)
                    # 再次激活：发现新登记的资料，成功创建 run
                    with patch("stock_research.service.resolve_provider", return_value=None):
                        research = workspace.activate("研究公司")
                self.assertIsNotNone(research)
                self.assertTrue(workspace._activated)
                self.assertGreaterEqual(len(research.documents), 1)
            finally:
                store.close()

    def test_chat_follow_up_without_report_still_answers_via_tools(self) -> None:
        from stock_research.service import create_project_with_session
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            created = create_project_with_session({"name": "长江和记", "symbol": "00001"})
            provider = FakeAgentProvider([
                {"action": "final", "answer": "还没有研报，建议先启动一次研究。"},
            ])
            with patch("stock_research.service.resolve_provider", return_value=provider):
                answer = chat_payload(UUID(created["session"]["id"]), "现金流怎么看？", db_path=f"{directory}/research.sqlite3")
            self.assertIn("还没有研报", answer["message"])


if __name__ == "__main__":
    unittest.main()
