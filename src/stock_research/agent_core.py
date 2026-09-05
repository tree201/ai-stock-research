"""Unified ReAct agent core: one loop for chat exploration and deep research.

聊天追问与深度研究原本是两套重复的 ReAct 循环（agent.py / orchestrator.py），
重试、容错、finalize 各维护一份，工具互不相通。现在合并为单一循环：

- 工具统一注册：探索工具（查报告/搜新闻/行情/抓URL）+ 研究工具
  （收资料/抽取/分析/估值/审查/出报告）对模型同时可见；
- plan 是工具不是模式：模型觉得问题复杂时自主调用 plan 写研究计划
  （落库、前端可见、步数预算+12），不强制调用；
- run 懒创建：纯聊天不建 run；模型第一次调用研究工具或 plan 时才由
  research_factory 激活一个 run 落 checkpoint；
- 容错只维护一份：_ask 带 3 次重试（供应商瞬时空响应/网络抖动不炸轮次）。

硬门禁不变（在 ResearchTools.run 内强制）：
- calculate_valuation 的算术必须走工具，模型不得心算；
- compile_report 出报告前必须已通过 review 门禁。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID

from .domain import ResearchProject, RunStatus
from .llm import LLMError, identity_directive
from .tools import CompanyTools, Observation, render_tool_catalog

DEFAULT_MAX_STEPS = 24
PLAN_STEP_BONUS = 12
PLAN_TOOL_NAME = "plan"

EXPLORATION_TOOLS: tuple[str, ...] = ("query_report", "search_news", "get_quote", "fetch_filings")
RESEARCH_TOOLS: tuple[str, ...] = (
    "collect_filings",
    "extract_financials",
    "analyze_business",
    "analyze_risks",
    "calculate_valuation",
    "review",
    "compile_report",
)

_PLAN_DESCRIPTION = (
    "写下或更新研究计划（仅在判断问题需要完整研究时调用一次）："
    f'{{"thought":"理由","action":"plan","args":{{"steps":[{{"key":"collect_filings","purpose":"获取年报"}},'
    '{"key":"extract_financials","purpose":"抽取财务事实"}]}}}；'
    "key 必须来自研究工具名；计划确定后按计划逐步执行"
)


@dataclass
class AgentOutcome:
    """一轮统一 agent 循环的产物。"""

    answer: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)
    report: dict[str, Any] | None = None
    run_id: UUID | None = None
    plan_steps: list[dict[str, Any]] | None = None


class UnifiedTools:
    """统一工具分发器：探索 + 研究 + plan，研究侧惰性激活。

    company_tools 缺省时（纯研究管线入口）只暴露研究工具与 plan；
    research_factory 缺省时只暴露探索工具。两者都在时模型可自由穿插
    （例如研究途中查行情佐证）。
    """

    def __init__(
        self,
        company_tools: CompanyTools | None,
        research_factory: Callable[[str], Any | None] | None,
        *,
        research_only: bool = False,
    ) -> None:
        self.company_tools = company_tools
        self.research_factory = research_factory
        self.research_only = research_only
        self.research_tools: Any | None = None
        self.plan_calls = 0
        self.extra_budget = 0
        self.plan_steps: list[dict[str, Any]] | None = None

    # -- catalog -----------------------------------------------------------

    def specs(self) -> list[dict[str, str]]:
        catalog: list[dict[str, str]] = []
        if self.company_tools is not None:
            for spec in self.company_tools.specs():
                catalog.append({"name": spec["name"], "description": spec["description"], "args_example": spec.get("args", "{}")})
        from .orchestrator import STEP_HINTS

        for name, hint in STEP_HINTS.items():
            catalog.append({"name": name, "description": hint, "args_example": "{}"})
        catalog.append({"name": PLAN_TOOL_NAME, "description": _PLAN_DESCRIPTION, "args_example": '{"steps":[]}'})
        return catalog

    def catalog(self) -> str:
        return json.dumps(self.specs(), ensure_ascii=False)

    # -- dispatch ----------------------------------------------------------

    def run(self, name: str, args: dict[str, Any], question: str) -> Observation:
        if name == PLAN_TOOL_NAME:
            return self._apply_plan(args, question)
        if name in RESEARCH_TOOLS:
            research = self._ensure_research(question)
            if research is None:
                return Observation(
                    name, args,
                    "工具执行失败：研究环境尚未就绪（缺少可用资料）。请改用探索工具回答，或建议用户在「公司详情 → 资料」登记财报/公告链接。",
                )
            return research.run(name, args)
        if self.company_tools is not None and name in {spec["name"] for spec in self.company_tools.specs()}:
            return self.company_tools.run(name, args)
        available = [spec["name"] for spec in self.specs()]
        return Observation(name, args, f"工具执行失败：未知工具 {name}，可用工具：{', '.join(available)}")

    # -- lazy research activation -------------------------------------------

    def _ensure_research(self, question: str) -> Any | None:
        if self.research_tools is not None:
            return self.research_tools
        if self.research_factory is None:
            return None
        self.research_tools = self.research_factory(question)
        return self.research_tools

    @property
    def research_activated(self) -> bool:
        return self.research_tools is not None

    @property
    def budget(self) -> int:
        return self.extra_budget

    # -- plan ---------------------------------------------------------------

    def _apply_plan(self, args: dict[str, Any], question: str) -> Observation:
        research = self._ensure_research(question)
        if research is None:
            return Observation(PLAN_TOOL_NAME, args, "工具执行失败：研究环境尚未就绪，无法制定研究计划。")
        if self.plan_calls >= 1:
            return Observation(PLAN_TOOL_NAME, args, "工具执行失败：研究计划已确定，请按计划执行；如需调整可直接调用对应研究工具。")
        raw_steps = args.get("steps")
        raw_steps = raw_steps if isinstance(raw_steps, list) else []
        from .orchestrator import STEP_HINTS

        plan: list[tuple[str, str]] = []
        problems: list[str] = []
        for item in raw_steps:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            if key not in STEP_HINTS:
                problems.append(f"未知步骤 {key or '(空)'}")
                continue
            plan.append((key, str(item.get("purpose", STEP_HINTS[key]))))
        if problems:
            return Observation(
                PLAN_TOOL_NAME, args,
                f"工具执行失败：{'；'.join(problems)}。可用步骤：{', '.join(STEP_HINTS)}",
            )
        if not plan:
            return Observation(PLAN_TOOL_NAME, args, f"工具执行失败：steps 不能为空。可用步骤：{', '.join(STEP_HINTS)}")
        # 守卫：研究已启动（任一步骤 running/completed）后不允许 re-plan——
        # workflow.plan 会整体替换 run.steps，清空已完成 checkpoint。
        run = research.workflow.runs[research.run_id]
        if any(step.status in {"running", "completed"} for step in run.steps):
            return Observation(PLAN_TOOL_NAME, args, "工具执行失败：研究已开始执行，不能再改计划；请继续执行剩余步骤。")
        research.workflow.plan(run.id, tuple(plan))
        self.plan_calls += 1
        self.extra_budget += PLAN_STEP_BONUS
        self.plan_steps = [{"key": key, "purpose": purpose} for key, purpose in plan]
        return Observation(
            PLAN_TOOL_NAME, args,
            f"研究计划已确定（{len(plan)} 步）：{' → '.join(key for key, _ in plan)}。请按计划逐步执行。",
        )


_AGENT_PROTOCOL = (
    "你是严谨的股票研究助手，可以使用工具探索或完成完整研究。每轮只输出一个 JSON 对象：\n"
    '调用工具：{"thought":"简短理由","action":"<工具名>","args":{...}}\n'
    '给出最终回答：{"thought":"简短理由","action":"final","answer":"面向用户的中文回答"}\n'
    "规则：\n"
    "1. 简单问题（查行情、问报告里的数字、搜新闻）直接用探索工具，1-3 次调用后回答；\n"
    "2. 需要完整研究时先调用 plan 写下计划，再按计划调用研究步骤工具，出报告后回答；\n"
    "3. 数值计算必须走 calculate_valuation 工具，禁止心算；compile_report 必须先通过 review；\n"
    "4. 工具的观察结果以 [O1] [O2]… 编号提供，最终回答必须基于这些观察；\n"
    "5. 回答中引用观察时标注对应的 [O*] 编号，引用了网络/公告来源时在末尾用"
    " Markdown 列出参考来源（[标题](URL)）并标注可信度（白名单来源/未验证来源）；\n"
    "6. 不要编造数字；资料不足时明确说明；不要重复相同调用。"
)


def run_agent_turn(
    provider: Any,
    project: ResearchProject,
    question: str,
    tools: UnifiedTools,
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> AgentOutcome:
    """统一 ReAct 主循环：返回 AgentOutcome（answer + 可选 report/citations）。"""
    identity = identity_directive(project.name, project.symbol)
    system = f"{_AGENT_PROTOCOL}\n{identity}"
    transcript = ""
    steps: list[dict[str, Any]] = []
    citations: list[dict[str, str]] = []
    total_budget = max_steps + tools.budget
    while len(steps) < total_budget:
        user = (
            f"可用工具：\n{tools.catalog()}\n\n"
            f"用户问题：{question}\n\n"
            + (f"已完成的探索：\n{transcript}\n" if transcript else "（尚无探索结果）\n")
            + (
                "步数已达上限：若研究报告未生成请先 review 再 compile_report，然后立即给出最终回答。"
                if len(steps) == total_budget - 1
                else "请输出下一轮 JSON。"
            )
        )
        decision = _ask(provider, system, user)
        action = str(decision.get("action", ""))
        if action == "final":
            return _finalize(decision, steps, citations, tools, answer_override=str(decision.get("answer", "")).strip())
        args = decision.get("args")
        args = args if isinstance(args, dict) else {}
        observation = tools.run(action, args, question)
        # plan 授予额外预算（模型承诺了更长的路径）
        if action == PLAN_TOOL_NAME and observation.ok:
            total_budget = max_steps + tools.budget
        ref = f"O{len(steps) + 1}"
        step_record = {
            "ref": ref,
            "tool": action,
            "args": args,
            "observation": observation.text,
            "citations": observation.citations,
            "thought": str(decision.get("thought", "")),
        }
        steps.append(step_record)
        citations.extend({**citation, "observation": ref} for citation in observation.citations)
        transcript += f"[{ref}] 工具 {action}，参数 {json.dumps(args, ensure_ascii=False)}：\n{observation.text}\n\n"
        if on_step is not None:
            on_step(step_record)
    decision = _ask(
        provider,
        system,
        f"可用工具：\n{tools.catalog()}\n\n用户问题：{question}\n\n已完成的探索：\n{transcript}\n请立即给出最终回答。",
    )
    return _finalize(decision, steps, citations, tools, answer_override=str(decision.get("answer", "")).strip())


def _ask(provider: Any, system: str, user: str, attempts: int = 3) -> dict[str, Any]:
    """带重试的决策调用：供应商瞬时空响应/网络抖动不应炸掉整轮对话。

    每轮决策是无状态单轮调用（transcript 全量随 prompt 重发），重试幂等。
    """
    last: Exception | None = None
    for _ in range(attempts):
        try:
            decision = provider.chat_json(system, user)
            return decision if isinstance(decision, dict) else {}
        except LLMError as exc:
            last = exc
    raise LLMError(f"agent 循环连续 {attempts} 次调用模型失败：{last}")


def _finalize(
    decision: dict[str, Any],
    steps: list[dict[str, Any]],
    citations: list[dict[str, str]],
    tools: UnifiedTools,
    *,
    answer_override: str,
) -> AgentOutcome:
    answer = answer_override.strip()
    if tools.research_activated and tools.research_tools is not None:
        research = tools.research_tools
        if research.report is not None:
            if not answer:
                answer = "研究已完成，报告已生成。"
            return AgentOutcome(answer, steps, citations, report=research.report, run_id=research.run_id, plan_steps=tools.plan_steps)
        # 研究未完成（中途 final / review 未过）：暂停 run 保留恢复语义，
        # 后续"继续研究"可从 checkpoint 续跑。已暂停/终态时幂等跳过。
        try:
            research.workflow.pause(research.run_id)
        except ValueError:
            pass
        review = research.review
        if review is not None and review.get("status") == "needs_review":
            # 资料质量问题走 ValueError（与旧管线契约一致，提示用户补资料），
            # 而非 LLMError（那是模型调用失败）。
            issues = "；".join(str(issue) for issue in review.get("issues", []))
            raise ValueError(f"research review failed: {issues}")
    if not answer:
        raise LLMError("agent 未给出最终回答")
    return AgentOutcome(answer, steps, citations, plan_steps=tools.plan_steps)


def run_id_of(tools: UnifiedTools) -> UUID | None:
    """当前 UnifiedTools 激活的 run id（未激活时 None）。"""
    if tools.research_tools is not None:
        return tools.research_tools.run_id
    return None


def run_status_of(tools: UnifiedTools) -> RunStatus | None:
    if tools.research_tools is not None:
        return tools.research_tools.workflow.runs[tools.research_tools.run_id].status
    return None


# render_tool_catalog 从 tools 导入仅为兼容旧引用；新代码用 UnifiedTools.catalog。
_ = render_tool_catalog
