"""Research orchestrator: ReAct loop over pipeline-step tools.

固定瀑布已移除——研究路径由模型在 ReAct 循环中自主决定：先收什么资料、
何时补抽取、要不要跑估值、何时出报告。每个管线步骤降级为可独立调用的
工具，内部保留确定性实现与 checkpoint（complete_step 照常落库），因此
断点续跑与 grounding 评测链路不受影响。

两个不可让渡的硬门禁（见 ResearchTools.run）：
- calculate_valuation 的算术必须走工具，模型不得心算；
- compile_report 出报告前必须已通过 review 门禁。

无 LLM 时由调用方传入脚本 provider（HeuristicOrchestratorProvider），
按固定顺序驱动同一循环，保证无 key 开发与测试的确定性。
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

from .calculations import CalculationResult
from .context import ContextBuilder
from .documents import DocumentIngestor, EvidenceChunk, RawDocument
from .facts import FactCandidate, FinancialFactExtractor
from .llm import AnalysisRequest, LLMError, LLMProvider, identity_directive
from .pipeline_support import (
    ToolError,
    chunk_from_row,
    claim_from_row,
    calculation_from_row,
    fact_from_row,
    run_calculations,
    run_review,
    signals_from_chunks,
)
from .report import ReportBuilder
from .trust import trust_label
from .workflow import ResearchWorkflow


STEP_HINTS = {
    "collect_filings": "收录资料并切块（documents 已由外部就绪；可多次调用增量补充）",
    "extract_financials": "从已收资料中抽取收入/利润/现金流等财务事实",
    "analyze_business": "分析业务与收入结构（LLM claims + 信号归纳）",
    "analyze_risks": "归纳风险与反方证据",
    "calculate_valuation": "用工具计算增长率/利润率/自由现金流/DCF 情景（禁止心算）",
    "review": "校验数字、引用和截止日期；不通过会列出问题",
    "compile_report": "汇总生成最终研究报告（必须最后调用，且必须先通过 review）",
}


class Observation:
    """一次步骤调用的结构化结果，text 面向模型。"""

    def __init__(self, tool: str, args: dict[str, Any], text: str) -> None:
        self.tool = tool
        self.args = args
        self.text = text


class ResearchTools:
    """管线步骤的工具化外壳，绑定一个 run。

    跨工具调用的工作区（chunks/facts/calculations/...）等价于原管线的
    局部变量；每步完成照常 complete_step 落 checkpoint。
    """

    def __init__(
        self,
        workflow: ResearchWorkflow,
        run_id: UUID,
        *,
        llm_provider: LLMProvider | None,
        dcf_assumptions: dict[str, float | list[float]] | None,
        question: str,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self.workflow = workflow
        self.run_id = run_id
        self.llm_provider = llm_provider
        self.dcf_assumptions = dcf_assumptions
        self.question = question
        self.context_builder = context_builder or ContextBuilder()
        self.ingestor = DocumentIngestor()
        self.report_builder = ReportBuilder()
        self.documents: list[RawDocument] = []
        self.trust_by_document: dict[str, str | None] = {}
        self.evidence_sources: dict[str, dict[str, Any]] = {}
        self.chunks: list[EvidenceChunk] = []
        self.facts: list[FactCandidate] = []
        self.calculations: list[CalculationResult] = []
        self.llm_claims: list[dict] = []
        self.review: dict | None = None
        self.report: dict | None = None
        self.usage: dict | None = None
        self._ingested_document_ids: set[str] = set()

    # -- catalog -----------------------------------------------------------

    def specs(self) -> list[dict[str, str]]:
        return [
            {"name": name, "description": hint, "args_example": "{}"}
            for name, hint in STEP_HINTS.items()
        ]

    def catalog(self) -> str:
        import json
        return json.dumps(self.specs(), ensure_ascii=False)

    # -- dispatch ----------------------------------------------------------

    def run(self, name: str, args: dict[str, Any]) -> Observation:
        handlers = {
            "collect_filings": self.collect_filings,
            "extract_financials": self.extract_financials,
            "analyze_business": self.analyze_business,
            "analyze_risks": self.analyze_risks,
            "calculate_valuation": self.calculate_valuation,
            "review": self.review_step,
            "compile_report": self.compile_report,
        }
        handler = handlers.get(name)
        if handler is None:
            return Observation(name, args, f"工具执行失败：未知步骤 {name}，可用步骤：{', '.join(handlers)}")
        completed = self.completed_steps
        # checkpoint 复用：已完成步骤不重跑（collect_filings 幂等增量，允许重入）。
        # 断点续跑时这一条保证不重复下载、不重复调模型。
        if name in completed and name != "collect_filings":
            return Observation(name, args, f"步骤 {name} 已在早前完成，产物已从 checkpoint 复用，无需重跑。")
        # 前置检查给出可行动的错误，而不是让模型对着堆栈猜
        if name in {"extract_financials", "analyze_business", "analyze_risks"} and not self.chunks:
            return Observation(name, args, "工具执行失败：还没有任何资料，请先调用 collect_filings。")
        if name == "calculate_valuation" and not self.facts and "extract_financials" not in completed:
            return Observation(name, args, "工具执行失败：还没有财务事实，请先调用 extract_financials。")
        if name == "compile_report" and self.review is None and "review" not in completed:
            return Observation(name, args, "工具执行失败：报告必须先通过 review 门禁，请先调用 review。")
        try:
            return handler(args)
        except ToolError as exc:
            return Observation(name, args, f"工具执行失败：{exc}")

    @property
    def completed_steps(self) -> set[str]:
        run = self.workflow.runs[self.run_id]
        return {step.step_key for step in run.steps if step.status == "completed"}

    def hydrate_completed(self) -> None:
        """断点续跑：把已完成步骤的 checkpoint 产物恢复到工作区。

        恢复后 run() 会跳过这些步骤（collect_filings 因增量语义也只会
        no-op），循环只补齐剩余步骤；没有 store 的内存 workflow 没有
        checkpoint 可言，直接返回。
        """
        store = self.workflow.store
        if store is None:
            return
        completed = self.completed_steps
        if not completed:
            return
        artifacts = store.load_run_artifacts(self.run_id)
        documents_by_id = {row["id"]: row for row in artifacts.get("documents", [])}
        if "collect_filings" in completed:
            self.chunks = [chunk_from_row(row) for row in artifacts.get("evidence", [])]
            self.trust_by_document = {row["id"]: row.get("trust") for row in artifacts.get("documents", [])}
            self.evidence_sources = {
                str(chunk.id): {
                    "source_url": documents_by_id.get(str(chunk.document_id), {}).get("source_url", ""),
                    "source_title": documents_by_id.get(str(chunk.document_id), {}).get("title", ""),
                    "page": chunk.page,
                    "source_class": documents_by_id.get(str(chunk.document_id), {}).get("source_class"),
                    "trust": documents_by_id.get(str(chunk.document_id), {}).get("trust"),
                }
                for chunk in self.chunks
            }
            self._ingested_document_ids = set(documents_by_id)
        if "extract_financials" in completed:
            self.facts = [fact_from_row(row) for row in artifacts.get("facts", [])]
        if "analyze_business" in completed:
            self.llm_claims = [claim_from_row(row) for row in artifacts.get("claims", [])]
        if "calculate_valuation" in completed:
            self.calculations = [calculation_from_row(row) for row in artifacts.get("calculations", [])]
        if "review" in completed:
            step = next((s for s in self.workflow.runs[self.run_id].steps if s.step_key == "review"), None)
            if step is not None:
                self.review = step.output_data or None

    # -- steps ---------------------------------------------------------------

    def collect_filings(self, args: dict[str, Any]) -> Observation:
        if not self.documents:
            raise ToolError("没有可用资料（documents 为空）")
        # 增量收录：只处理还没入过工作区的文档，重复调用是幂等 no-op
        new_documents = [document for document in self.documents if str(document.id) not in self._ingested_document_ids]
        if not new_documents:
            return Observation(
                "collect_filings", args,
                f"没有新增资料：现有 {len(self.documents)} 篇文档均已收录（累计 {len(self.chunks)} 个证据块）。如需补充资料，请先取得新文档。",
            )
        run = self.workflow.runs[self.run_id]
        self.workflow.start_step(self.run_id, "collect_filings")
        new_chunks = self.ingestor.chunk_many(new_documents)
        self.chunks.extend(new_chunks)
        documents_by_id = {document.id: document for document in new_documents}
        for chunk in new_chunks:
            document = documents_by_id.get(chunk.document_id)
            if document is None:
                continue
            self.trust_by_document[str(document.id)] = document.trust
            self.evidence_sources[str(chunk.id)] = {
                "source_url": document.source_url,
                "source_title": document.title,
                "page": chunk.page,
                "source_class": document.source_class,
                "trust": document.trust,
            }
        self._ingested_document_ids.update(str(document.id) for document in new_documents)
        if self.workflow.store:
            self.workflow.store.save_documents(run.id, new_documents)
            self.workflow.store.save_evidence_chunks(new_chunks)
        self.workflow.complete_step(self.run_id, "collect_filings", {"document_count": len(new_documents), "evidence_count": len(new_chunks)})
        return Observation("collect_filings", args, f"已收录 {len(new_documents)} 篇新资料，切出 {len(new_chunks)} 个证据块（累计 {len(self.chunks)} 块）。")

    def extract_financials(self, args: dict[str, Any]) -> Observation:
        self.workflow.start_step(self.run_id, "extract_financials")
        self.facts = FinancialFactExtractor().extract(self.chunks)
        if self.workflow.store:
            self.workflow.store.save_facts(self.run_id, self.facts)
        self.workflow.complete_step(self.run_id, "extract_financials", {"fact_count": len(self.facts)})
        metrics = sorted({fact.metric for fact in self.facts})
        body = f"抽取到 {len(self.facts)} 条财务事实" + (f"：{', '.join(metrics)}" if metrics else "（资料里没有识别出财务数字）") + "。"
        if any(fact.confidence < 0.8 for fact in self.facts):
            body += "部分事实置信度低于 0.8，计算时会被排除。"
        return Observation("extract_financials", args, body)

    def analyze_business(self, args: dict[str, Any]) -> Observation:
        run = self.workflow.runs[self.run_id]
        self.workflow.start_step(self.run_id, "analyze_business")
        if self.llm_provider and self.chunks:
            selected = self.context_builder.select(self.chunks, self.question)
            project = self.workflow.projects[run.project_id]
            evidence = tuple(
                {
                    "evidence_id": str(chunk.id),
                    "text": chunk.text,
                    "trust": trust_label(self.trust_by_document.get(str(chunk.document_id))),
                }
                for chunk in selected
            )
            try:
                analysis = self.llm_provider.analyze(
                    AnalysisRequest(self.question, run.as_of_date, evidence, project.name, project.symbol)
                )
            except LLMError as exc:
                # 内容风控/供应商临时故障不应炸掉整轮研究：降级为观察，
                # 模型可基于已抽取的事实继续 review + compile_report。
                if self.workflow.store:
                    self.workflow.store.save_claims(self.run_id, [])
                self.workflow.complete_step(self.run_id, "analyze_business", {"llm_claim_count": 0, "llm_error": str(exc)})
                return Observation("analyze_business", args, f"LLM 业务分析调用失败：{exc}。可跳过定性分析，基于已抽取的财务事实继续后续步骤。")
            self.usage = analysis.usage
            self.llm_claims = [
                {"category": claim.category, "text": claim.text, "evidence_ids": list(claim.evidence_ids), "confidence": claim.confidence, "counter_evidence_ids": list(claim.counter_evidence_ids), "provider": analysis.provider, "model": analysis.model}
                for claim in analysis.claims
            ]
        if self.workflow.store:
            self.workflow.store.save_claims(self.run_id, self.llm_claims)
        self.workflow.complete_step(self.run_id, "analyze_business", {"llm_claim_count": len(self.llm_claims), "llm_usage": self.usage})
        return Observation("analyze_business", args, f"业务分析完成：{len(self.llm_claims)} 条 LLM claims。")

    def analyze_risks(self, args: dict[str, Any]) -> Observation:
        signals = signals_from_chunks(self.chunks)
        self.workflow.start_step(self.run_id, "analyze_risks")
        self.workflow.complete_step(self.run_id, "analyze_risks", {"risk_signal_count": len(signals.get("risks", [])), "claim_count": len(self.llm_claims)})
        return Observation("analyze_risks", args, f"风险分析完成：归纳出 {len(signals.get('risks', []))} 条风险信号。")

    def calculate_valuation(self, args: dict[str, Any]) -> Observation:
        self.workflow.start_step(self.run_id, "calculate_valuation")
        self.calculations = run_calculations(self.facts, self.dcf_assumptions)
        if self.workflow.store:
            self.workflow.store.save_calculations(self.run_id, self.calculations)
        self.workflow.complete_step(self.run_id, "calculate_valuation", {"calculation_count": len(self.calculations)})
        kinds = [calc.calculation_type for calc in self.calculations]
        body = f"计算完成 {len(self.calculations)} 项" + (f"：{', '.join(kinds)}" if kinds else "（缺少可计算的财务事实）") + "。"
        return Observation("calculate_valuation", args, body)

    def review_step(self, args: dict[str, Any]) -> Observation:
        run = self.workflow.runs[self.run_id]
        self.workflow.start_step(self.run_id, "review")
        review = run_review(self.facts, self.calculations, run.as_of_date)
        self.review = review
        self.workflow.complete_step(self.run_id, "review", review)
        if review["status"] == "needs_review":
            return Observation("review", args, f"review 未通过：{review['issues']}。请补充资料或重新抽取后再试。")
        warnings = review.get("warnings")
        return Observation("review", args, f"review 通过" + (f"（警告：{warnings}）" if warnings else "，无警告") + "。现在可以调用 compile_report。")

    def compile_report(self, args: dict[str, Any]) -> Observation:
        run = self.workflow.runs[self.run_id]
        project = self.workflow.projects[run.project_id]
        if self.review is None or self.review.get("status") == "needs_review":
            return Observation("compile_report", args, "工具执行失败：review 未通过或尚未执行，不能出报告。")
        self.workflow.start_step(self.run_id, "compile_report")
        report = self.report_builder.build(
            company={"symbol": project.symbol, "name": project.name, "market": project.market},
            question=self.question,
            as_of_date=run.as_of_date,
            facts=self.facts,
            calculations=self.calculations,
            qualitative_signals=signals_from_chunks(self.chunks),
            llm_claims=self.llm_claims,
            review=self.review,
            evidence_sources=self.evidence_sources,
        )
        self.report = report
        self.workflow.complete_step(self.run_id, "compile_report", {"report_ready": True})
        return Observation("compile_report", args, "研究报告已生成。")


_ORCHESTRATOR_PROTOCOL = (
    "你是严谨的股票研究编排者，通过调用研究步骤工具完成一次公司研究。每轮只输出一个 JSON 对象：\n"
    '调用步骤：{"thought":"简短理由","action":"<步骤名>","args":{}}\n'
    '结束研究：{"thought":"简短理由","action":"final","answer":"面向用户的中文总结"}\n'
    "规则：\n"
    "1. 按公司情况自适应规划路径：金融股可跳过 DCF，资料不足可再次 collect_filings；\n"
    "2. 数值计算必须走 calculate_valuation 工具，禁止心算；\n"
    "3. compile_report 必须最后调用，且必须先通过 review；\n"
    "4. compile_report 成功后立即 final，不要重复调用已完成步骤。"
)


def run_research(
    provider: Any,
    project_name: str,
    project_symbol: str,
    question: str,
    tools: ResearchTools,
    max_steps: int = 16,
) -> dict[str, Any]:
    """ReAct 主循环：模型自主决定步骤顺序，返回 {answer, steps, report}。"""
    identity = identity_directive(project_name, project_symbol)
    system = f"{_ORCHESTRATOR_PROTOCOL}\n{identity}"
    transcript = ""
    steps: list[dict[str, Any]] = []
    for step_index in range(max_steps):
        user = (
            f"可用步骤：\n{tools.catalog()}\n\n"
            f"研究问题：{question}\n\n"
            + (f"已完成步骤：\n{transcript}\n" if transcript else "（尚未开始）\n")
            + ("步数已达上限：若报告未生成请先 review 再 compile_report，然后立即 final。" if step_index == max_steps - 1 else "请输出下一轮 JSON。")
        )
        decision = provider.chat_json(system, user)
        decision = decision if isinstance(decision, dict) else {}
        action = str(decision.get("action", ""))
        if action == "final":
            return _finalize(decision, steps, tools)
        args = decision.get("args")
        args = args if isinstance(args, dict) else {}
        observation = tools.run(action, args)
        ref = f"S{len(steps) + 1}"
        steps.append({"ref": ref, "tool": action, "thought": str(decision.get("thought", "")), "observation": observation.text})
        transcript += f"[{ref}] {action}：{observation.text}\n"
    decision = provider.chat_json(system, f"可用步骤：\n{tools.catalog()}\n\n研究问题：{question}\n\n已完成：\n{transcript}\n请立即结束（final）。")
    return _finalize(decision if isinstance(decision, dict) else {}, steps, tools)


def _finalize(decision: dict[str, Any], steps: list[dict[str, Any]], tools: ResearchTools) -> dict[str, Any]:
    answer = str(decision.get("answer", "")).strip()
    if tools.report is None:
        review = tools.review
        if review is not None and review.get("status") == "needs_review":
            # 资料质量问题走 ValueError（与旧管线契约一致，提示用户补资料），
            # 而非 LLMError（那是模型调用失败）。
            issues = "；".join(str(issue) for issue in review.get("issues", []))
            raise ValueError(f"research review failed: {issues}")
        raise LLMError("研究循环结束时没有生成报告")
    return {"answer": answer, "steps": steps, "report": tools.report}


class HeuristicOrchestratorProvider:
    """无 LLM 时的确定性编排：按预排脚本顺序驱动 run_research。

    与原固定瀑布顺序一致，保证无 key 开发与现有测试行为不变。
    """

    provider_name = "heuristic"
    model_name = "orchestrator-script-v1"

    def __init__(self) -> None:
        self._script = [
            {"action": "collect_filings"},
            {"action": "extract_financials"},
            {"action": "analyze_business"},
            {"action": "analyze_risks"},
            {"action": "calculate_valuation"},
            {"action": "review"},
            {"action": "compile_report"},
            {"action": "final", "answer": "研究完成。"},
        ]
        self._cursor = 0

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        decision = self._script[min(self._cursor, len(self._script) - 1)]
        self._cursor += 1
        return dict(decision)
