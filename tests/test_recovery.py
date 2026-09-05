"""Startup job recovery and checkpoint resume tests (Phase 4 slice)."""

from datetime import date
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import patch
from uuid import uuid4, UUID

from stock_research.documents import DocumentIngestor, RawDocument
from stock_research.domain import ResearchProject
from stock_research.jobs import execute_research_job, recover_stuck_jobs
from stock_research.llm import AnalysisClaim, AnalysisRequest, AnalysisResponse, HeuristicLLMProvider, LLMError
from stock_research.pipeline import ResearchPipeline
from stock_research.service import chat_entry_payload
from stock_research.storage import SQLiteStore
from stock_research.workflow import ResearchWorkflow

DOCUMENT_TEXT = (
    "Revenue FY2024 HK$ 100 million\n"
    "Net income FY2024 HK$ 20 million\n"
    "Business: cloud growth is important.\n"
    "Risk: competition remains intense."
)


class FlakyLLMProvider:
    """Fails the first analysis call, succeeds afterwards."""

    provider_name = "flaky"
    model_name = "flaky-v1"

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        self.calls += 1
        if self.calls == 1:
            raise LLMError("simulated model outage")
        return AnalysisResponse(
            (AnalysisClaim("business", "Cloud growth matters", (request.evidence[0]["evidence_id"],), 0.9),),
            self.provider_name,
            self.model_name,
        )


class CountingIngestor:
    """Wraps the pipeline ingestor to observe re-downloads across attempts."""

    def __init__(self) -> None:
        self.calls = 0
        self.delegate = DocumentIngestor()

    def chunk_many(self, documents):
        self.calls += 1
        return self.delegate.chunk_many(documents)


class AdoptRunTests(unittest.TestCase):
    def test_adopt_run_resets_running_steps_and_parks_run(self) -> None:
        store = SQLiteStore()
        try:
            workflow = ResearchWorkflow(store=store)
            project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯")
            workflow.create_project(project)
            run = workflow.create_run(project.id, "研究公司", date(2025, 12, 31))
            workflow.plan(run.id)
            workflow.start_step(run.id, "collect_filings")
            workflow.complete_step(run.id, "collect_filings", {"document_count": 1})
            workflow.start_step(run.id, "extract_financials")  # left running by a dead process

            adopted = workflow.adopt_run(run.id)
            self.assertEqual(adopted.status.value, "paused")
            by_key = {step.step_key: step for step in adopted.steps}
            self.assertEqual(by_key["collect_filings"].status, "completed")
            self.assertEqual(by_key["extract_financials"].status, "pending")
            events = [event.event_type for event in store.events_for_run(run.id)]
            self.assertIn("run/resumed", events)

            with self.assertRaises(KeyError):
                workflow.adopt_run(uuid4())
        finally:
            store.close()

    def test_adopt_rejects_terminal_runs(self) -> None:
        store = SQLiteStore()
        try:
            workflow = ResearchWorkflow(store=store)
            project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯")
            workflow.create_project(project)
            run = workflow.create_run(project.id, "研究公司", date(2025, 12, 31))
            store.save_run(run)
            store.connection.execute("UPDATE runs SET status='completed' WHERE id=?", (str(run.id),))
            store.connection.commit()
            with self.assertRaises(ValueError):
                workflow.adopt_run(run.id)
        finally:
            store.close()


class PipelineResumeTests(unittest.TestCase):
    def test_resume_reuses_completed_step_artifacts(self) -> None:
        store = SQLiteStore()
        try:
            flaky = FlakyLLMProvider()
            pipeline = ResearchPipeline(workflow=ResearchWorkflow(store=store), llm_provider=flaky)
            pipeline.ingestor = CountingIngestor()
            project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯")
            pipeline.workflow.create_project(project)
            document = RawDocument(
                company_id=project.company_id,
                source_type="local_fixture",
                source_url="fixture://tencent",
                title="sample",
                content=DOCUMENT_TEXT,
            )

            with self.assertRaises(LLMError):
                pipeline.run(
                    project_id=project.id,
                    question="研究公司",
                    as_of_date=date(2025, 12, 31),
                    documents=[document],
                )
            interrupted = next(iter(pipeline.workflow.runs.values()))
            completed_steps = {step.step_key for step in interrupted.steps if step.status == "completed"}
            self.assertEqual(completed_steps, {"collect_filings", "extract_financials"})
            self.assertEqual(pipeline.ingestor.calls, 1)

            report = pipeline.run(
                project_id=project.id,
                question="研究公司",
                as_of_date=date(2025, 12, 31),
                documents=[document],
                resume_run_id=interrupted.id,
            )
            self.assertEqual(report["run_id"], str(interrupted.id))
            self.assertTrue(report["llm_claims"])
            self.assertGreaterEqual(len(report["facts"]), 2)
            self.assertIn("markdown", report)
            # No re-download: collection ran exactly once across both attempts.
            self.assertEqual(pipeline.ingestor.calls, 1)
            # Model was called once per attempt: the failed one and the retry.
            self.assertEqual(flaky.calls, 2)

            finished = store.load_run(interrupted.id)
            self.assertEqual(finished.status.value, "completed")
            self.assertTrue(all(step.status == "completed" for step in finished.steps))
        finally:
            store.close()


class RecoverStuckJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch("stock_research.service.resolve_provider", return_value=HeuristicLLMProvider())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_recover_executes_stuck_running_job(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = f"{directory}/research.sqlite3"
            with patch.dict(os.environ, {"AI_STOCK_DB": db_path, "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""}, clear=False):
                first = chat_entry_payload(
                    {"name": "腾讯", "symbol": "00700", "content": "你好"}, db_path=db_path,
                )
                session_id = UUID(first["session_id"])
                store = SQLiteStore(db_path)
                job_id = uuid4()
                payload = {"name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司", "document": DOCUMENT_TEXT}
                store.create_job(job_id, session_id, payload, queue_name="inline")
                store.update_job(job_id, status="running", attempts=1)
                store.close()

                summary = recover_stuck_jobs(db_path)
                self.assertEqual(summary, {"recovered": 1, "failed": 0})

                reopened = SQLiteStore(db_path)
                try:
                    job = reopened.load_job(job_id)
                    self.assertEqual(job["status"], "completed")
                    self.assertTrue(job.get("run_id"))
                    runs = reopened.list_runs(session_id=session_id)
                    self.assertEqual(runs[0]["status"], "completed")
                finally:
                    reopened.close()

    def test_recover_marks_exhausted_jobs_failed_without_executing(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = f"{directory}/research.sqlite3"
            with patch.dict(os.environ, {"AI_STOCK_DB": db_path, "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""}, clear=False):
                first = chat_entry_payload(
                    {"name": "腾讯", "symbol": "00700", "content": "你好"}, db_path=db_path,
                )
                session_id = UUID(first["session_id"])
                store = SQLiteStore(db_path)
                job_id = uuid4()
                store.create_job(job_id, session_id, {"name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司"}, queue_name="inline")
                store.update_job(job_id, status="running", attempts=3)
                store.close()

                summary = recover_stuck_jobs(db_path)
                self.assertEqual(summary, {"recovered": 0, "failed": 1})

                reopened = SQLiteStore(db_path)
                try:
                    job = reopened.load_job(job_id)
                    self.assertEqual(job["status"], "failed")
                    self.assertEqual(job["error"]["type"], "JobRecoveryExhausted")
                    self.assertEqual(reopened.list_runs(session_id=session_id), [])
                finally:
                    reopened.close()

    def test_execute_job_resumes_interrupted_run(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = f"{directory}/research.sqlite3"
            with patch.dict(os.environ, {"AI_STOCK_DB": db_path, "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""}, clear=False):
                first = chat_entry_payload(
                    {"name": "腾讯", "symbol": "00700", "content": "你好"}, db_path=db_path,
                )
                session_id = UUID(first["session_id"])
                store = SQLiteStore(db_path)
                project = store.find_project("00700")
                # Simulate an attempt that died after planning: a non-terminal run
                # with the same question is the resume candidate.
                workflow = ResearchWorkflow(store=store)
                pipeline_project = store.load_project(project.id)
                workflow.create_project(pipeline_project)
                interrupted = workflow.create_run(pipeline_project.id, "研究公司", date(2025, 12, 31), session_id=session_id)
                workflow.plan(interrupted.id)
                job_id = uuid4()
                store.create_job(job_id, session_id, {"name": "腾讯", "symbol": "00700", "as_of_date": "2025-12-31", "question": "研究公司", "document": DOCUMENT_TEXT}, queue_name="inline")
                store.close()

                result = execute_research_job(str(job_id), db_path)
                self.assertEqual(result["run_id"], str(interrupted.id))

                reopened = SQLiteStore(db_path)
                try:
                    job = reopened.load_job(job_id)
                    self.assertEqual(job["status"], "completed")
                    self.assertEqual(job["run_id"], str(interrupted.id))
                finally:
                    reopened.close()


if __name__ == "__main__":
    unittest.main()
