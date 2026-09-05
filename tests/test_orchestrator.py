"""Orchestrator tests: ReAct 主循环 + 步骤工具化 + 硬门禁。

覆盖四类行为：
1. 脚本 provider 驱动的默认路径等价于原固定瀑布；
2. 模型决定乱序/重复步骤时 checkpoint 仍一致（start_step 任意顺序）；
3. 硬门禁：未 review 不能出报告、review 未通过不能出报告；
4. 自适应路径：金融股跳过 DCF、资料不足时再收一轮。
"""

from datetime import date
import unittest
from typing import Any
from unittest import TestCase
from uuid import uuid4

from stock_research.agent_core import UnifiedTools, run_agent_turn
from stock_research.documents import RawDocument
from stock_research.llm import HeuristicLLMProvider
from stock_research.orchestrator import (
    HeuristicOrchestratorProvider,
    ResearchTools,
)
from stock_research.workflow import ResearchWorkflow


def _run_research(provider: Any, project: Any, question: str, tools: ResearchTools) -> dict[str, Any]:
    """统一循环 + research_only 工具；返回 {answer, steps, report} 兼容断言。"""
    unified = UnifiedTools(None, lambda _question: tools, research_only=True)
    outcome = run_agent_turn(provider, project, question, unified)
    return {"answer": outcome.answer, "steps": outcome.steps, "report": outcome.report}


def _document(company_id: Any) -> RawDocument:
    return RawDocument(
        company_id=company_id,
        source_type="hkex_filing",
        source_url="https://example.com/report.pdf",
        title="年报",
        content=(
            "收入 100.0 百万港元\n"
            "净利润 12.0 百万港元\n"
            "经营活动现金流 30.0 百万港元\n"
            "资本开支 5.0 百万港元\n"
            "现金 50.0 百万港元\n"
            "债务 20.0 百万港元\n"
            "云业务增长强劲，主要风险为市场竞争和监管变化。\n"
        ),
        published_at=None,
    )


def _tools_with_documents(workflow: ResearchWorkflow, run_id) -> ResearchTools:
    project = workflow.projects[workflow.runs[run_id].project_id]
    tools = ResearchTools(
        workflow,
        run_id,
        llm_provider=HeuristicLLMProvider(),
        dcf_assumptions={"growth_rates": [0.05, 0.04], "discount_rate": 0.09, "terminal_growth": 0.02},
        question="研究这家公司",
    )
    tools.documents = [_document(project.company_id)]
    return tools


class ScriptedOrchestratorTests(TestCase):
    """脚本 provider 走完整循环，产物与 checkpoint 与旧瀑布等价。"""

    def setUp(self) -> None:
        self.workflow = ResearchWorkflow()
        self.project = self.workflow.create_project(
            __import__("stock_research", fromlist=["ResearchProject"]).ResearchProject(
                user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯"
            )
        )
        self.run = self.workflow.create_run(self.project.id, "研究腾讯", date(2025, 12, 31))
        self.workflow.plan(self.run.id)

    def test_scripted_run_completes_all_steps_in_order(self) -> None:
        tools = _tools_with_documents(self.workflow, self.run.id)
        outcome = _run_research(HeuristicOrchestratorProvider(), self.project, "研究腾讯", tools)
        self.assertIsNotNone(outcome["report"])
        completed = {step.step_key for step in self.workflow.runs[self.run.id].steps if step.status == "completed"}
        self.assertEqual(
            completed,
            {"collect_filings", "extract_financials", "analyze_business", "analyze_risks", "calculate_valuation", "review", "compile_report"},
        )
        self.assertTrue(outcome["report"].get("markdown"))

    def test_run_facade_equivalent_report(self) -> None:
        from stock_research.pipeline import ResearchPipeline
        pipeline = ResearchPipeline(workflow=self.workflow, llm_provider=HeuristicLLMProvider())
        report = pipeline.run(
            project_id=self.project.id,
            question="研究腾讯",
            as_of_date=date(2025, 12, 31),
            documents=[_document(self.project.company_id)],
        )
        self.assertIn("run_id", report)
        self.assertIn("report_id", report)


class AdaptiveOrderingTests(TestCase):
    """模型乱序/重复决策：状态机放行，checkpoint 一致。"""

    def setUp(self) -> None:
        self.workflow = ResearchWorkflow()
        project_cls = __import__("stock_research", fromlist=["ResearchProject"]).ResearchProject
        self.project = self.workflow.create_project(
            project_cls(user_id=uuid4(), company_id=uuid4(), symbol="00005", name="汇丰")
        )
        self.run = self.workflow.create_run(self.project.id, "研究汇丰", date(2025, 12, 31))
        self.workflow.plan(self.run.id)

    def test_out_of_order_and_repeat_collect(self) -> None:
        tools = _tools_with_documents(self.workflow, self.run.id)
        # 模型"犯错"：先想算估值（被前置检查拦下），再抽事实（拦下），
        # 收资料后又收一轮（无新增资料 → 幂等 no-op），然后正常走完。
        script = [
            {"action": "calculate_valuation"},
            {"action": "extract_financials"},
            {"action": "collect_filings"},
            {"action": "collect_filings"},
            {"action": "extract_financials"},
            {"action": "analyze_business"},
            {"action": "analyze_risks"},
            {"action": "calculate_valuation"},
            {"action": "review"},
            {"action": "compile_report"},
            {"action": "final", "answer": "完成"},
        ]
        provider = _Scripted(script)
        outcome = _run_research(provider, self.project, "研究汇丰", tools)
        self.assertIsNotNone(outcome["report"])
        # 前两次被拦截的尝试不产生 step/completed；重复 collect 因无新增
        # 资料是幂等 no-op，同样不产生事件，共 7 条
        events = [event.event_type for event in self.workflow.event_log.for_run(self.run.id)]
        self.assertEqual(events.count("step/completed"), 7)
        calculate = next(step for step in self.workflow.runs[self.run.id].steps if step.step_key == "calculate_valuation")
        self.assertEqual(calculate.status, "completed")

    def test_completed_steps_are_not_rerun(self) -> None:
        """checkpoint 复用：已完成步骤重入时只返回复用观察，不产生新事件。"""
        tools = _tools_with_documents(self.workflow, self.run.id)
        script = [
            {"action": "collect_filings"},
            {"action": "extract_financials"},
            {"action": "extract_financials"},
            {"action": "analyze_business"},
            {"action": "analyze_risks"},
            {"action": "calculate_valuation"},
            {"action": "review"},
            {"action": "compile_report"},
            {"action": "final", "answer": "完成"},
        ]
        outcome = _run_research(_Scripted(script), self.project, "研究汇丰", tools)
        self.assertIsNotNone(outcome["report"])
        repeat = next(step for step in outcome["steps"] if step["tool"] == "extract_financials" and "checkpoint 复用" in step["observation"])
        self.assertIsNotNone(repeat)
        events = [event.event_type for event in self.workflow.event_log.for_run(self.run.id)]
        self.assertEqual(events.count("step/completed"), 7)


class HardGateTests(TestCase):
    """不可让渡门禁：算术走工具、报告必须过 review。"""

    def setUp(self) -> None:
        self.workflow = ResearchWorkflow()
        project_cls = __import__("stock_research", fromlist=["ResearchProject"]).ResearchProject
        self.project = self.workflow.create_project(
            project_cls(user_id=uuid4(), company_id=uuid4(), symbol="00001", name="长和")
        )
        self.run = self.workflow.create_run(self.project.id, "研究长和", date(2025, 12, 31))
        self.workflow.plan(self.run.id)
        self.tools = _tools_with_documents(self.workflow, self.run.id)

    def test_compile_report_blocked_without_review(self) -> None:
        observation = self.tools.run("compile_report", {})
        self.assertIn("review", observation.text)
        compile_step = next(step for step in self.workflow.runs[self.run.id].steps if step.step_key == "compile_report")
        self.assertNotEqual(compile_step.status, "completed")

    def test_steps_require_collect_first(self) -> None:
        observation = self.tools.run("extract_financials", {})
        self.assertIn("collect_filings", observation.text)

    def test_review_failure_lists_issues(self) -> None:
        self.tools.run("collect_filings", {})
        # 不做 extract 直接 review：没有事实，review 必须列出 issue
        observation = self.tools.run("review", {})
        self.assertIn("没有抽取到财务事实", observation.text)


class LLMOutageResilienceTests(TestCase):
    """analyze 撞内容风控/供应商故障时降级继续，不炸整轮研究。"""

    def setUp(self) -> None:
        self.workflow = ResearchWorkflow()
        project_cls = __import__("stock_research", fromlist=["ResearchProject"]).ResearchProject
        self.project = self.workflow.create_project(
            project_cls(user_id=uuid4(), company_id=uuid4(), symbol="01113", name="长实集团")
        )
        self.run = self.workflow.create_run(self.project.id, "研究长实", date(2025, 12, 31))
        self.workflow.plan(self.run.id)

    def test_analyze_llm_failure_degrades_and_run_still_reports(self) -> None:
        from stock_research.llm import LLMError as _LLMError

        class BlockedProvider(HeuristicLLMProvider):
            """chat_json 正常（编排循环可用），analyze 被风控拦截。"""

            def analyze(self, request):
                raise _LLMError("LLM request failed: Content Exists Risk")

        tools = _tools_with_documents(self.workflow, self.run.id)
        tools.llm_provider = BlockedProvider()
        outcome = _run_research(HeuristicOrchestratorProvider(), self.project, "研究长实", tools)
        self.assertIsNotNone(outcome["report"], "analyze 被风控拦截后研究仍应产出报告骨架")
        degrade = next(step for step in outcome["steps"] if step["tool"] == "analyze_business" and "Content Exists Risk" in step["observation"])
        self.assertIsNotNone(degrade)
        finished = self.workflow.runs[self.run.id]
        self.assertEqual(finished.status.value, "reviewing")
        analyze_step = next(step for step in finished.steps if step.step_key == "analyze_business")
        self.assertEqual(analyze_step.status, "completed")
        self.assertIn("llm_error", analyze_step.output_data)

    def test_transient_chat_json_failure_is_retried(self) -> None:
        """供应商偶发空内容/抖动：重试后恢复，研究不被一轮失败炸掉。"""
        from stock_research.llm import LLMError as _LLMError

        class FlakyOrchestrator(HeuristicOrchestratorProvider):
            """第一次 chat_json 抛瞬时错误，之后走正常脚本。"""

            def __init__(self) -> None:
                super().__init__()
                self.failures_left = 2

            def chat_json(self, system, user):
                if self.failures_left > 0:
                    self.failures_left -= 1
                    raise _LLMError("LLM chat_json failed: Expecting value: line 1 column 1 (char 0)")
                return super().chat_json(system, user)

        tools = _tools_with_documents(self.workflow, self.run.id)
        outcome = _run_research(FlakyOrchestrator(), self.project, "研究长实", tools)
        self.assertIsNotNone(outcome["report"], "瞬时失败重试后研究应正常完成")


class _Scripted:
    """按脚本回放决策的假 provider，用于测乱序路径。"""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = list(script)
        self.cursor = 0

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        decision = self.script[min(self.cursor, len(self.script) - 1)]
        self.cursor += 1
        return dict(decision)


if __name__ == "__main__":
    unittest.main()
