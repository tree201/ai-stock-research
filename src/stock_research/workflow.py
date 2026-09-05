"""Deterministic first-version research workflow.

No LLM or network calls are made here.  Adapters will later implement the
individual steps, while this module owns state transitions and event logging.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from .domain import ResearchProject, ResearchRun, ResearchSession, ResearchStep, RunStatus, utc_now
from .events import InMemoryEventLog
from .storage import SQLiteStore


DEFAULT_STEPS: tuple[tuple[str, str], ...] = (
    ("collect_filings", "获取年报和业绩公告"),
    ("extract_financials", "抽取收入、利润和现金流"),
    ("analyze_business", "分析业务和收入结构"),
    ("analyze_risks", "寻找风险和反方证据"),
    ("calculate_valuation", "计算基础和情景估值"),
    ("review", "检查数字、引用和截止日期"),
    ("compile_report", "生成研究报告"),
)


class ResearchWorkflow:
    def __init__(self, event_log: InMemoryEventLog | None = None, store: SQLiteStore | None = None) -> None:
        self.event_log = event_log or InMemoryEventLog()
        self.store = store
        self.projects: dict[UUID, ResearchProject] = {}
        self.runs: dict[UUID, ResearchRun] = {}

    def create_project(self, project: ResearchProject) -> ResearchProject:
        self.projects[project.id] = project
        if self.store:
            self.store.save_project(project)
        return project

    def create_session(self, session: ResearchSession) -> ResearchSession:
        if session.project_id not in self.projects:
            raise KeyError(f"unknown project: {session.project_id}")
        if self.store:
            self.store.save_session(session)
        return session

    def create_run(
        self,
        project_id: UUID,
        question: str,
        as_of_date: date,
        run_type: str = "initial",
        session_id: UUID | None = None,
    ) -> ResearchRun:
        if project_id not in self.projects:
            raise KeyError(f"unknown project: {project_id}")
        if not question.strip():
            raise ValueError("question must not be empty")
        if session_id is not None and self.store:
            session = self.store.load_session(session_id)
            if session.project_id != project_id:
                raise ValueError("session does not belong to project")
        run = ResearchRun(
            project_id=project_id,
            question=question.strip(),
            as_of_date=as_of_date,
            run_type=run_type,
            session_id=session_id,
        )
        self.runs[run.id] = run
        self._emit(run, "run/created", {"question": run.question, "as_of_date": str(as_of_date), "run_type": run_type})
        return run

    def adopt_run(self, run_id: UUID) -> ResearchRun:
        """Adopt a persisted, non-terminal run so the pipeline can resume it.

        Steps left in ``running`` state by a dead process are reset to
        ``pending``: the pipeline only persists artifacts at step completion
        boundaries, so their partial work is not committed anyway.  Non-PAUSED
        runs are parked in PAUSED first, which every active status allows;
        fully completed runs (crash between compile and finish) move to
        REVIEWING so ``finish()`` can run.
        """
        if self.store is None:
            raise ValueError("adopt_run requires a store")
        run = self.store.load_run(run_id)
        if run.status in {RunStatus.COMPLETED, RunStatus.CANCELED}:
            raise ValueError(f"cannot adopt a {run.status.value} run")
        if run.steps and all(step.status == "completed" for step in run.steps):
            if run.status != RunStatus.REVIEWING:
                run.transition(RunStatus.REVIEWING)
        elif run.status not in {RunStatus.CREATED, RunStatus.PAUSED}:
            run.transition(RunStatus.PAUSED)
        for step in run.steps:
            if step.status == "running":
                step.status = "pending"
                step.started_at = None
        self.projects[run.project_id] = self.store.load_project(run.project_id)
        self.runs[run.id] = run
        self._emit(run, "run/resumed", {"status": run.status.value, "completed_steps": [step.step_key for step in run.steps if step.status == "completed"]})
        return run

    def plan(self, run_id: UUID, steps: tuple[tuple[str, str], ...] = DEFAULT_STEPS) -> ResearchRun:
        run = self._get_run(run_id)
        run.transition(RunStatus.PLANNED)
        replanned = run.plan_version > 0
        run.plan_version += 1
        run.steps = [
            ResearchStep(run_id=run.id, step_key=key, order=index, input_data={"purpose": purpose})
            for index, (key, purpose) in enumerate(steps, start=1)
        ]
        self._emit(run, "run/replanned" if replanned else "run/planned", {"plan_version": run.plan_version, "steps": [step.step_key for step in run.steps]})
        return run

    def start_step(self, run_id: UUID, step_key: str) -> ResearchStep:
        """ReAct 编排入口：按名启动任意步骤，顺序由编排循环决定。

        步骤可以重复执行（例如模型决定再收一轮资料）：已完成步骤会被重置
        为 running，attempt 递增。
        """
        run = self._get_run(run_id)
        step = self._get_step(run, step_key)
        expected = self._status_for_step(step_key)
        if run.status is not expected:
            run.transition(expected)
        step.status = "running"
        step.attempt += 1
        step.started_at = utc_now()
        self._emit(run, "step/started", {"step_key": step_key, "attempt": step.attempt})
        return step

    def complete_step(self, run_id: UUID, step_key: str, output: dict[str, Any] | None = None) -> ResearchStep:
        run = self._get_run(run_id)
        step = self._get_step(run, step_key)
        # ReAct 下 start_step 已把步骤置 running；宽容幂等：重复 complete 覆盖输出
        if step.status not in {"running", "completed"}:
            raise ValueError(f"step {step_key} is not running")
        step.status = "completed"
        step.output_data = output or {}
        step.completed_at = utc_now()
        self._emit(run, "step/completed", {"step_key": step_key, "output": step.output_data})
        return step

    def fail_step(self, run_id: UUID, step_key: str, error: dict[str, Any]) -> ResearchStep:
        run = self._get_run(run_id)
        step = self._get_step(run, step_key)
        if step.status != "running":
            raise ValueError(f"step {step_key} is not running")
        step.status = "failed"
        step.error = dict(error)
        step.completed_at = utc_now()
        run.transition(RunStatus.FAILED)
        self._emit(run, "step/failed", {"step_key": step_key, "error": step.error})
        return step

    def pause(self, run_id: UUID) -> ResearchRun:
        run = self._get_run(run_id)
        if run.status in {RunStatus.COMPLETED, RunStatus.CANCELED, RunStatus.FAILED, RunStatus.PAUSED}:
            raise ValueError(f"cannot pause run in {run.status.value}")
        run.transition(RunStatus.PAUSED)
        self._emit(run, "run/paused", {})
        return run

    def finish(self, run_id: UUID) -> ResearchRun:
        run = self._get_run(run_id)
        if any(step.status != "completed" for step in run.steps):
            raise ValueError("cannot complete run while steps are unfinished")
        if run.status != RunStatus.REVIEWING:
            raise ValueError(f"run is {run.status.value}, expected reviewing")
        run.transition(RunStatus.COMPLETED)
        self._emit(run, "report/published", {"run_id": str(run.id)})
        return run

    def _get_run(self, run_id: UUID) -> ResearchRun:
        try:
            return self.runs[run_id]
        except KeyError as exc:
            raise KeyError(f"unknown run: {run_id}") from exc

    @staticmethod
    def _get_step(run: ResearchRun, step_key: str) -> ResearchStep:
        for step in run.steps:
            if step.step_key == step_key:
                return step
        raise KeyError(f"unknown step: {step_key}")

    @staticmethod
    def _status_for_step(step_key: str) -> RunStatus:
        return {
            "collect_filings": RunStatus.COLLECTING_DATA,
            "extract_financials": RunStatus.EXTRACTING_FACTS,
            "analyze_business": RunStatus.ANALYZING,
            "analyze_risks": RunStatus.ANALYZING,
            "calculate_valuation": RunStatus.CALCULATING,
            "review": RunStatus.REVIEWING,
            "compile_report": RunStatus.REVIEWING,
        }[step_key]

    def _emit(self, run: ResearchRun, event_type: str, payload: dict[str, Any]) -> None:
        timestamp = utc_now()
        self.event_log.append(run.id, event_type, payload, timestamp)
        if self.store:
            self.store.save_run(run)
            self.store.append_event(run.id, event_type, payload, timestamp)
            if run.session_id:
                self.store.append_session_event(run.session_id, event_type, payload, timestamp)
