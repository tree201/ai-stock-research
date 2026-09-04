from datetime import date, datetime, timezone
from uuid import uuid4
import unittest

from stock_research.calculations import cagr, dcf, free_cash_flow, net_cash, ratio
from stock_research.documents import RawDocument
from stock_research.domain import ResearchProject
from stock_research.facts import FactCandidate
from stock_research.pipeline import ResearchPipeline
from stock_research.report import diff_reports
from stock_research.llm import HeuristicLLMProvider


class CalculationTests(unittest.TestCase):
    def test_cagr_and_ratio(self) -> None:
        self.assertAlmostEqual(cagr(100, 121, 2).outputs["cagr"], 0.1)
        self.assertAlmostEqual(ratio(25, 100, "margin").outputs["margin"], 0.25)
        self.assertEqual(free_cash_flow(100, 30).outputs["free_cash_flow"], 70)
        self.assertEqual(net_cash(100, 40).outputs["net_cash"], 60)

    def test_dcf_has_reproducible_outputs(self) -> None:
        result = dcf(100, [0.1, 0.1], 0.1, 0.03, net_cash=50, shares=10)
        self.assertGreater(result.outputs["equity_value"], result.outputs["enterprise_value"])
        self.assertAlmostEqual(result.outputs["value_per_share"], result.outputs["equity_value"] / 10)


class PipelineTests(unittest.TestCase):
    def test_local_pipeline_produces_cited_report_and_completes_run(self) -> None:
        project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯（示例）")
        pipeline = ResearchPipeline(llm_provider=HeuristicLLMProvider())
        pipeline.workflow.create_project(project)
        document = RawDocument(
            company_id=project.company_id,
            source_type="local_fixture",
            source_url="fixture://tencent",
            title="sample",
            content=(
                "Revenue FY2024 HK$ 660.3 billion\n"
                "Net income FY2024 HK$ 194.1 billion\n"
                "Net cash generated from operating activities FY2024 HK$ 218.0 billion\n"
                "Business: cloud growth is important.\n"
                "Risk: competition remains intense."
            ),
            published_at=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        report = pipeline.run(
            project_id=project.id,
            question="研究示例公司",
            as_of_date=date(2025, 12, 31),
            documents=[document],
            dcf_assumptions={
                "base_fcf": 100,
                "growth_rates": [0.1, 0.1],
                "discount_rate": 0.1,
                "terminal_growth": 0.03,
            },
        )
        self.assertIn("markdown", report)
        self.assertGreaterEqual(len(report["facts"]), 3)
        self.assertEqual(sum(1 for item in report["calculations"] if item["calculation_type"] == "dcf"), 3)
        self.assertEqual(report["review"]["status"], "passed")
        self.assertTrue(report["llm_claims"])
        run = pipeline.workflow.runs[next(iter(pipeline.workflow.runs))]
        self.assertEqual(run.status.value, "completed")
        self.assertIn("line", report["markdown"])

    def test_pipeline_rejects_documents_published_after_cutoff(self) -> None:
        project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯（示例）")
        pipeline = ResearchPipeline()
        pipeline.workflow.create_project(project)
        future_document = RawDocument(
            company_id=project.company_id,
            source_type="local_fixture",
            source_url="fixture://future",
            title="future",
            content="Revenue FY2026 HK$ 1 billion",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        with self.assertRaises(ValueError):
            pipeline.run(
                project_id=project.id,
                question="研究示例公司",
                as_of_date=date(2025, 12, 31),
                documents=[future_document],
            )

    @staticmethod
    def _fact(metric: str, value: float, period_end: date | None, confidence: float = 0.92) -> FactCandidate:
        return FactCandidate(
            metric=metric,
            value=value,
            currency="HKD",
            unit=None,
            period_end=period_end,
            evidence_id=uuid4(),
            source_line=1,
            raw_text="fixture",
            confidence=confidence,
        )

    def test_calculate_uses_latest_period_fact_per_metric(self) -> None:
        older_revenue = self._fact("revenue", 100.0, date(2023, 12, 31))
        latest_revenue = self._fact("revenue", 200.0, date(2024, 12, 31))
        net_income = self._fact("net_income", 50.0, date(2024, 12, 31))
        for facts in ([older_revenue, latest_revenue, net_income], [latest_revenue, older_revenue, net_income]):
            calculations = ResearchPipeline._calculate(facts, None)
            margin = next(item for item in calculations if item.calculation_type == "net_margin")
            self.assertAlmostEqual(margin.outputs["net_margin"], 0.25)

    def test_calculate_excludes_low_confidence_facts(self) -> None:
        revenue = self._fact("revenue", 200.0, date(2024, 12, 31))
        low_confidence_income = self._fact("net_income", 50.0, date(2024, 12, 31), confidence=0.72)
        calculations = ResearchPipeline._calculate([revenue, low_confidence_income], None)
        self.assertFalse(any(item.calculation_type == "net_margin" for item in calculations))

    def test_pipeline_selects_evidence_within_budget_for_llm(self) -> None:
        from stock_research.context import ContextBuilder
        from stock_research.llm import AnalysisRequest, AnalysisResponse

        class _RecordingProvider:
            provider_name = "recording"
            model_name = "test"

            def __init__(self) -> None:
                self.request: AnalysisRequest | None = None

            def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
                self.request = request
                return AnalysisResponse((), self.provider_name, self.model_name)

        project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯（示例）")
        provider = _RecordingProvider()
        pipeline = ResearchPipeline(llm_provider=provider, context_builder=ContextBuilder(max_chars=2000))
        pipeline.workflow.create_project(project)
        document = RawDocument(
            company_id=project.company_id,
            source_type="local_fixture",
            source_url="fixture://long",
            title="long",
            content=(
                "Revenue FY2024 HK$ 660.3 billion\n"
                + "\n".join(f"Operating overview line {number} with ongoing business discussion" for number in range(120))
            ),
            published_at=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        pipeline.run(
            project_id=project.id,
            question="研究示例公司",
            as_of_date=date(2025, 12, 31),
            documents=[document],
        )
        self.assertIsNotNone(provider.request)
        self.assertGreater(len(provider.request.evidence), 0)
        self.assertLessEqual(sum(len(item["text"]) for item in provider.request.evidence), 2000)

    def test_pipeline_completes_with_low_confidence_warning(self) -> None:
        project = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="00700", name="腾讯（示例）")
        pipeline = ResearchPipeline(llm_provider=HeuristicLLMProvider())
        pipeline.workflow.create_project(project)
        document = RawDocument(
            company_id=project.company_id,
            source_type="local_fixture",
            source_url="fixture://partial",
            title="partial",
            content=(
                "Revenue FY2024 HK$ 660.3 billion\n"
                "Net income 194.1\n"
                "Business: cloud growth is important.\n"
                "Risk: competition remains intense."
            ),
            published_at=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        report = pipeline.run(
            project_id=project.id,
            question="研究示例公司",
            as_of_date=date(2025, 12, 31),
            documents=[document],
        )
        self.assertEqual(report["review"]["status"], "passed_with_warnings")
        self.assertTrue(report["review"]["warnings"])
        self.assertEqual(report["review"]["issues"], [])
        self.assertFalse(any(item["calculation_type"] == "net_margin" for item in report["calculations"]))
        self.assertIn("警告", report["markdown"])
        run = pipeline.workflow.runs[next(iter(pipeline.workflow.runs))]
        self.assertEqual(run.status.value, "completed")


class DiffReportsTests(unittest.TestCase):
    def test_diff_reports_detects_new_changed_and_valuation(self) -> None:
        previous = {
            "report_id": "r1",
            "facts": [{"metric": "revenue", "period_end": "2024-12-31", "value": 100.0, "citation": {}}],
            "calculations": [{"calculation_type": "dcf", "inputs": {"scenario": "base"}, "outputs": {"value_per_share": 10.0}}],
        }
        current = {
            "report_id": "r2",
            "facts": [
                {"metric": "revenue", "period_end": "2024-12-31", "value": 110.0, "citation": {}},
                {"metric": "net_income", "period_end": "2024-12-31", "value": 20.0, "citation": {}},
            ],
            "calculations": [{"calculation_type": "dcf", "inputs": {"scenario": "base"}, "outputs": {"value_per_share": 12.0}}],
        }
        diff = diff_reports(previous, current)
        self.assertEqual([fact["metric"] for fact in diff["new_facts"]], ["net_income"])
        self.assertEqual(
            diff["changed_facts"],
            [{"metric": "revenue", "period_end": "2024-12-31", "previous_value": 100.0, "current_value": 110.0}],
        )
        self.assertAlmostEqual(diff["valuation"]["change_pct"], 0.2)
        self.assertTrue(diff["conclusion_changed"])
        self.assertEqual(diff["previous_report_id"], "r1")

    def test_diff_reports_handles_missing_valuation_and_no_changes(self) -> None:
        report = {"report_id": "r1", "facts": [{"metric": "revenue", "period_end": "2024-12-31", "value": 100.0, "citation": {}}], "calculations": []}
        diff = diff_reports(report, {"report_id": "r2", "facts": list(report["facts"]), "calculations": []})
        self.assertEqual(diff["new_facts"], [])
        self.assertEqual(diff["changed_facts"], [])
        self.assertIsNone(diff["valuation"])
        self.assertFalse(diff["conclusion_changed"])


if __name__ == "__main__":
    unittest.main()
