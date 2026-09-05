"""ReAct-style exploration loop for chat follow-ups.

模型在循环中自主决定调用哪个探索工具（查报告/搜新闻/拉行情/抓公告），
每一步的观察结果都带回上下文，最终回答必须基于观察并附来源引用。
探索轨迹作为会话记忆持久化，证据链供评测系统做 grounding 评测。
"""

from __future__ import annotations

import json
from typing import Any

from .domain import ResearchProject
from .llm import LLMError, identity_directive
from .tools import CompanyTools, render_tool_catalog

DEFAULT_MAX_STEPS = 4

_AGENT_PROTOCOL = (
    "你是严谨的股票研究助手，可以使用工具探索后回答。每轮只输出一个 JSON 对象：\n"
    '调用工具：{"thought":"简短理由","action":"<工具名>","args":{...}}\n'
    '给出最终回答：{"thought":"简短理由","action":"final","answer":"面向用户的中文回答"}\n'
    "规则：\n"
    "1. 工具的观察结果以 [O1] [O2]… 编号提供，最终回答必须基于这些观察；\n"
    "2. 回答中引用观察时标注对应的 [O*] 编号，引用了网络/公告来源时在末尾用"
    " Markdown 列出参考来源（[标题](URL)）并标注可信度（白名单来源/未验证来源）；\n"
    "3. 不要编造数字；资料不足时明确说明；\n"
    "4. 通常 1-3 次工具调用即可，不要重复相同调用。"
)


def run_exploration(
    provider: Any,
    project: ResearchProject,
    question: str,
    tools: CompanyTools,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> dict[str, Any]:
    """Run the ReAct loop and return {answer, steps, citations}."""
    identity = identity_directive(project.name, project.symbol)
    system = f"{_AGENT_PROTOCOL}\n{identity}"
    transcript = ""
    steps: list[dict[str, Any]] = []
    for step_index in range(max_steps):
        user = (
            f"可用工具：\n{render_tool_catalog(tools)}\n\n"
            f"用户问题：{question}\n\n"
            + (f"已完成的探索：\n{transcript}\n" if transcript else "（尚无探索结果）\n")
            + ("探索次数已达上限，必须现在给出最终回答。" if step_index == max_steps - 1 else "请输出下一轮 JSON。")
        )
        decision = _decide(provider, system, user)
        action = str(decision.get("action", ""))
        if action == "final":
            return _finalize(decision, steps)
        args = decision.get("args")
        args = args if isinstance(args, dict) else {}
        observation = tools.run(action, args)
        ref = f"O{len(steps) + 1}"
        steps.append({
            "ref": ref,
            "tool": action,
            "args": args,
            "observation": observation.text,
            "citations": observation.citations,
            "thought": str(decision.get("thought", "")),
        })
        transcript += f"[{ref}] 工具 {action}，参数 {json.dumps(args, ensure_ascii=False)}：\n{observation.text}\n\n"
    # 循环耗尽仍未 final：把最后的决定权交给一次强制 final 调用。
    decision = _decide(
        provider,
        system,
        f"可用工具：\n{render_tool_catalog(tools)}\n\n用户问题：{question}\n\n已完成的探索：\n{transcript}\n请立即给出最终回答。",
    )
    return _finalize(decision, steps)


def _decide(provider: Any, system: str, user: str) -> dict[str, Any]:
    raw = provider.chat_json(system, user)
    return raw if isinstance(raw, dict) else {}


def _finalize(decision: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    answer = str(decision.get("answer", "")).strip()
    if not answer:
        raise LLMError("agent 未给出最终回答")
    citations: list[dict[str, str]] = []
    for step in steps:
        for citation in step["citations"]:
            citations.append({**citation, "observation": step["ref"]})
    return {"answer": answer, "steps": steps, "citations": citations}
