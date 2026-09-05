from datetime import date
from uuid import uuid4
import unittest

from stock_research import ResearchProject, ResearchWorkflow, RunStatus


class ResearchWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = ResearchWorkflow()
        self.project = self.workflow.create_project(
            ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯")
        )

    def test_create_and_plan_run(self) -> None:
        run = self.workflow.create_run(self.project.id, "研究腾讯是否适合长期持有", date(2025, 12, 31))
        self.assertEqual(run.status, RunStatus.CREATED)

        self.workflow.plan(run.id)
        self.assertEqual(run.status, RunStatus.PLANNED)
        self.assertEqual(len(run.steps), 7)
        self.assertEqual(run.plan_version, 1)
        self.assertEqual(self.workflow.event_log.for_run(run.id)[-1].event_type, "run/planned")

    def test_complete_full_workflow(self) -> None:
        run = self.workflow.create_run(self.project.id, "研究腾讯", date(2025, 12, 31))
        self.workflow.plan(run.id)

        for step in run.steps:
            started = self.workflow.start_step(run.id, step.step_key)
            self.assertEqual(started.step_key, step.step_key)
            self.workflow.complete_step(run.id, step.step_key, {"ok": True})

        self.assertEqual(run.status, RunStatus.REVIEWING)
        self.workflow.finish(run.id)
        self.assertEqual(run.status, RunStatus.COMPLETED)
        events = self.workflow.event_log.for_run(run.id)
        self.assertEqual(events[0].event_type, "run/created")
        self.assertEqual(events[-1].event_type, "report/published")

    def test_invalid_transition_is_rejected(self) -> None:
        run = self.workflow.create_run(self.project.id, "研究腾讯", date(2025, 12, 31))
        with self.assertRaises(ValueError):
            run.transition(RunStatus.COMPLETED)

    def test_failure_is_recorded(self) -> None:
        run = self.workflow.create_run(self.project.id, "研究腾讯", date(2025, 12, 31))
        self.workflow.plan(run.id)
        step = self.workflow.start_step(run.id, "collect_filings")
        self.workflow.fail_step(run.id, step.step_key, {"code": "SOURCE_TIMEOUT"})
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(step.status, "failed")
        self.assertEqual(self.workflow.event_log.for_run(run.id)[-1].event_type, "step/failed")


if __name__ == "__main__":
    unittest.main()

