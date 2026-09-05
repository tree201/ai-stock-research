"""Shared deterministic step implementations for pipeline/orchestrator.

从 ResearchPipeline 抽出的纯函数：信号归纳、事实选取、计算、review 门禁。
不依赖 workflow 状态，方便 ReAct 工具外壳与旧管线复用同一实现。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from .calculations import CalculationResult, cagr, dcf, free_cash_flow, net_cash, ratio
from .documents import EvidenceChunk
from .facts import FactCandidate


class ToolError(RuntimeError):
    """Raised when a research step cannot proceed; message is model-facing."""


def chunk_from_row(row: dict[str, Any]) -> EvidenceChunk:
    """把 evidence_chunks 表行还原成 EvidenceChunk（checkpoint 恢复用）。"""
    return EvidenceChunk(
        document_id=UUID(row["document_id"]),
        chunk_index=int(row["chunk_index"]),
        text=row["text"],
        start_line=int(row["start_line"]),
        end_line=int(row["end_line"]),
        published_at=datetime.fromisoformat(row["published_at"]) if row.get("published_at") else None,
        page=row.get("page"),
        section=row.get("section"),
        id=UUID(row["id"]),
    )


def fact_from_row(row: dict[str, Any]) -> FactCandidate:
    """把 facts 表行还原成 FactCandidate（checkpoint 恢复用）。"""
    return FactCandidate(
        metric=str(row["metric"]),
        value=float(row["value"]),
        currency=row.get("currency"),
        unit=row.get("unit"),
        period_end=date.fromisoformat(row["period_end"]) if row.get("period_end") else None,
        evidence_id=UUID(row["evidence_id"]),
        source_line=int(row["source_line"]),
        raw_text=str(row["raw_text"]),
        confidence=float(row["confidence"]),
    )


def calculation_from_row(row: dict[str, Any]) -> CalculationResult:
    """把 calculations 表行还原成 CalculationResult（checkpoint 恢复用）。"""
    return CalculationResult(row["calculation_type"], row["formula_version"], row["inputs"], row["outputs"])


def claim_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """把 claims 表行还原成 llm_claims 字典（checkpoint 恢复用）。"""
    return {
        "category": row.get("category", "general"),
        "text": row.get("text", ""),
        "evidence_ids": row.get("evidence_ids", []),
        "confidence": float(row.get("confidence", 0.0)),
        "counter_evidence_ids": row.get("counter_evidence_ids", []),
        "provider": row.get("provider"),
        "model": row.get("model"),
    }


def signals_from_chunks(chunks: list[EvidenceChunk]) -> dict[str, list[dict[str, Any]]]:
    signals: dict[str, list[dict[str, Any]]] = {"business": [], "risks": []}
    for chunk in chunks:
        for offset, line in enumerate(chunk.text.splitlines()):
            text = line.strip()
            lowered = text.casefold()
            item = {"text": text, "source_line": chunk.start_line + offset, "evidence_id": str(chunk.id)}
            if any(keyword in lowered for keyword in ("business", "业务", "cloud", "云业务", "growth", "增长")):
                signals["business"].append(item)
            if any(keyword in lowered for keyword in ("risk", "风险", "competition", "竞争", "regulatory", "监管")):
                signals["risks"].append(item)
    return signals


def run_calculations(
    facts: list[FactCandidate],
    assumptions: dict[str, float | list[float]] | None,
) -> list[CalculationResult]:
    # 低置信度事实被排除；剩余事实中最新报告期获胜，与报告摘要逻辑一致。
    by_metric: dict[str, FactCandidate] = {}
    for fact in facts:
        if fact.confidence < 0.8:
            continue
        previous = by_metric.get(fact.metric)
        if previous is None or (fact.period_end or date.min) > (previous.period_end or date.min):
            by_metric[fact.metric] = fact
    calculations: list[CalculationResult] = []
    revenue = by_metric.get("revenue")
    if revenue and isinstance(assumptions, dict) and "revenue_prior" in assumptions:
        calculations.append(cagr(float(assumptions["revenue_prior"]), revenue.value, float(assumptions.get("revenue_years", 1))))
    if revenue and by_metric.get("net_income"):
        calculations.append(ratio(by_metric["net_income"].value, revenue.value, "net_margin"))
    if revenue and by_metric.get("operating_cash_flow"):
        calculations.append(ratio(by_metric["operating_cash_flow"].value, revenue.value, "operating_cash_flow_margin"))
    if by_metric.get("operating_cash_flow") and by_metric.get("capital_expenditure"):
        calculations.append(free_cash_flow(by_metric["operating_cash_flow"].value, by_metric["capital_expenditure"].value))
    if by_metric.get("cash") and by_metric.get("debt"):
        calculations.append(net_cash(by_metric["cash"].value, by_metric["debt"].value))

    if assumptions and {"growth_rates", "discount_rate", "terminal_growth"}.issubset(assumptions):
        base_fcf = float(assumptions.get("base_fcf", next((c.outputs["free_cash_flow"] for c in calculations if c.calculation_type == "free_cash_flow"), 0)))
        if base_fcf <= 0:
            return calculations
        base_rates = [float(rate) for rate in assumptions["growth_rates"]]
        scenario_rates = {
            "bear": [rate * 0.7 for rate in base_rates],
            "base": base_rates,
            "bull": [rate * 1.2 for rate in base_rates],
        }
        default_net_cash = next((c.outputs["net_cash"] for c in calculations if c.calculation_type == "net_cash"), 0.0)
        for scenario, rates in scenario_rates.items():
            result = dcf(
                base_fcf=base_fcf,
                growth_rates=rates,
                discount_rate=float(assumptions["discount_rate"]),
                terminal_growth=float(assumptions["terminal_growth"]),
                net_cash=float(assumptions.get("net_cash", default_net_cash)),
                shares=float(assumptions["shares"]) if "shares" in assumptions else None,
            )
            result = CalculationResult(result.calculation_type, result.formula_version, {**result.inputs, "scenario": scenario}, result.outputs)
            calculations.append(result)
    return calculations


def run_review(
    facts: list[FactCandidate],
    calculations: list[CalculationResult],
    as_of_date: date,
) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    if not facts:
        issues.append("没有抽取到财务事实")
    if any(fact.period_end and fact.period_end > as_of_date for fact in facts):
        issues.append("事实期间晚于研究截止日期")
    low_confidence = [fact for fact in facts if fact.confidence < 0.8]
    if low_confidence:
        metrics = ", ".join(sorted({fact.metric for fact in low_confidence}))
        warnings.append(f"{len(low_confidence)} 条低置信度事实未参与计算（{metrics}）")
    for calculation in calculations:
        if not calculation.outputs:
            issues.append(f"计算无输出: {calculation.calculation_type}")
    if issues:
        status = "needs_review"
    elif warnings:
        status = "passed_with_warnings"
    else:
        status = "passed"
    return {"status": status, "issues": issues, "warnings": warnings, "checked_at": as_of_date.isoformat()}


def review_gate(review: dict[str, Any] | None, completed_steps: set[str]) -> None:
    """报告出炉前的硬门禁：review 必须已完成且通过。"""
    if review is None and "review" not in completed_steps:
        raise ToolError("报告必须先通过 review 门禁")
    if review is not None and review.get("status") == "needs_review":
        raise ToolError(f"review 未通过：{review.get('issues')}")
