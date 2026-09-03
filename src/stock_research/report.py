"""Report assembly from verified facts, calculations and evidence."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any, Iterable

from .calculations import CalculationResult
from .facts import FactCandidate


def _citation(fact: FactCandidate, evidence_sources: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    citation = {
        "evidence_id": str(fact.evidence_id),
        "source_line": fact.source_line,
        "raw_text": fact.raw_text,
        "confidence": fact.confidence,
    }
    citation.update((evidence_sources or {}).get(str(fact.evidence_id), {}))
    return citation


class ReportBuilder:
    def build(
        self,
        *,
        company: dict[str, str],
        question: str,
        as_of_date: date,
        facts: Iterable[FactCandidate],
        calculations: Iterable[CalculationResult],
        qualitative_signals: dict[str, list[dict[str, Any]]] | None = None,
        llm_claims: Iterable[dict[str, Any]] | None = None,
        review: dict[str, Any] | None = None,
        evidence_sources: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        facts = list(facts)
        calculations = list(calculations)
        fact_rows = [
            {
                "metric": fact.metric,
                "value": fact.value,
                "currency": fact.currency,
                "unit": fact.unit,
                "period_end": fact.period_end.isoformat() if fact.period_end else None,
                "confidence": fact.confidence,
                "citation": _citation(fact, evidence_sources),
            }
            for fact in facts
        ]
        report = {
            "company": company,
            "question": question,
            "as_of_date": as_of_date.isoformat(),
            "summary": self._summary(facts, calculations),
            "facts": fact_rows,
            "calculations": [asdict(calculation) for calculation in calculations],
            "qualitative_signals": qualitative_signals or {},
            "llm_claims": list(llm_claims or []),
            "review": review or {},
            "disclaimer": "本报告仅用于研究辅助，不构成个性化投资建议，也不承诺投资收益。",
        }
        report["markdown"] = self.to_markdown(report)
        return report

    @staticmethod
    def _summary(facts: list[FactCandidate], calculations: list[CalculationResult]) -> list[str]:
        summary: list[str] = []
        by_metric: dict[str, FactCandidate] = {}
        for fact in facts:
            if fact.confidence < 0.8:
                continue
            previous = by_metric.get(fact.metric)
            if previous is None or (fact.period_end or date.min) > (previous.period_end or date.min):
                by_metric[fact.metric] = fact
        if "revenue" in by_metric:
            summary.append(f"已从证据中确认收入数据：{by_metric['revenue'].value:,.0f} {by_metric['revenue'].currency or ''}。")
        if "net_income" in by_metric and "revenue" in by_metric:
            summary.append("报告包含净利润与收入的利润率分析。")
        if any(calculation.calculation_type == "dcf" for calculation in calculations):
            summary.append("报告包含基于明确假设的 DCF 情景估值；估值结果不代表确定价格目标。")
        if not summary:
            summary.append("当前证据不足以形成可靠的定量结论，需要补充资料。")
        return summary

    @staticmethod
    def to_markdown(report: dict[str, Any]) -> str:
        lines = [
            f"# {report['company']['name']} 研究报告",
            "",
            f"**研究问题：** {report['question']}",
            f"**截止日期：** {report['as_of_date']}",
            "",
            "## 核心摘要",
            "",
        ]
        lines.extend(f"- {item}" for item in report["summary"])
        lines.extend(["", "## 财务事实", "", "| 指标 | 数值 | 期间 | 置信度 | 来源 |", "|---|---:|---|---:|---|"])
        for fact in report["facts"]:
            value = f"{fact['value']:,.2f} {fact['currency'] or ''}".strip()
            location = f"page {fact['citation']['page']}" if fact['citation'].get('page') else f"line {fact['citation']['source_line']}"
            source_url = fact['citation'].get('source_url')
            source = f"[{location}]({source_url})" if source_url else location
            lines.append(f"| {fact['metric']} | {value} | {fact['period_end'] or '-'} | {fact['confidence']:.2f} | {source} |")
        lines.extend(["", "## 估值计算", ""])
        for calculation in report["calculations"]:
            lines.append(f"### {calculation['calculation_type']} ({calculation['formula_version']})")
            lines.append("")
            lines.append(f"输入：`{calculation['inputs']}`")
            lines.append("")
            lines.append(f"输出：`{calculation['outputs']}`")
            lines.append("")
        for category, signals in report.get("qualitative_signals", {}).items():
            if not signals:
                continue
            lines.extend([f"## {category}", ""])
            lines.extend(f"- {signal['text']}（line {signal['source_line']}）" for signal in signals)
            lines.append("")
        if report.get("llm_claims"):
            lines.extend(["## 模型分析结论", ""])
            for claim in report["llm_claims"]:
                lines.append(f"- [{claim['category']}] {claim['text']}（置信度 {claim['confidence']:.2f}）")
            lines.append("")
        lines.extend(["## 审计", "", f"状态：{report.get('review', {}).get('status', 'unknown')}", "", report["disclaimer"]])
        return "\n".join(lines)
