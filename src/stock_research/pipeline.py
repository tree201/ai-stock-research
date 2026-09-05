"""Research pipeline facade: ReAct-orchestrated over step tools.

run() 现在是编排器入口：把就绪的 documents 交给 ResearchTools 工作区，
由模型（或无 LLM 时的脚本 provider）在 ReAct 循环里自主决定步骤顺序。
固定瀑布已删除；checkpoint 语义不变（每步 complete_step 落库，支持
resume_run_id 续跑）。
"""

from __future__ import annotations

from datetime import date
from typing import Callable, Iterable
from uuid import UUID, uuid4

from .agent_core import UnifiedTools, run_agent_turn
from .context import ContextBuilder
from .documents import DocumentIngestor, RawDocument
from .llm import LLMProvider
from .orchestrator import HeuristicOrchestratorProvider, ResearchTools
from .workflow import ResearchWorkflow


class ResearchPipeline:
    def __init__(
        self,
        workflow: ResearchWorkflow | None = None,
        llm_provider: LLMProvider | None = None,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self.workflow = workflow or ResearchWorkflow()
        self.llm_provider = llm_provider
        self.context_builder = context_builder or ContextBuilder()
        self.ingestor = DocumentIngestor()

    def run(
        self,
        *,
        project_id: UUID,
        session_id: UUID | None = None,
        question: str,
        as_of_date: date,
        documents: Iterable[RawDocument],
        dcf_assumptions: dict[str, float | list[float]] | None = None,
        run_type: str = "initial",
        report_transform: Callable[[dict], dict] | None = None,
        resume_run_id: UUID | None = None,
    ) -> dict:
        store = self.workflow.store
        if resume_run_id is not None:
            # Checkpoint resume: adopt the persisted run; completed steps keep
            # their stored artifacts, the loop only fills the gaps.
            run = self.workflow.adopt_run(resume_run_id)
            question = run.question
            as_of_date = run.as_of_date
            session_id = run.session_id
            project_id = run.project_id
        else:
            run = self.workflow.create_run(project_id, question, as_of_date, run_type=run_type, session_id=session_id)
            self.workflow.plan(run.id)

        tools = ResearchTools(
            self.workflow,
            run.id,
            llm_provider=self.llm_provider,
            dcf_assumptions=dcf_assumptions,
            question=question,
            context_builder=self.context_builder,
        )
        tools.ingestor = self.ingestor
        tools.documents = [document for document in documents if not document.published_at or document.published_at.date() <= as_of_date]
        if not tools.documents and "collect_filings" not in tools.completed_steps:
            # 与旧管线一致的 fail-fast：没有任何可用资料时研究无法开始，
            # 让上层（聊天/任务队列）拿到明确的 ValueError 而不是循环末端的 LLMError。
            raise ValueError("no documents available before as_of_date")
        if resume_run_id is not None:
            # Checkpoint resume: 恢复已完成步骤的产物，run() 会跳过它们，
            # 循环只补齐剩余步骤（不重复下载、不重复调模型）。
            tools.hydrate_completed()

        provider = self.llm_provider if self.llm_provider is not None and hasattr(self.llm_provider, "chat_json") else HeuristicOrchestratorProvider()
        project = self.workflow.projects[run.project_id]
        unified = UnifiedTools(None, lambda _question: tools, research_only=True)
        outcome = run_agent_turn(provider, project, question, unified)

        report = outcome.report
        if report is None:
            raise ValueError("research did not produce a report (未出报告：review 未通过或步骤未完成)")
        self.workflow.finish(run.id)
        report.setdefault("report_id", str(uuid4()))
        report["run_id"] = str(run.id)
        report["plan_version"] = run.plan_version
        if report_transform is not None:
            report = report_transform(report)
        if store:
            store.save_report(run.id, report, UUID(report["report_id"]))
        return report
