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
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID

from .domain import ResearchProject, RunStatus
from .llm import LLMError, identity_directive
from .tools import CompanyTools, Observation, render_tool_catalog

DEFAULT_MAX_STEPS = 24
PLAN_STEP_BONUS = 12
PLAN_TOOL_NAME = "plan"

# prompt 中保留观测原文的最近步数（对齐 Anthropic clear_tool_uses 默认值）；
# 更早步骤只留一行「调用记录 + 目的」，需要时模型重新调用工具。
KEEP_RECENT_OBSERVATIONS = 3
# 单条观测截断阈值：DeepSeek 网关实测 ~65KB 请求会直接断连（无 HTTP 响应），
# 单次超大工具返回同样会引爆，故对最新观测也设兜底。
MAX_OBSERVATION_CHARS = 16_000

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
                    "工具执行失败：研究环境尚未就绪（缺少可用资料）。可先用 fetch_filings 抓取公司财报/公告页面并以 save=true 登记资料，再重试本步骤。",
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
    "2. 需要完整研究时先调用 plan 写下计划，再按计划调用研究步骤工具，出报告后回答；"
    "若研究因资料不足失败，用 fetch_filings 抓取公司财报/公告页面并以 save=true 登记为资料，再重新研究；\n"
    "3. 数值计算必须走 calculate_valuation 工具，禁止心算；compile_report 必须先通过 review；\n"
    "4. 工具的观察结果以 [O1] [O2]… 编号提供，最终回答必须基于这些观察；\n"
    "5. 回答中引用观察时标注对应的 [O*] 编号，引用了网络/公告来源时在末尾用"
    " Markdown 列出参考来源（[标题](URL)）并标注可信度（白名单来源/未验证来源）；\n"
    "6. 不要编造数字；资料不足时明确说明；不要重复相同调用。"
)


def _compact_transcript(steps: list[dict[str, Any]]) -> str:
    """拼 prompt 的探索记录压缩：最近 N 步保留观测原文，更早的只留一行目的。

    观测原文是 prompt 的 token 大头，且信号寿命只有一轮（下一步决策看过
    就不再需要；报告引用走 steps 存储的全文）。全量携带会把 prompt 滚过
    供应商网关的断连阈值（DeepSeek 实测 ~65KB 必挂）。调用记录与目的必须
    留档，否则模型不知道查过什么，会重复调用或凭空编造已有结论。
    """
    if not steps:
        return ""
    keep_from = max(0, len(steps) - KEEP_RECENT_OBSERVATIONS)
    lines: list[str] = []
    for index, step in enumerate(steps):
        header = f"[{step['ref']}] 工具 {step['tool']}，参数 {json.dumps(step['args'], ensure_ascii=False)}"
        thought = str(step.get("thought", "")).strip()
        if index < keep_from:
            purpose = f"，目的：{thought}" if thought else ""
            lines.append(f"{header}{purpose}（结果已丢弃，需要时重新调用工具）")
            continue
        observation = str(step.get("observation", ""))
        if len(observation) > MAX_OBSERVATION_CHARS:
            observation = (
                observation[:MAX_OBSERVATION_CHARS]
                + f"\n…（观测过长已截断，丢弃 {len(observation) - MAX_OBSERVATION_CHARS} 字符；需要完整数据请重新调用工具）"
            )
        lines.append(f"{header}：\n{observation}")
    return "\n\n".join(lines)


def run_agent_turn(
    provider: Any,
    project: ResearchProject,
    question: str,
    tools: UnifiedTools,
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    on_step: Callable[[dict[str, Any]], None] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> AgentOutcome:
    """统一 ReAct 主循环：返回 AgentOutcome（answer + 可选 report/citations）。

    on_event 逐步推送执行进度供 UI 流式显示（step_started / step_completed），
    推送失败不影响主流程；on_step 保持每步落库语义不变。
    """
    identity = identity_directive(project.name, project.symbol)
    system = f"{_AGENT_PROTOCOL}\n{identity}"

    def _emit(event: dict[str, Any]) -> None:
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception:
            pass

    steps: list[dict[str, Any]] = []
    citations: list[dict[str, str]] = []
    total_budget = max_steps + tools.budget
    while len(steps) < total_budget:
        # 每轮从 steps 重新拼装（而非累积字符串）：历史观测按预算折叠，
        # prompt 大小有上界，长探索不再滚爆供应商网关。
        transcript = _compact_transcript(steps)
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
        ref = f"O{len(steps) + 1}"
        thought = str(decision.get("thought", ""))
        _emit({"type": "step_started", "ref": ref, "tool": action, "args": args, "thought": thought})
        observation = tools.run(action, args, question)
        # plan 授予额外预算（模型承诺了更长的路径）
        if action == PLAN_TOOL_NAME and observation.ok:
            total_budget = max_steps + tools.budget
        step_record = {
            "ref": ref,
            "tool": action,
            "args": args,
            "observation": observation.text,
            "citations": observation.citations,
            "thought": thought,
        }
        steps.append(step_record)
        citations.extend({**citation, "observation": ref} for citation in observation.citations)
        if on_step is not None:
            on_step(step_record)
        _emit({
            "type": "step_completed", "ref": ref, "tool": action,
            "observation": observation.text[:400],
            "citations": observation.citations,
        })
    decision = _ask(
        provider,
        system,
        f"可用工具：\n{tools.catalog()}\n\n用户问题：{question}\n\n已完成的探索：\n{_compact_transcript(steps)}\n请立即给出最终回答。",
    )
    return _finalize(decision, steps, citations, tools, answer_override=str(decision.get("answer", "")).strip())


_RETRY_BASE_SECONDS = 0.5
_RETRY_MAX_SECONDS = 30.0


def _backoff_delay(attempt: int, retry_after: float | None) -> float:
    """指数退避 + 随机抖动；服务端 Retry-After 建议优先（封顶防死等）。"""
    if retry_after is not None:
        return min(retry_after, _RETRY_MAX_SECONDS)
    return min(_RETRY_BASE_SECONDS * 2**attempt, _RETRY_MAX_SECONDS) * random.uniform(1.0, 1.25)


def _ask(provider: Any, system: str, user: str, attempts: int = 3) -> dict[str, Any]:
    """带指数退避的决策调用（对齐 OpenAI/Anthropic SDK 默认重试策略）。

    瞬态错误（429 限流/5xx/网络瞬断）按指数退避重试，429 遵循服务端
    Retry-After；确定性错误（鉴权/参数/内容风控等）重试无意义，立即失败。
    每轮决策是无状态单轮调用（transcript 全量随 prompt 重发），重试幂等。
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            decision = provider.chat_json(system, user)
            return decision if isinstance(decision, dict) else {}
        except LLMError as exc:
            last = exc
            if not getattr(exc, "retryable", True):
                break
            if attempt + 1 < attempts:
                time.sleep(_backoff_delay(attempt, getattr(exc, "retry_after", None)))
    raise LLMError(f"agent 循环连续 {attempt + 1} 次调用模型失败：{last}")


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
