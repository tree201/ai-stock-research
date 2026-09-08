"""Unified agent core tests: plan-as-tool、懒 run 创建、聊天内完整研究。

覆盖行为：
1. plan 工具：有效计划落库（plan_version/replanned 事件/预算授予）；
   未知步骤被引导；研究启动后拒绝改计划；
2. 懒创建：纯聊天不建 run；
3. 聊天内完整研究：脚本 provider 走 plan→研究→出报告→report_card；
4. 无资料激活失败：可行动观察 + run paused + 仍返回回答；
5. 研究中途 final：无报告不炸轮次，run paused；
6. kind=chat 后台任务：整轮聊天经 execute_research_job 执行。
"""

from datetime import date
import os
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from stock_research.agent_core import PLAN_STEP_BONUS, UnifiedTools, run_agent_turn
from stock_research.domain import ResearchProject
from stock_research.documents import RawDocument
from stock_research.jobs import InlineQueue, create_and_enqueue, execute_research_job
from stock_research.llm import HeuristicLLMProvider
from stock_research.service import chat_entry_payload, chat_payload, history_payload
from stock_research.storage import SQLiteStore
from stock_research.tools import CompanyTools
from stock_research.workflow import ResearchWorkflow


class ScriptedProvider:
    """按脚本回放决策；记录全部 prompt 供断言。"""

    def __init__(self, script: list[dict]) -> None:
        self.script = list(script)
        self.cursor = 0
        self.prompts: list[str] = []
        self.systems: list[str] = []

    def chat_json(self, system: str, user: str) -> dict:
        self.systems.append(system)
        self.prompts.append(user)
        decision = self.script[min(self.cursor, len(self.script) - 1)]
        self.cursor += 1
        return dict(decision)


class _Workspace:
    """内存 workflow + 预置 run 的研究工厂（不落库）。"""

    def __init__(self, with_documents: bool = True) -> None:
        self.workflow = ResearchWorkflow()
        self.project = self.workflow.create_project(
            ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯控股")
        )
        self.run = None
        self.with_documents = with_documents

    def activate(self, question: str):
        from stock_research.orchestrator import ResearchTools

        self.run = self.workflow.create_run(self.project.id, question, date(2025, 12, 31))
        self.workflow.plan(self.run.id)
        tools = ResearchTools(
            self.workflow, self.run.id,
            llm_provider=HeuristicLLMProvider(),
            dcf_assumptions={"growth_rates": [0.05], "discount_rate": 0.09, "terminal_growth": 0.02},
            question=question,
        )
        if self.with_documents:
            tools.documents = [RawDocument(
                company_id=self.project.company_id,
                source_type="hkex_filing",
                source_url="https://example.com/report.pdf",
                title="年报",
                content=(
                    "收入 100.0 百万港元\n净利润 12.0 百万港元\n经营活动现金流 30.0 百万港元\n"
                    "资本开支 5.0 百万港元\n现金 50.0 百万港元\n债务 20.0 百万港元\n"
                    "云业务增长强劲，主要风险为市场竞争和监管变化。\n"
                ),
                published_at=None,
            )]
        return tools


class PlanToolTests(unittest.TestCase):
    def test_valid_plan_persists_and_grants_budget(self) -> None:
        workspace = _Workspace()
        tools = UnifiedTools(None, workspace.activate)
        provider = ScriptedProvider([
            {"thought": "复杂问题先规划", "action": "plan", "args": {"steps": [
                {"key": "collect_filings", "purpose": "收资料"},
                {"key": "extract_financials", "purpose": "抽事实"},
            ]}},
            {"thought": "执行", "action": "collect_filings", "args": {}},
            {"thought": "完成", "action": "final", "answer": "规划完成"},
        ])
        outcome = run_agent_turn(provider, workspace.project, "深入研究这家公司", tools, max_steps=4)
        self.assertIsNotNone(outcome.plan_steps)
        run = workspace.workflow.runs[workspace.run.id]
        # activate 预置计划 version=1，plan 工具按模型计划重写后 version=2
        self.assertEqual(run.plan_version, 2)
        self.assertEqual([step.step_key for step in run.steps], ["collect_filings", "extract_financials"])
        # 预算授予：4 + 12
        self.assertEqual(tools.budget, PLAN_STEP_BONUS)
        events = [event.event_type for event in workspace.workflow.event_log.for_run(workspace.run.id)]
        self.assertIn("run/planned", events)

    def test_plan_with_unknown_key_is_guided(self) -> None:
        workspace = _Workspace()
        tools = UnifiedTools(None, workspace.activate)
        observation = tools.run("plan", {"steps": [{"key": "bogus_step", "purpose": "?"}]}, "问题")
        self.assertIn("未知步骤", observation.text)
        self.assertIn("collect_filings", observation.text)

    def test_plan_refused_after_research_started(self) -> None:
        workspace = _Workspace()
        tools = UnifiedTools(None, workspace.activate)
        tools.run("plan", {"steps": [{"key": "collect_filings", "purpose": "收"}]}, "问题")
        tools.run("collect_filings", {}, "问题")
        observation = tools.run("plan", {"steps": [{"key": "extract_financials", "purpose": "抽"}]}, "问题")
        self.assertIn("研究计划已确定", observation.text)


class LazyRunTests(unittest.TestCase):
    def test_simple_question_creates_no_run(self) -> None:
        workspace = _Workspace()
        tools = UnifiedTools(CompanyTools(workspace.project), workspace.activate)
        provider = ScriptedProvider([
            {"thought": "直接回答", "action": "final", "answer": "资料不足，建议先研究。"},
        ])
        outcome = run_agent_turn(provider, workspace.project, "现金流怎么样？", tools)
        self.assertEqual(outcome.answer, "资料不足，建议先研究。")
        self.assertIsNone(outcome.report)
        self.assertFalse(tools.research_activated)
        self.assertEqual(len(workspace.workflow.runs), 0, "纯聊天不得创建 run")

    def test_research_tool_activates_run_once(self) -> None:
        workspace = _Workspace()
        tools = UnifiedTools(None, workspace.activate)
        provider = ScriptedProvider([
            {"action": "collect_filings", "args": {}},
            {"action": "collect_filings", "args": {}},
            {"action": "final", "answer": "已收录"},
        ])
        run_agent_turn(provider, workspace.project, "研究", tools)
        self.assertTrue(tools.research_activated)
        self.assertEqual(len(workspace.workflow.runs), 1, "多次研究工具调用只建一个 run")

    def test_research_activation_without_documents_guides_model(self) -> None:
        workspace = _Workspace(with_documents=False)
        tools = UnifiedTools(None, workspace.activate)
        provider = ScriptedProvider([
            {"action": "collect_filings", "args": {}},
            {"action": "final", "answer": "没有可用资料，请先登记来源。"},
        ])
        outcome = run_agent_turn(provider, workspace.project, "研究", tools)
        self.assertIn("没有可用资料", outcome.steps[0]["observation"])


class AgentEventStreamTests(unittest.TestCase):
    """on_event 逐步推送 step_started/step_completed 供 UI 流式显示。"""

    def test_on_event_receives_started_and_completed(self) -> None:
        workspace = _Workspace()
        tools = UnifiedTools(None, workspace.activate)
        provider = ScriptedProvider([
            {"thought": "先收资料", "action": "collect_filings", "args": {}},
            {"thought": "回答", "action": "final", "answer": "完成"},
        ])
        events: list[dict] = []
        run_agent_turn(provider, workspace.project, "研究", tools, max_steps=3, on_event=events.append)
        self.assertEqual([event["type"] for event in events], ["step_started", "step_completed"])
        self.assertEqual(events[0]["tool"], "collect_filings")
        self.assertEqual(events[0]["ref"], "O1")
        self.assertEqual(events[0]["thought"], "先收资料")
        self.assertIn("observation", events[1])

    def test_on_event_failure_does_not_break_loop(self) -> None:
        def broken(event: dict) -> None:
            raise RuntimeError("stream broken")

        workspace = _Workspace()
        tools = UnifiedTools(None, workspace.activate)
        provider = ScriptedProvider([
            {"action": "collect_filings", "args": {}},
            {"action": "final", "answer": "完成"},
        ])
        outcome = run_agent_turn(provider, workspace.project, "研究", tools, max_steps=3, on_event=broken)
        self.assertEqual(outcome.answer, "完成")


class ChatResearchTests(unittest.TestCase):
    """聊天消息直接触发完整研究：报告落库 + report_card。"""

    def test_chat_message_produces_report_and_report_card(self) -> None:
        class ScriptedChatProvider(HeuristicLLMProvider):
            """继承 HeuristicLLMProvider：analyze_business 内部调用 analyze 走确定性实现。"""

            def __init__(self) -> None:
                self.calls = 0

            def chat_json(self, system: str, user: str) -> dict:
                self.calls += 1
                if self.calls == 1:
                    return {"thought": "规划", "action": "plan", "args": {"steps": [
                        {"key": "collect_filings", "purpose": "收"},
                        {"key": "extract_financials", "purpose": "抽"},
                        {"key": "analyze_business", "purpose": "析"},
                        {"key": "analyze_risks", "purpose": "险"},
                        {"key": "calculate_valuation", "purpose": "算"},
                        {"key": "review", "purpose": "审"},
                        {"key": "compile_report", "purpose": "报"},
                    ]}}
                if self.calls <= 8:
                    keys = ["collect_filings", "extract_financials", "analyze_business", "analyze_risks", "calculate_valuation", "review", "compile_report"]
                    return {"action": keys[self.calls - 2], "args": {}}
                return {"action": "final", "answer": "研究完成。"}

        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ), patch("stock_research.service.resolve_provider", return_value=ScriptedChatProvider()):
            created = chat_entry_payload({
                "name": "腾讯", "symbol": "00700", "content": "帮我深入研究这家公司的基本面",
                "document": "收入 100.0 百万港元\n净利润 12.0 百万港元\n经营活动现金流 30.0 百万港元\n资本开支 5.0 百万港元\n现金 50.0 百万港元\n债务 20.0 百万港元\n云业务增长强劲，主要风险为市场竞争和监管变化。\n",
            }, db_path=f"{directory}/research.sqlite3")
            # chat_entry_payload 的首条消息已是研究请求（脚本 provider 全程驱动）
            self.assertEqual(created["type"], "answer")
            self.assertIn("run_id", created)
            self.assertIn("report", created)
            detail = history_payload(f"/api/sessions/{created['session_id']}")
            cards = [m for m in detail["messages"] if m["message_type"] == "report_card"]
            self.assertEqual(len(cards), 1, "报告卡片必须落库")
            plan_messages = [m for m in detail["messages"] if m["message_type"] == "tool" and m["content"].get("tool") == "plan"]
            self.assertEqual(len(plan_messages), 1, "计划消息必须落库且前端可见")


class ChatTurnMidResearchFinalTests(unittest.TestCase):
    def test_mid_research_final_pauses_run_and_answers(self) -> None:
        """模型研究到一半直接 final：返回回答、run 暂停、不落报告。"""
        workspace = _Workspace()
        tools = UnifiedTools(CompanyTools(workspace.project), workspace.activate)
        provider = ScriptedProvider([
            {"action": "plan", "args": {"steps": [{"key": "collect_filings", "purpose": "收"}]}},
            {"action": "collect_filings", "args": {}},
            {"thought": "用户可能只是随口一问", "action": "final", "answer": "已收录资料，如需完整报告请继续。"},
        ])
        outcome = run_agent_turn(provider, workspace.project, "先看看资料", tools)
        self.assertIsNone(outcome.report)
        run = workspace.workflow.runs[workspace.run.id]
        self.assertEqual(run.status.value, "paused")


class ChatJobTests(unittest.TestCase):
    def test_kind_chat_job_executes_full_turn(self) -> None:
        provider = ScriptedProvider([
            {"action": "final", "answer": "你好！可以问我这家公司的具体情况，或说「研究这家公司」启动完整研究。"},
            {"action": "final", "answer": "当前使用的是 DeepSeek 的 deepseek-chat。"},
        ])
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3", "DEEPSEEK_API_KEY": "", "AI_STOCK_LLM_API_KEY": ""},
            clear=False,
        ), patch("stock_research.service.resolve_provider", return_value=provider):
            first = chat_entry_payload({"name": "腾讯", "symbol": "00700", "content": "你好"}, db_path=f"{directory}/research.sqlite3")
            store = SQLiteStore(f"{directory}/research.sqlite3")
            session_id = first["session_id"]
            job = create_and_enqueue(store, __import__("uuid").UUID(session_id), {
                "kind": "chat", "content": "当前用的什么模型？",
                "as_of_date": "2025-12-31", "llm": None, "document_urls": None,
                # 入队前未保存用户消息：worker 需代为落库
                "_session_message_saved": False,
            }, f"{directory}/research.sqlite3", queue=InlineQueue())
            store.close()
            result = execute_research_job(job["job_id"], f"{directory}/research.sqlite3")
            self.assertEqual(result["type"], "answer")
            reopened = SQLiteStore(f"{directory}/research.sqlite3")
            try:
                loaded = reopened.load_job(__import__("uuid").UUID(job["job_id"]))
                self.assertEqual(loaded["status"], "completed")
                # kind=chat 任务可能没有 run：run_id 允许为空
                detail = history_payload(f"/api/sessions/{session_id}")
                user_texts = [m["content"]["text"] for m in detail["messages"] if m["role"] == "user"]
                self.assertIn("当前用的什么模型？", user_texts)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
