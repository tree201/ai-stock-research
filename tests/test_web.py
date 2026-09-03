from datetime import date, datetime, timezone
import os
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID

from stock_research.documents import FetchedDocument
from stock_research.llm import HeuristicLLMProvider, ModelNotConfiguredError
from stock_research.web import _research_documents, _web_provider, chat_entry_payload, chat_payload, history_payload, provider_status, run_research_payload
from stock_research.jobs import InlineQueue, create_and_enqueue, execute_research_job
from stock_research.storage import SQLiteStore
from stock_research.web_search import SearchResult


class WebMvpTests(unittest.TestCase):
    def setUp(self) -> None:
        # Existing workflow tests exercise persistence and calculations with a
        # deterministic test double; production entry points still reject a
        # missing real provider (covered explicitly below).
        self._provider_patch = patch("stock_research.web._web_provider", return_value=HeuristicLLMProvider())
        self._provider_patch.start()

    def tearDown(self) -> None:
        self._provider_patch.stop()

    def test_web_provider_requires_a_real_model(self) -> None:
        self._provider_patch.stop()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""}, clear=False):
            with self.assertRaises(ModelNotConfiguredError):
                _web_provider()
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
            first = chat_entry_payload({"name": "腾讯", "symbol": "00700", "content": "你好"}, db_path=f"{directory}/research.sqlite3")
            with patch("stock_research.web.create_and_enqueue", return_value={"job_id": "job-1", "queue_job_id": "rq-1"}):
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
            with self.assertRaises(ValueError):
                chat_payload(UUID(report["session_id"]), "你能研究一下这家公司的基本面吗？", db_path=f"{directory}/research.sqlite3")
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
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""}, clear=False):
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

        with patch("stock_research.web.HttpDocumentFetcher", FakeFetcher):
            documents = _research_documents({"document_urls": ["https://ir.example.com/results.html"]}, UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), date(2025, 12, 31))
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
        class FakeProvider:
            def answer(self, question: str, context: str) -> str:
                self.question = question
                self.context = context
                return "基于报告，净利润率约为 20%。"

        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            report = run_research_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million\nNet income FY2024 HK$ 20 million",
            }, db_path=f"{directory}/research.sqlite3")
            with patch("stock_research.web._web_provider", return_value=FakeProvider()):
                answer = chat_payload(UUID(report["session_id"]), "利润率怎么看？", db_path=f"{directory}/research.sqlite3")
            self.assertEqual(answer["message"], "基于报告，净利润率约为 20%。")

    def test_chat_search_intent_uses_web_search(self) -> None:
        class FakeProvider:
            def __init__(self):
                self.calls: list[str] = []

            def answer(self, question: str, context: str = "") -> str:
                self.calls.append("answer")
                return "report answer"

            def answer_with_search(self, question: str, search_results, report_context: str = "") -> str:
                self.calls.append("answer_with_search")
                return "search answer"

        class FakeSearch:
            def search(self, query: str, max_results: int = 5):
                return [SearchResult(title="t", url="https://example.com", snippet="s")]

        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ):
            report = run_research_payload({
                "name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司",
                "document": "Revenue FY2024 HK$ 100 million",
            }, db_path=f"{directory}/research.sqlite3")
            provider = FakeProvider()
            with patch("stock_research.web._web_provider", return_value=provider), \
                 patch("stock_research.web.DuckDuckGoSearch", FakeSearch):
                answer = chat_payload(UUID(report["session_id"]), "腾讯最新新闻是什么？", db_path=f"{directory}/research.sqlite3")
            self.assertEqual(answer["message"], "search answer")
            self.assertEqual(provider.calls, ["answer_with_search"])

    def test_chat_search_falls_back_to_report_when_no_results(self) -> None:
        class FakeProvider:
            def answer(self, question: str, context: str = "") -> str:
                return "report answer"

            def answer_with_search(self, question: str, search_results, report_context: str = "") -> str:
                return "search answer"

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
            with patch("stock_research.web._web_provider", return_value=FakeProvider()), \
                 patch("stock_research.web.DuckDuckGoSearch", EmptySearch), \
                 patch("stock_research.web.GoogleNewsSearch", EmptySearch):
                answer = chat_payload(UUID(report["session_id"]), "腾讯最新新闻", db_path=f"{directory}/research.sqlite3")
            self.assertEqual(answer["message"], "report answer")

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
