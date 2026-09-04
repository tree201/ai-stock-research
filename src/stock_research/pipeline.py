"""First end-to-end local research pipeline."""

from __future__ import annotations

from datetime import date
from typing import Callable, Iterable
from uuid import UUID, uuid4

from .calculations import CalculationResult, cagr, dcf, free_cash_flow, net_cash, ratio
from .context import ContextBuilder
from .documents import DocumentIngestor, RawDocument
from .facts import FactCandidate, FinancialFactExtractor
from .report import ReportBuilder
from .llm import AnalysisRequest, LLMProvider
from .workflow import ResearchWorkflow


class ResearchPipeline:
    def __init__(
        self,
        workflow: ResearchWorkflow | None = None,
        llm_provider: LLMProvider | None = None,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self.workflow = workflow or ResearchWorkflow()
        self.document_ingestor = DocumentIngestor()
        self.fact_extractor = FinancialFactExtractor()
        self.report_builder = ReportBuilder()
        self.llm_provider = llm_provider
        self.context_builder = context_builder or ContextBuilder()

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
    ) -> dict:
        run = self.workflow.create_run(project_id, question, as_of_date, run_type=run_type, session_id=session_id)
        self.workflow.plan(run.id)
        documents = [document for document in documents if not document.published_at or document.published_at.date() <= as_of_date]
        if not documents:
            raise ValueError("no documents available before as_of_date")

        self.workflow.start_next_step(run.id)
        chunks = self.document_ingestor.chunk_many(documents)
        documents_by_id = {document.id: document for document in documents}
        evidence_sources = {
            str(chunk.id): {
                "source_url": documents_by_id[chunk.document_id].source_url,
                "source_title": documents_by_id[chunk.document_id].title,
                "page": chunk.page,
            }
            for chunk in chunks
            if chunk.document_id in documents_by_id
        }
        if self.workflow.store:
            self.workflow.store.save_documents(run.id, documents)
            self.workflow.store.save_evidence_chunks(chunks)
        self.workflow.complete_step(run.id, "collect_filings", {"document_count": len(documents), "evidence_count": len(chunks)})

        self.workflow.start_next_step(run.id)
        facts = self.fact_extractor.extract(chunks)
        if self.workflow.store:
            self.workflow.store.save_facts(run.id, facts)
        self.workflow.complete_step(run.id, "extract_financials", {"fact_count": len(facts)})

        qualitative_signals = self._signals(chunks)
        llm_claims: list[dict] = []
        llm_usage: dict | None = None
        llm_evidence_count = 0
        if self.llm_provider:
            selected_chunks = self.context_builder.select(chunks, question)
            evidence = tuple({"evidence_id": str(chunk.id), "text": chunk.text} for chunk in selected_chunks)
            llm_evidence_count = len(evidence)
            analysis = self.llm_provider.analyze(AnalysisRequest(question, as_of_date, evidence))
            llm_usage = analysis.usage
            llm_claims = [
                {"category": claim.category, "text": claim.text, "evidence_ids": list(claim.evidence_ids), "confidence": claim.confidence, "counter_evidence_ids": list(claim.counter_evidence_ids), "provider": analysis.provider, "model": analysis.model}
                for claim in analysis.claims
            ]
        if self.workflow.store:
            self.workflow.store.save_claims(run.id, llm_claims)
        self.workflow.start_next_step(run.id)
        self.workflow.complete_step(run.id, "analyze_business", {"signal_count": len(qualitative_signals.get("business", [])), "llm_claim_count": len(llm_claims), "llm_evidence_count": llm_evidence_count, "llm_evidence_total": len(chunks), "llm_usage": llm_usage})

        self.workflow.start_next_step(run.id)
        self.workflow.complete_step(run.id, "analyze_risks", {"signal_count": len(qualitative_signals.get("risks", [])), "llm_claim_count": len(llm_claims)})

        self.workflow.start_next_step(run.id)
        calculations = self._calculate(facts, dcf_assumptions)
        if self.workflow.store:
            self.workflow.store.save_calculations(run.id, calculations)
        self.workflow.complete_step(run.id, "calculate_valuation", {"calculation_count": len(calculations)})

        self.workflow.start_next_step(run.id)
        review = self._review(facts, calculations, as_of_date)
        if review["status"] == "needs_review":
            raise ValueError(f"research review failed: {review['issues']}")
        self.workflow.complete_step(run.id, "review", review)

        self.workflow.start_next_step(run.id)
        project = self.workflow.projects[run.project_id]
        report = self.report_builder.build(
            company={"symbol": project.symbol, "name": project.name, "market": project.market},
            question=question,
            as_of_date=as_of_date,
            facts=facts,
            calculations=calculations,
            qualitative_signals=qualitative_signals,
            llm_claims=llm_claims,
            review=review,
            evidence_sources=evidence_sources,
        )
        self.workflow.complete_step(run.id, "compile_report", {"report_ready": True})
        self.workflow.finish(run.id)
        report["report_id"] = str(uuid4())
        report["run_id"] = str(run.id)
        report["plan_version"] = run.plan_version
        if report_transform is not None:
            report = report_transform(report)
        if self.workflow.store:
            self.workflow.store.save_report(run.id, report, UUID(report["report_id"]))
        return report

    @staticmethod
    def _calculate(facts: list[FactCandidate], assumptions: dict[str, float | list[float]] | None) -> list[CalculationResult]:
        # Low-confidence facts are excluded; among the remaining facts the
        # latest reported period wins, matching the report summary logic.
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

    @staticmethod
    def _signals(chunks) -> dict[str, list[dict]]:
        signals: dict[str, list[dict]] = {"business": [], "risks": []}
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

    @staticmethod
    def _review(facts: list[FactCandidate], calculations: list[CalculationResult], as_of_date: date) -> dict:
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
        return {"status": status, "issues": issues, "warnings": warnings, "fact_count": len(facts), "calculation_count": len(calculations)}
