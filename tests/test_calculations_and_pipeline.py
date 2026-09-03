from datetime import date, datetime, timezone
from uuid import uuid4
import unittest

from stock_research.calculations import cagr, dcf, free_cash_flow, net_cash, ratio
from stock_research.documents import RawDocument
from stock_research.domain import ResearchProject
from stock_research.pipeline import ResearchPipeline
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


if __name__ == "__main__":
    unittest.main()
