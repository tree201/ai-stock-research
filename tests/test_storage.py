from datetime import date, datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

from stock_research import ResearchProject, ResearchSession, ResearchWorkflow, SessionMessage, SQLiteStore, RunStatus


class SQLiteStorageTests(unittest.TestCase):
    def test_run_and_events_survive_new_store_instance(self) -> None:
        with TemporaryDirectory() as directory:
            path = f"{directory}/research.sqlite3"
            store = SQLiteStore(path)
            project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯")
            workflow = ResearchWorkflow(store=store)
            workflow.create_project(project)
            run = workflow.create_run(project.id, "研究腾讯", date(2025, 12, 31))
            workflow.plan(run.id)
            workflow.start_step(run.id, "collect_filings")
            workflow.complete_step(run.id, "collect_filings", {"documents": 1})
            store.close()

            reopened = SQLiteStore(path)
            loaded = reopened.load_run(run.id)
            self.assertEqual(loaded.status, RunStatus.COLLECTING_DATA)
            self.assertEqual(loaded.steps[0].output_data, {"documents": 1})
            self.assertEqual([event.event_type for event in reopened.events_for_run(run.id)], ["run/created", "run/planned", "step/started", "step/completed"])
            reopened.close()

    def test_sessions_and_company_activity_are_sorted_by_latest_event(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteStore(f"{directory}/research.sqlite3")
            project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯")
            workflow = ResearchWorkflow(store=store)
            workflow.create_project(project)
            older = ResearchSession(project.id, title="旧主题")
            newer = ResearchSession(project.id, title="新主题")
            workflow.create_session(older); workflow.create_session(newer)
            base = datetime(2025, 1, 1, tzinfo=timezone.utc)
            store.append_session_event(older.id, "message/user", {"preview": "旧"}, base)
            store.append_session_event(newer.id, "message/user", {"preview": "新"}, base + timedelta(days=1))
            sessions = store.list_sessions(project.id)
            self.assertEqual([item["title"] for item in sessions], ["新主题", "旧主题"])
            companies = store.list_companies()
            self.assertEqual(companies[0]["symbol"], "00700")
            self.assertEqual(companies[0]["session_count"], 2)
            self.assertEqual(companies[0]["latest_event_preview"], "新")
            store.save_session_message(SessionMessage(newer.id, "assistant", "text", {"text": "已回复"}))
            self.assertEqual(len(store.list_session_messages(newer.id)), 1)
            self.assertEqual(store.set_session_status(newer.id, "archived").status, "archived")
            self.assertEqual(store.set_session_status(newer.id, "active").status, "active")
            store.close()

    def test_companies_with_same_symbol_are_grouped(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteStore(f"{directory}/research.sqlite3")
            workflow = ResearchWorkflow(store=store)
            first = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯")
            second = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯（旧记录）")
            workflow.create_project(first)
            workflow.create_project(second)
            first_session = ResearchSession(first.id, title="增长")
            second_session = ResearchSession(second.id, title="估值")
            workflow.create_session(first_session)
            workflow.create_session(second_session)
            store.save_session_message(SessionMessage(first_session.id, "user", "text", {"text": "看增长"}))
            store.save_session_message(SessionMessage(second_session.id, "user", "text", {"text": "看估值"}))
            companies = store.list_companies()
            self.assertEqual(len(companies), 1)
            self.assertEqual(companies[0]["session_count"], 2)
            self.assertCountEqual(companies[0]["project_ids"], [str(first.id), str(second.id)])
            store.close()
