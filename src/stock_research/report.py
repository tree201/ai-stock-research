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


def _fact_key(fact: dict[str, Any]) -> tuple[str, str | None]:
    return (str(fact.get("metric", "")), fact.get("period_end"))


def _base_value_per_share(report: dict[str, Any]) -> float | None:
    for item in report.get("calculations", []):
        if item.get("calculation_type") == "dcf" and item.get("inputs", {}).get("scenario") == "base":
            return item.get("outputs", {}).get("value_per_share")
    return None


def diff_reports(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Compare an updated report against the report it refreshes.

    Facts are keyed by metric and period; the base-scenario DCF value per
    share stands in for the valuation.  Removed facts are intentionally not
    reported: an update run only ingests new documents, so absent old facts
    are expected rather than meaningful.
    """
    previous_facts = {_fact_key(fact): fact for fact in previous.get("facts", [])}
    current_facts = {_fact_key(fact): fact for fact in current.get("facts", [])}
    new_facts = [fact for key, fact in current_facts.items() if key not in previous_facts]
    changed_facts = [
        {
            "metric": key[0],
            "period_end": key[1],
            "previous_value": previous_facts[key]["value"],
            "current_value": fact["value"],
        }
        for key, fact in current_facts.items()
        if key in previous_facts and abs(float(previous_facts[key]["value"]) - float(fact["value"])) > 1e-9
    ]
    valuation: dict[str, Any] | None = None
    previous_vps = _base_value_per_share(previous)
    current_vps = _base_value_per_share(current)
    if previous_vps is not None and current_vps is not None and abs(previous_vps) > 1e-12:
        valuation = {
            "previous": previous_vps,
            "current": current_vps,
            "change_pct": (current_vps - previous_vps) / abs(previous_vps),
        }
    conclusion_changed = bool(new_facts or changed_facts or (valuation and abs(valuation["change_pct"]) > 0.01))
    return {
        "previous_report_id": previous.get("report_id"),
        "new_facts": new_facts,
        "changed_facts": changed_facts,
        "valuation": valuation,
        "conclusion_changed": conclusion_changed,
    }


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
        diff = report.get("diff")
        if diff:
            lines.extend(["## 与上一版差异", ""])
            new_facts = diff.get("new_facts", [])
            if new_facts:
                lines.append(f"- 新增事实 {len(new_facts)} 条：" + "、".join(f"{fact['metric']}（{fact.get('period_end') or '-'}）" for fact in new_facts))
            else:
                lines.append("- 无新增事实")
            for change in diff.get("changed_facts", []):
                lines.append(f"- {change['metric']}（{change.get('period_end') or '-'}）：{change['previous_value']:,.2f} → {change['current_value']:,.2f}")
            valuation = diff.get("valuation")
            if valuation:
                lines.append(f"- 基准情景每股价值：{valuation['previous']:,.4f} → {valuation['current']:,.4f}（{valuation['change_pct']:+.1%}）")
            lines.append(f"- 结论变化：{'是' if diff.get('conclusion_changed') else '否'}")
            lines.append("")
        lines.extend(["## 审计", "", f"状态：{report.get('review', {}).get('status', 'unknown')}"])
        review = report.get("review", {})
        for warning in review.get("warnings", []):
            lines.append(f"- 警告：{warning}")
        for issue in review.get("issues", []):
            lines.append(f"- 问题：{issue}")
        lines.extend(["", report["disclaimer"]])
        return "\n".join(lines)
