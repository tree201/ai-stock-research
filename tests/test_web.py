import base64
import os
from datetime import date, datetime, timezone
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID

import urllib.error
import urllib.request

from stock_research.documents import FetchedDocument
from stock_research.domain import SessionMessage
from stock_research.llm import HeuristicLLMProvider, ModelNotConfiguredError
from stock_research.market_data import PriceBar
from stock_research.service import add_company_source, chat_entry_payload, chat_payload, create_project_with_session, discover_documents, history_payload, provider_status, recalculate_report, remove_company, remove_company_source, research_documents, resolve_provider, run_research_payload, run_update_payload
from stock_research.jobs import InlineQueue, create_and_enqueue, execute_research_job
from stock_research.storage import SQLiteStore
from stock_research.web_search import SearchResult


class WebMvpTests(unittest.TestCase):
    def setUp(self) -> None:
        # Existing workflow tests exercise persistence and calculations with a
        # deterministic test double; production entry points still reject a
        # missing real provider (covered explicitly below).
        self._provider_patch = patch("stock_research.service.resolve_provider", return_value=HeuristicLLMProvider())
        self._provider_patch.start()
        # Safety net: any service call that falls back to the default database
        # path must never touch the developer's real ./research.sqlite3.
        # Patching the env (not the function) keeps tests that mix an explicit
        # db_path with default-path calls pointed at the same isolated store.
        self._isolation_dir = TemporaryDirectory()
        self.addCleanup(self._isolation_dir.cleanup)
        self._db_env_patch = patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{self._isolation_dir.name}/research.sqlite3"},
        )
        self._db_env_patch.start()
        self.addCleanup(self._db_env_patch.stop)

    def tearDown(self) -> None:
        self._provider_patch.stop()

    def test_web_provider_requires_a_real_model(self) -> None:
        self._provider_patch.stop()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""}, clear=False):
            with self.assertRaises(ModelNotConfiguredError):
                resolve_provider()
    def test_inline_queue_completes_and_persists_job(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            first = chat_entry_payload({"name": "腾讯", "symbol": "00700", "content": "你好"}, db_path=f"{directory}/research.sqlite3")
            session_id = UUID(first["session_id"])
            store = SQLiteStore(f"{directory}/research.sqlite3")
            project = store.load_project(store.load_session(session_id).project_id)
            job = create_and_enqueue(store, session_id, {
                "name": project.name, "symbol": project.symbol, "as_of_date": "2025-12-31",
                "question": "研究公司", "_session_message_saved": False,
                "document": "Revenue FY2024 HK$ 100 million",
            }, f"{directory}/research.sqlite3", queue=InlineQueue())
            loaded = store.load_job(UUID(job["job_id"]))
            self.assertEqual(loaded["status"], "completed")
            self.assertTrue(loaded["run_id"])
            store.close()

    def test_chat_returns_queued_job_in_rq_mode_with_stub(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "AI_STOCK_QUEUE": "rq", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            with patch("stock_research.service.create_and_enqueue", return_value={"job_id": "job-0", "queue_job_id": "rq-0"}):
                first = chat_entry_payload({"name": "腾讯", "symbol": "00700", "content": "你好"}, db_path=f"{directory}/research.sqlite3")
            with patch("stock_research.service.create_and_enqueue", return_value={"job_id": "job-1", "queue_job_id": "rq-1"}):
                result = chat_payload(UUID(first["session_id"]), "研究这家公司", db_path=f"{directory}/research.sqlite3")
            self.assertEqual(result["type"], "research_queued")
            self.assertEqual(result["job_id"], "job-1")

    def test_inline_research_chat_persists_user_message(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            report = run_research_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million",
            }, db_path=f"{directory}/research.sqlite3")
            # 统一循环下"研究"字样不再特判：无 chat_json 的 provider 走 canned 应答，
            # 但用户消息必须持久化。
            result = chat_payload(UUID(report["session_id"]), "你能研究一下这家公司的基本面吗？", db_path=f"{directory}/research.sqlite3")
            self.assertEqual(result["type"], "answer")
            messages = history_payload(f"/api/sessions/{report['session_id']}")["messages"]
            user_texts = [message["content"]["text"] for message in messages if message["role"] == "user"]
            self.assertIn("你能研究一下这家公司的基本面吗？", user_texts)

    def test_failed_job_is_persisted(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            first = chat_entry_payload({"name": "腾讯", "symbol": "00700", "content": "你好"}, db_path=f"{directory}/research.sqlite3")
            session_id = UUID(first["session_id"])
            store = SQLiteStore(f"{directory}/research.sqlite3")
            job_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            store.create_job(job_id, session_id, {"name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": ""})
            store.close()
            with self.assertRaises(ValueError):
                execute_research_job(str(job_id), f"{directory}/research.sqlite3")
            reopened = SQLiteStore(f"{directory}/research.sqlite3")
            failed = reopened.load_job(job_id)
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["error"]["type"], "ValueError")
            reopened.close()

    def test_research_payload_returns_structured_report(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""}, clear=False):
            report = run_research_payload({
                "name": "腾讯（示例）",
                "symbol": "00700",
                "as_of_date": "2025-12-31",
                "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million\nNet income FY2024 HK$ 20 million\nRisk: competition",
            })
        self.assertEqual(report["company"]["symbol"], "00700")
        self.assertTrue(report["markdown"])
        self.assertEqual(report["review"]["status"], "passed")

    def test_research_payload_requires_core_fields(self) -> None:
        with self.assertRaises(ValueError):
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""}, clear=False):
                run_research_payload({"name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31"})

    def test_public_document_url_is_fetched_and_preserves_source(self) -> None:
        class FakeFetcher:
            def __init__(self, **_kwargs):
                pass

            def fetch(self, url: str) -> FetchedDocument:
                return FetchedDocument(url, "text/html", b"<p>Revenue FY2024 HK$ 100 million</p>", datetime.now(timezone.utc))

        with patch("stock_research.service.HttpDocumentFetcher", FakeFetcher):
            documents = research_documents({"document_urls": ["https://ir.example.com/results.html"]}, UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), date(2025, 12, 31))
        self.assertEqual(documents[0].source_type, "company_ir")
        self.assertEqual(documents[0].source_url, "https://ir.example.com/results.html")
        self.assertIn("Revenue FY2024", documents[0].content)

    def test_web_reports_model_status_without_secret(self) -> None:
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""}, clear=False):
            status = provider_status()
        self.assertIn("llm_enabled", status)
        self.assertIn("provider", status)
        self.assertFalse(status["llm_enabled"])
        self.assertFalse(status["api_key_configured"])
        self.assertNotEqual(status["provider"], "heuristic")

    def test_research_artifacts_and_report_survive_store_reopen(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            report = run_research_payload({
                "name": "腾讯（示例）", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million\nNet income FY2024 HK$ 20 million\nRisk: competition",
            })
            projects = history_payload("/api/projects")
            runs = history_payload("/api/runs")
            self.assertEqual(len(projects), 1)
            self.assertEqual(len(runs), 1)
            restored = history_payload(f"/api/runs/{report['run_id']}")
            self.assertEqual(restored["run"]["status"], "completed")
            self.assertEqual(len(restored["artifacts"]["documents"]), 1)
            self.assertEqual(len(restored["artifacts"]["evidence"]), 1)
            self.assertGreaterEqual(len(restored["artifacts"]["facts"]), 2)
            self.assertEqual(restored["artifacts"]["reports"][0]["id"], report["report_id"])
            self.assertEqual(history_payload(f"/api/reports/{report['report_id']}")["report_id"], report["report_id"])

    def test_recalculate_report_reuses_facts_and_creates_new_version(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            report = run_research_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": (
                    "Revenue FY2024 HK$ 100 million\n"
                    "Net cash generated from operating activities FY2024 HK$ 80 million\n"
                    "Capital expenditure FY2024 HK$ 30 million\n"
                    "Risk: competition remains intense"
                ),
            }, db_path=f"{directory}/research.sqlite3")
            self.assertTrue(any(item["calculation_type"] == "dcf" for item in report["calculations"]))
            original_base = next(item for item in report["calculations"] if item["calculation_type"] == "dcf" and item["inputs"]["scenario"] == "base")

            updated = recalculate_report(
                report["report_id"],
                {"growth_rates": [0.08, 0.06], "discount_rate": 0.15, "terminal_growth": 0.02, "shares": 9_000_000_000},
                db_path=f"{directory}/research.sqlite3",
            )
            self.assertNotEqual(updated["report_id"], report["report_id"])
            self.assertEqual(updated["recalculated_from"], report["report_id"])
            self.assertEqual(updated["version"], 2)
            self.assertEqual(len(updated["facts"]), len(report["facts"]))
            self.assertEqual(updated["llm_claims"], report["llm_claims"])
            new_base = next(item for item in updated["calculations"] if item["calculation_type"] == "dcf" and item["inputs"]["scenario"] == "base")
            self.assertEqual(new_base["inputs"]["discount_rate"], 0.15)
            self.assertNotEqual(new_base["outputs"]["value_per_share"], original_base["outputs"]["value_per_share"])
            self.assertEqual(updated["company"]["symbol"], "00700")
            self.assertTrue(updated["markdown"])

            reopened = SQLiteStore(f"{directory}/research.sqlite3")
            try:
                artifacts = reopened.load_run_artifacts(UUID(report["run_id"]))
                self.assertEqual(len(artifacts["reports"]), 2)
                self.assertEqual({row["version"] for row in artifacts["reports"]}, {1, 2})
                event_types = [event.event_type for event in reopened.events_for_run(UUID(report["run_id"]))]
                self.assertIn("report/recalculated", event_types)
                messages = reopened.list_session_messages(UUID(report["session_id"]))
                self.assertTrue(any(message["content"].get("report_id") == updated["report_id"] for message in messages))
            finally:
                reopened.close()

            # The original report stays intact and loadable.
            restored = history_payload(f"/api/reports/{report['report_id']}")
            self.assertEqual(restored["report_id"], report["report_id"])

    def test_recalculate_report_validates_assumptions(self) -> None:
        with self.assertRaises(ValueError):
            recalculate_report("00000000-0000-0000-0000-000000000000", {"unknown_key": 1.0})
        with self.assertRaises(ValueError):
            recalculate_report("00000000-0000-0000-0000-000000000000", {"growth_rates": "fast"})
        with self.assertRaises(ValueError):
            recalculate_report("00000000-0000-0000-0000-000000000000", {"discount_rate": "0.1"})

    def test_update_flow_detects_new_documents_and_diffs(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            initial = run_research_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million\nNet income FY2024 HK$ 20 million",
            }, db_path=f"{directory}/research.sqlite3")

            # Same content again: nothing new to ingest (discovery finds nothing offline).
            with patch("stock_research.service.GoogleNewsSearch", DiscoveryTests._search_stub([])), \
                    patch("stock_research.service.DuckDuckGoSearch", DiscoveryTests._search_stub([])):
                with self.assertRaisesRegex(ValueError, "未检测到新资料"):
                    run_research_payload({
                        "name": "腾讯", "symbol": "00700", "as_of_date": "2026-06-30", "question": "更新研究",
                        "document": "Revenue FY2024 HK$ 100 million\nNet income FY2024 HK$ 20 million",
                        "mode": "update",
                    }, db_path=f"{directory}/research.sqlite3", session_id=UUID(initial["session_id"]))

            updated = run_update_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2026-06-30", "question": "更新研究",
                "document": "Revenue FY2025 HK$ 120 million\nNet income FY2025 HK$ 30 million\nRisk: regulatory pressure",
            }, db_path=f"{directory}/research.sqlite3", session_id=UUID(initial["session_id"]))
            self.assertEqual(updated["update_of"], initial["report_id"])
            new_metrics = {(fact["metric"], fact.get("period_end")) for fact in updated["diff"]["new_facts"]}
            self.assertIn(("revenue", "2025-12-31"), new_metrics)
            self.assertIn(("net_income", "2025-12-31"), new_metrics)
            self.assertEqual(updated["diff"]["changed_facts"], [])
            self.assertIsNone(updated["diff"]["valuation"])
            self.assertTrue(updated["diff"]["conclusion_changed"])
            self.assertIn("与上一版差异", updated["markdown"])
            run_detail = history_payload(f"/api/runs/{updated['run_id']}")
            self.assertEqual(run_detail["run"]["run_type"], "update")

            # The initial report is untouched.
            self.assertNotIn("diff", history_payload(f"/api/reports/{initial['report_id']}"))

    def test_update_requires_existing_research_history(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "请先完成一次初始研究"):
                run_update_payload({
                    "name": "新公司", "symbol": "09999", "as_of_date": "2026-06-30", "question": "更新研究",
                    "document": "Revenue FY2025 HK$ 10 million",
                }, db_path=f"{directory}/research.sqlite3")

    def test_chat_update_intent_runs_incremental_update(self) -> None:
        """更新研究不再由聊天关键词触发：直接走 run_update_payload 服务入口。"""
        contents = {"body": "<p>Revenue FY2024 HK$ 100 million</p>"}

        class FakeFetcher:
            def __init__(self, **_kwargs):
                pass

            def fetch(self, url: str) -> FetchedDocument:
                return FetchedDocument(url, "text/html", contents["body"].encode("utf-8"), datetime.now(timezone.utc))

        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            create_project_with_session({"name": "腾讯", "symbol": "00700"})
            add_company_source("00700", "HK", "https://ir.example.com/results.html")
            with patch("stock_research.service.HttpDocumentFetcher", FakeFetcher):
                initial = run_research_payload({
                    "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                }, db_path=f"{directory}/research.sqlite3")
            contents["body"] = "<p>Revenue FY2025 HK$ 120 million</p>"
            with patch("stock_research.service.HttpDocumentFetcher", FakeFetcher):
                report = run_update_payload({
                    "name": "腾讯", "symbol": "00700", "as_of_date": "2026-06-30", "question": "更新研究",
                    "_session_message_saved": True, "session_id": initial["session_id"],
                }, db_path=f"{directory}/research.sqlite3", session_id=UUID(initial["session_id"]))
            self.assertTrue(report["diff"]["new_facts"])
            self.assertEqual(report["update_of"], initial["report_id"])

    def test_repeated_symbol_reuses_project_but_creates_new_run(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            payload = {"name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究", "document": "Revenue FY2024 HK$ 100 million"}
            first = run_research_payload(payload)
            payload["question"] = "更新风险"
            second = run_research_payload(payload)
            self.assertNotEqual(first["run_id"], second["run_id"])
            self.assertEqual(len(history_payload("/api/projects")), 1)
            self.assertEqual(len(history_payload("/api/runs")), 2)

    def test_chat_message_uses_company_session_and_persists_messages(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            report = run_research_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million",
            })
            answer = chat_payload(UUID(report["session_id"]), "现金流怎么看？", db_path=f"{directory}/research.sqlite3")
            self.assertEqual(answer["type"], "answer")
            session = history_payload(f"/api/sessions/{report['session_id']}")
            self.assertGreaterEqual(len(session["messages"]), 3)
            self.assertEqual(session["session"]["project_id"], history_payload("/api/companies")[0]["id"])

    def test_chat_follow_up_uses_configured_provider_answer(self) -> None:
        """追问经统一循环：chat_json provider 按脚本调用 query_report 后回答。"""

        class ScriptedProvider:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def chat_json(self, system: str, user: str) -> dict:
                self.calls.append("chat_json")
                if len(self.calls) == 1:
                    return {"thought": "查报告", "action": "query_report", "args": {"keyword": "Net income"}}
                return {"thought": "够了", "action": "final", "answer": "基于报告，净利润率约为 20%。[O1]"}

        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            report = run_research_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million\nNet income FY2024 HK$ 20 million",
            }, db_path=f"{directory}/research.sqlite3")
            provider = ScriptedProvider()
            with patch("stock_research.service.resolve_provider", return_value=provider):
                answer = chat_payload(UUID(report["session_id"]), "利润率怎么看？", db_path=f"{directory}/research.sqlite3")
            self.assertEqual(provider.calls, ["chat_json", "chat_json"])
            self.assertIn("20%", answer["message"])
            self.assertNotIn("run_id", answer, "纯探索回答不应携带 run_id")

    def test_chat_follow_up_skips_cross_company_report_context(self) -> None:
        """query_report 按项目隔离：长和会话里查报告绝不会看到腾讯数据。"""

        class ScriptedProvider:
            def __init__(self) -> None:
                self.prompts: list[str] = []
                self.company_names: list[str] = []

            def chat_json(self, system: str, user: str) -> dict:
                self.prompts.append(user)
                self.company_names.append("长江和记" if "长江和记" in system else "其他")
                if len(self.prompts) == 1:
                    return {"thought": "查报告", "action": "query_report", "args": {"keyword": "Revenue"}}
                return {"thought": "够了", "action": "final", "answer": "ok"}

        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            run_research_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million",
            }, db_path=f"{directory}/research.sqlite3")
            ckh = run_research_payload({
                "name": "长江和记", "symbol": "00001", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 200 million",
            }, db_path=f"{directory}/research.sqlite3")
            provider = ScriptedProvider()
            with patch("stock_research.service.resolve_provider", return_value=provider):
                chat_payload(UUID(ckh["session_id"]), "现金流怎么看？", db_path=f"{directory}/research.sqlite3")
            # identity 注入会话公司；观察只能来自长和自己的报告。
            self.assertTrue(provider.company_names)
            self.assertTrue(all(name == "长江和记" for name in provider.company_names))
            transcript = "\n".join(provider.prompts)
            self.assertIn("200", transcript)
            self.assertNotIn("100 million", transcript)

    def test_chat_search_intent_uses_web_search(self) -> None:
        """搜索意图由模型经 search_news 工具发起，不再靠关键词判别。"""

        class ScriptedProvider:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def chat_json(self, system: str, user: str) -> dict:
                self.calls.append("chat_json")
                if "已完成" not in user:
                    return {"thought": "搜新闻", "action": "search_news", "args": {"query": "最新"}}
                return {"thought": "够了", "action": "final", "answer": "search answer"}

        class FakeSearch:
            def search(self, query: str, max_results: int = 5):
                self.query = query
                return [SearchResult(title="t", url="https://example.com", snippet="s")]

        fake_search = FakeSearch()
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            report = run_research_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million",
            }, db_path=f"{directory}/research.sqlite3")
            provider = ScriptedProvider()
            with patch("stock_research.service.resolve_provider", return_value=provider), \
                 patch("stock_research.tools.GoogleNewsSearch", return_value=fake_search):
                answer = chat_payload(UUID(report["session_id"]), "腾讯最新新闻是什么？", db_path=f"{directory}/research.sqlite3")
            self.assertEqual(answer["message"], "search answer")
            self.assertIn("腾讯", fake_search.query)

    def test_chat_search_empty_results_feeds_back_to_model(self) -> None:
        """搜索无结果时观察回灌循环，模型据实回答而不是编造。"""

        class ScriptedProvider:
            def __init__(self) -> None:
                self.saw_empty_note = False

            def chat_json(self, system: str, user: str) -> dict:
                if "没有搜到" in user:
                    self.saw_empty_note = True
                    return {"thought": "据实回答", "action": "final", "answer": "没有找到相关新闻。"}
                return {"thought": "搜新闻", "action": "search_news", "args": {"query": "最新"}}

        class EmptySearch:
            def search(self, query: str, max_results: int = 5):
                return []

        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            report = run_research_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million",
            }, db_path=f"{directory}/research.sqlite3")
            provider = ScriptedProvider()
            with patch("stock_research.service.resolve_provider", return_value=provider), \
                 patch("stock_research.tools.GoogleNewsSearch", return_value=EmptySearch()), \
                 patch("stock_research.tools.DuckDuckGoSearch", return_value=EmptySearch()):
                answer = chat_payload(UUID(report["session_id"]), "腾讯最新新闻", db_path=f"{directory}/research.sqlite3")
            self.assertEqual(answer["message"], "没有找到相关新闻。")
            self.assertTrue(provider.saw_empty_note)

    def test_first_chat_message_creates_session_without_forcing_research(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            answer = chat_entry_payload({"name": "腾讯", "symbol": "00700", "content": "你好"}, db_path=f"{directory}/research.sqlite3")
            self.assertEqual(answer["type"], "answer")
            companies = history_payload("/api/companies")
            self.assertEqual(len(companies), 1)
            self.assertEqual(companies[0]["session_count"], 1)

    def test_remove_company_deletes_all_research_data(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            db_path = f"{directory}/research.sqlite3"
            report = run_research_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million\nRisk: competition",
            }, db_path=db_path)
            add_company_source("00700", "HK", "https://ir.example.com/results.html")

            store = SQLiteStore(db_path)
            company_id = store.load_project(store.find_project("00700").id).company_id
            self.assertGreaterEqual(len(store.list_project_reports(store.find_project("00700").id)), 1)
            store.close()

            result = remove_company("00700", "HK", db_path=db_path)
            self.assertTrue(result["ok"])
            self.assertGreaterEqual(result["deleted"].get("reports", 0), 1)
            self.assertGreaterEqual(result["deleted"].get("projects", 1), 1)

            reopened = SQLiteStore(db_path)
            try:
                self.assertEqual(reopened.find_project("00700"), None)
                for table in ("projects", "runs", "reports", "documents", "evidence_chunks", "sessions", "session_messages", "jobs", "company_sources"):
                    count = reopened.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    self.assertEqual(count, 0, f"{table} should be empty after company removal")
                self.assertEqual(reopened.list_company_source_urls(company_id), [])
            finally:
                reopened.close()

            self.assertEqual([c for c in history_payload("/api/companies") if c["symbol"] == "00700"], [])

            with self.assertRaisesRegex(ValueError, "company not found"):
                remove_company("00700", "HK", db_path=db_path)

    def test_delete_http_route_removes_company(self) -> None:
        """DELETE /api/companies/{symbol}?market=HK 必须真的接线到 remove_company。

        回归背景：service 层早已实现级联删除，但 web.py 的 do_DELETE 没有该
        路由，前端「移除该公司」实际收到 404。此测试从 HTTP 层验证。
        """
        import json as _json
        import threading
        from http.server import ThreadingHTTPServer
        from stock_research.web import ResearchRequestHandler

        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            db_path = f"{directory}/research.sqlite3"
            run_research_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million\nRisk: competition",
            }, db_path=db_path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), ResearchRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                request = urllib.request.Request(f"{base}/api/companies/00700?market=HK", method="DELETE")
                with urllib.request.urlopen(request, timeout=10) as response:
                    body = _json.loads(response.read().decode("utf-8"))
                self.assertTrue(body["ok"])
                self.assertGreaterEqual(body["deleted"].get("projects", 0), 1)

                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(urllib.request.Request(f"{base}/api/companies/00700?market=HK", method="DELETE"), timeout=10)
                self.assertEqual(caught.exception.code, 400)  # company not found → 400
            finally:
                server.shutdown()
                server.server_close()
                reopened = SQLiteStore(db_path)
                try:
                    self.assertEqual(reopened.find_project("00700"), None)
                finally:
                    reopened.close()

    def test_company_panel_returns_project_documents_and_reports(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            run_research_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million",
            }, db_path=f"{directory}/research.sqlite3")
            panel = history_payload("/api/company-panel?symbol=00700&market=HK")
            self.assertTrue(panel["available"])
            self.assertEqual(panel["project"]["symbol"], "00700")
            self.assertGreaterEqual(len(panel["documents"]), 1)
            self.assertGreaterEqual(len(panel["reports"]), 1)
            self.assertTrue(panel["reports"][0]["id"])

    def test_quote_endpoint_returns_delayed_snapshot(self) -> None:
        now = datetime.now(timezone.utc)
        bars = [
            PriceBar("0700.HK", date(2026, 9, 1), 100.0, 110.0, 99.0, 105.0, 1000, "yahoo_chart", now),
            PriceBar("0700.HK", date(2026, 9, 2), 105.0, 112.0, 104.0, 110.0, 1200, "yahoo_chart", now),
        ]

        class FakeProvider:
            def __init__(self):
                self.symbol = ""

            def get_daily_bars(self, symbol, start, end):
                self.symbol = symbol
                return bars

        fake = FakeProvider()
        with patch("stock_research.service.YahooFinanceProvider", return_value=fake):
            quote = history_payload("/api/quote/00700?market=HK")
        self.assertTrue(quote["available"])
        self.assertEqual(fake.symbol, "0700.HK")
        self.assertEqual(quote["last"], 110.0)
        self.assertEqual(quote["change"], 5.0)
        self.assertEqual(quote["high_52w"], 112.0)
        self.assertTrue(quote["delayed"])

    def test_quote_endpoint_degrades_without_provider(self) -> None:
        from stock_research.market_data import MarketDataError

        class FailingProvider:
            def get_daily_bars(self, symbol, start, end):
                raise MarketDataError("down")

        with patch("stock_research.service.YahooFinanceProvider", FailingProvider):
            quote = history_payload("/api/quote/00700?market=HK")
        self.assertFalse(quote["available"])
        self.assertTrue(quote["delayed"])

    def test_news_endpoint_uses_keyless_search(self) -> None:
        class FakeNews:
            def __init__(self):
                self.queries: list[str] = []

            def search(self, query, max_results=5):
                self.queries.append(query)
                return [
                    SearchResult(
                        title="t", url="https://example.com", snippet="s",
                        published="Thu, 03 Sep 2026 21:15:00 GMT",
                    ),
                    SearchResult(
                        title="g", url="https://news.google.com/rss/articles/CBMiU0FVX3lxTE9YU3I4M0w2WnNYSUNNLWZCRENHY1ct?oc=5", snippet="s",
                    ),
                ]

        fake = FakeNews()
        with patch("stock_research.service.GoogleNewsSearch", return_value=fake):
            news = history_payload("/api/news?name=CKH%20HOLDINGS&symbol=00001")
        self.assertEqual(news[0]["url"], "https://example.com")
        self.assertEqual(news[0]["time"], "Thu, 03 Sep 2026 21:15:00 GMT")
        self.assertFalse(news[0]["external"])
        self.assertTrue(news[1]["external"])
        self.assertIn("news.google.com", news[1]["url"])
        self.assertEqual(fake.queries, ["CKH HOLDINGS 最新", "CKH HOLDINGS"])

    def test_news_default_merges_and_dedupes_queries(self) -> None:
        class FakeNews:
            def search(self, query, max_results=5):
                if "最新" in query:
                    return [
                        SearchResult(title="a", url="https://example.com/a", snippet="s"),
                    ]
                return [
                    SearchResult(title="b", url="https://example.com/b", snippet="s"),
                    SearchResult(title="a-duplicate", url="https://example.com/a", snippet="s"),
                ]

        with patch("stock_research.service.GoogleNewsSearch", return_value=FakeNews()):
            news = history_payload("/api/news?name=长江和记&symbol=00001")
        self.assertEqual([item["url"] for item in news], ["https://example.com/a", "https://example.com/b"])

    def test_news_search_stays_scoped_to_company(self) -> None:
        class FakeNews:
            def __init__(self):
                self.queries: list[str] = []

            def search(self, query, max_results=5):
                self.queries.append(query)
                if query == "长江和记 巴拿马":
                    return [SearchResult(title="scoped", url="https://example.com/scoped", snippet="s")]
                return []

        fake = FakeNews()
        with patch("stock_research.service.GoogleNewsSearch", return_value=fake):
            news = history_payload("/api/news?name=长江和记&symbol=00001&q=%E5%B7%B4%E6%8B%BF%E9%A9%AC")
        # Exactly one company-scoped query — no raw-keyword fallback.
        self.assertEqual(fake.queries, ["长江和记 巴拿马"])
        self.assertEqual(news[0]["url"], "https://example.com/scoped")

    def test_news_search_without_company_hits_returns_empty(self) -> None:
        class FakeNews:
            def __init__(self):
                self.queries: list[str] = []

            def search(self, query, max_results=5):
                self.queries.append(query)
                return []  # nothing matches even with the company name

        fake = FakeNews()
        with patch("stock_research.service.GoogleNewsSearch", return_value=fake):
            news = history_payload("/api/news?name=长江和记&symbol=00001&q=%E6%9D%8E%E5%AE%B6")
        # Raw "李家" must never be searched on its own.
        self.assertEqual(fake.queries, ["长江和记 李家"])
        self.assertEqual(news, [])

    def test_article_endpoint_extracts_readable_text(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return (
                    "<html><body><h1>标题</h1><p>长江和记最新正文内容。</p></body></html>"
                ).encode("utf-8")

            def get_header(self, name, _default=None):
                return "text/html" if name == "Content-Type" else None

            def geturl(self):
                return "https://example.com/news/1"

        fake_response = FakeResponse()
        fake_response.headers = {"Content-Type": "text/html"}

        with patch("stock_research.service.urlopen", return_value=fake_response):
            article = history_payload("/api/article?url=https%3A%2F%2Fexample.com%2Fnews%2F1")
        self.assertTrue(article["ok"])
        self.assertIn("长江和记最新正文内容", article["text"])

    def test_article_endpoint_blocks_private_hosts(self) -> None:
        with self.assertRaises(ValueError):
            history_payload("/api/article?url=http%3A%2F%2F127.0.0.1%2Fsecret")
        with self.assertRaises(ValueError):
            history_payload("/api/article?url=http%3A%2F%2F192.168.1.5%2Fsecret")

    def test_resolve_google_news_old_format_id(self) -> None:
        from stock_research.service import resolve_google_news_url

        direct = "https://finance.sina.com.cn/news/2026-09-01/doc.shtml"
        encoded = base64.urlsafe_b64encode(b"\x08\x13\x32\x10" + direct.encode()).decode().rstrip("=")
        resolved = resolve_google_news_url(f"https://news.google.com/rss/articles/{encoded}?oc=5")
        self.assertEqual(resolved, direct)
        new_format = "https://news.google.com/rss/articles/CBMiU0FVX3lxTE9YU3I4M0w2WnNYSUNNLWZCRENHY1ct?oc=5"
        self.assertEqual(resolve_google_news_url(new_format), new_format)
        self.assertEqual(resolve_google_news_url("https://example.com/a"), "https://example.com/a")

    def test_company_sources_persist_and_feed_research(self) -> None:
        class FakeFetcher:
            def __init__(self, **_kwargs):
                pass

            def fetch(self, url: str) -> FetchedDocument:
                return FetchedDocument(url, "text/html", b"<p>Revenue FY2024 HK$ 100 million</p>", datetime.now(timezone.utc))

        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            chat_entry_payload({"name": "腾讯", "symbol": "00700", "content": "你好"}, db_path=f"{directory}/research.sqlite3")
            added = add_company_source("00700", "HK", "https://ir.example.com/results.html")
            duplicate = add_company_source("00700", "HK", "https://ir.example.com/results.html")
            self.assertEqual(added["source"]["id"], duplicate["source"]["id"])
            panel = history_payload("/api/company-panel?symbol=00700&market=HK")
            self.assertEqual(len(panel["sources"]), 1)
            with patch("stock_research.service.HttpDocumentFetcher", FakeFetcher):
                run_research_payload({
                    "name": "腾讯", "symbol": "00700", "as_of_date": date.today().isoformat(), "question": "研究公司",
                }, db_path=f"{directory}/research.sqlite3")
            panel_after = history_payload("/api/company-panel?symbol=00700&market=HK")
            self.assertTrue(
                any(doc["source_url"] == "https://ir.example.com/results.html" for doc in panel_after["documents"]),
            )
            remove_company_source(added["source"]["id"])
            panel_removed = history_payload("/api/company-panel?symbol=00700&market=HK")
            self.assertEqual(len(panel_removed["sources"]), 0)


class DiscoveryTests(unittest.TestCase):
    """Agent 自动找资料：无登记来源时搜索网络并按可信度取舍。"""

    def setUp(self) -> None:
        self._provider_patch = patch("stock_research.service.resolve_provider", return_value=HeuristicLLMProvider())
        self._provider_patch.start()
        self.addCleanup(self._provider_patch.stop)

    class FakeFetcher:
        def __init__(self, **_kwargs):
            pass

        def fetch(self, url: str) -> FetchedDocument:
            return FetchedDocument(url, "text/html", f"<p>Revenue FY2024 HK$ 100 million ({url})</p>".encode(), datetime.now(timezone.utc))

    @staticmethod
    def _search_stub(results):
        class Stub:
            def __init__(self, **_kwargs):
                pass

            def search(self, query: str, max_results: int = 5):
                return results
        return Stub

    def test_discover_prefers_whitelist_and_annotates_trust(self) -> None:
        from stock_research.domain import ResearchProject
        from uuid import uuid4
        results = [
            SearchResult(title="新闻转载", url="https://random.news.com/report", snippet="..."),
            SearchResult(title="港交所公告", url="https://www1.hkexnews.hk/listedco/listconews/a.pdf", snippet="..."),
        ]
        project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯")
        with TemporaryDirectory() as directory:
            store = SQLiteStore(f"{directory}/research.sqlite3")
            try:
                with patch("stock_research.service.GoogleNewsSearch", self._search_stub(results)), \
                        patch("stock_research.service.HttpDocumentFetcher", self.FakeFetcher):
                    documents = discover_documents(project, project.company_id, date(2025, 12, 31), store=store)
            finally:
                store.close()
        self.assertEqual(documents[0].source_url, "https://www1.hkexnews.hk/listedco/listconews/a.pdf")
        self.assertEqual(documents[0].trust, "whitelist")
        self.assertEqual(documents[1].trust, "unverified")

    def test_research_runs_on_discovered_materials_without_registered_sources(self) -> None:
        results = [
            SearchResult(title="业绩公告", url="https://ir.example.com/annual-results.html", snippet="..."),
        ]
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            with patch("stock_research.service.GoogleNewsSearch", self._search_stub(results)), \
                    patch("stock_research.service.DuckDuckGoSearch", self._search_stub([])), \
                    patch("stock_research.service.HttpDocumentFetcher", self.FakeFetcher):
                report = run_research_payload({
                    "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                }, db_path=f"{directory}/research.sqlite3")
            self.assertEqual(report["company"]["symbol"], "00700")
            self.assertTrue(report["markdown"])
            # 发现的来源自动登记，供后续增量更新复用
            store = SQLiteStore(f"{directory}/research.sqlite3")
            try:
                project = store.find_project("00700")
                urls = store.list_company_source_urls(project.company_id)
            finally:
                store.close()
            self.assertIn("https://ir.example.com/annual-results.html", urls)

    def test_research_raises_actionable_error_when_discovery_finds_nothing(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            with patch("stock_research.service.GoogleNewsSearch", self._search_stub([])), \
                    patch("stock_research.service.DuckDuckGoSearch", self._search_stub([])):
                with self.assertRaises(ValueError) as ctx:
                    run_research_payload({
                        "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                    }, db_path=f"{directory}/research.sqlite3")
            self.assertIn("自动检索未找到可用资料", str(ctx.exception))

    def test_message_stream_endpoint_emits_ndjson_frames(self) -> None:
        """流式端点：NDJSON 逐帧响应，最后一帧 done 携带完整聊天结果。"""
        import json as json_module
        import threading
        from http.server import ThreadingHTTPServer

        from stock_research.web import ResearchRequestHandler

        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3"},
            clear=False,
        ), patch("stock_research.service.resolve_provider", return_value=HeuristicLLMProvider()):
            db_path = os.environ["AI_STOCK_DB"]
            first = chat_entry_payload({"name": "腾讯", "symbol": "00700", "content": "你好"}, db_path=db_path)
            session_id = first["session_id"]

            server = ThreadingHTTPServer(("127.0.0.1", 0), ResearchRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/api/sessions/{session_id}/messages/stream"
                request = urllib.request.Request(
                    url,
                    data=json_module.dumps({"content": "最新股价"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    self.assertEqual(response.headers.get("Content-Type"), "application/x-ndjson; charset=utf-8")
                    lines = [line for line in response.read().decode("utf-8").splitlines() if line.strip()]
                frames = [json_module.loads(line) for line in lines]
                self.assertGreaterEqual(len(frames), 1)
                self.assertEqual(frames[-1]["event"], "done")
                self.assertEqual(frames[-1]["data"]["type"], "answer")
                self.assertTrue(frames[-1]["data"]["message"])
            finally:
                server.shutdown()
                server.server_close()
