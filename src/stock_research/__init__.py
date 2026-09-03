"""Core domain primitives for the stock research agent."""

from .domain import ResearchProject, ResearchRun, ResearchSession, SessionMessage, RunStatus
from .documents import DocumentIngestor, HttpDocumentFetcher, PdfTextExtractor
from .facts import FactCandidate, FinancialFactExtractor
from .calculations import CalculationResult, cagr, dcf, free_cash_flow, net_cash, ratio
from .workflow import ResearchWorkflow
from .storage import SQLiteStore
from .llm import AnalysisClaim, AnalysisRequest, AnalysisResponse, DeepSeekProvider, HeuristicLLMProvider, LLMError, ModelNotConfiguredError, OpenAICompatibleProvider, provider_from_config, provider_from_env

__all__ = [
    "DocumentIngestor",
    "FactCandidate",
    "FinancialFactExtractor",
    "CalculationResult",
    "cagr",
    "dcf",
    "free_cash_flow",
    "net_cash",
    "ratio",
    "HttpDocumentFetcher",
    "PdfTextExtractor",
    "ResearchProject",
    "ResearchRun",
    "ResearchSession",
    "SessionMessage",
    "RunStatus",
    "ResearchWorkflow",
    "SQLiteStore",
    "AnalysisClaim",
    "AnalysisRequest",
    "AnalysisResponse",
    "DeepSeekProvider",
    "LLMError",
    "ModelNotConfiguredError",
    "HeuristicLLMProvider",
    "OpenAICompatibleProvider",
    "provider_from_env",
    "provider_from_config",
]
