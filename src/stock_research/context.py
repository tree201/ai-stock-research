"""Budgeted evidence selection for model analysis (Context Builder).

Fact extraction runs over every evidence chunk, but a model prompt must stay
within a token budget.  The Context Builder ranks chunks by question
relevance and financial-signal density, greedily fills the budget, and
returns the selected chunks in original document order so prompts read
coherently and citations remain stable.
"""

from __future__ import annotations

import re
from typing import Sequence

from .documents import EvidenceChunk

DEFAULT_EVIDENCE_BUDGET = 48_000

_FINANCIAL_KEYWORDS: tuple[str, ...] = (
    "revenue", "收入", "net income", "profit", "利润", "溢利",
    "cash flow", "现金", "margin", "毛利", "borrowings", "负债", "借款",
    "capital expenditure", "资本开支", "dividend", "股息",
    "business", "业务", "growth", "增长", "risk", "风险",
    "competition", "竞争", "regulatory", "监管", "outlook", "展望",
)

_ASCII_WORD_RE = re.compile(r"[a-z][a-z0-9]+")
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


def _question_terms(question: str) -> tuple[str, ...]:
    lowered = question.casefold()
    words = tuple({word for word in _ASCII_WORD_RE.findall(lowered)})
    cjk = _CJK_CHAR_RE.findall(lowered)
    bigrams = tuple({cjk[index] + cjk[index + 1] for index in range(len(cjk) - 1)})
    return words + bigrams


class ContextBuilder:
    """Keep the evidence passed to a model within a character budget."""

    def __init__(self, max_chars: int = DEFAULT_EVIDENCE_BUDGET) -> None:
        if max_chars < 100:
            raise ValueError("max_chars must be at least 100")
        self.max_chars = max_chars

    def select(self, chunks: Sequence[EvidenceChunk], question: str) -> list[EvidenceChunk]:
        if not chunks:
            return []
        terms = _question_terms(question)
        scored = [(self._score(chunk.text, terms), index, chunk) for index, chunk in enumerate(chunks)]
        selected: list[tuple[int, EvidenceChunk]] = []
        used = 0
        for _score, index, chunk in sorted(scored, key=lambda item: (-item[0], item[1])):
            if used + len(chunk.text) > self.max_chars:
                continue
            selected.append((index, chunk))
            used += len(chunk.text)
        if not selected:
            # Every chunk alone exceeds the budget: fall back to the single
            # highest-scored chunk so analysis still receives context.
            best = max(scored, key=lambda item: item[0])
            selected.append((best[1], best[2]))
        return [chunk for _index, chunk in sorted(selected, key=lambda item: item[0])]

    @staticmethod
    def _score(text: str, terms: tuple[str, ...]) -> int:
        lowered = text.casefold()
        score = 0
        for term in terms:
            score += min(lowered.count(term), 5)
        for keyword in _FINANCIAL_KEYWORDS:
            if keyword in lowered:
                score += 2
        return score
