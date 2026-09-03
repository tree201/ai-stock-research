"""Conservative extraction of common financial facts from evidence chunks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Iterable
from uuid import UUID

from .documents import EvidenceChunk


@dataclass(frozen=True, slots=True)
class FactCandidate:
    metric: str
    value: float
    currency: str | None
    unit: str | None
    period_end: date | None
    evidence_id: UUID
    source_line: int
    raw_text: str
    confidence: float


_METRICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("revenue", ("total revenue", "revenue", "营业收入", "营业额", "收入")),
    ("net_income", ("net income", "profit for the year", "profit attributable", "净利润", "年度溢利")),
    (
        "operating_cash_flow",
        (
            "net cash generated from operating activities",
            "net cash from operating activities",
            "经营活动产生的现金流量净额",
            "经营活动所得现金净额",
        ),
    ),
    ("cash", ("cash and cash equivalents", "cash and bank balances", "现金及现金等价物", "现金及银行结余")),
    ("debt", ("total borrowings", "interest-bearing debt", "borrowings", "总借款", "有息负债")),
    ("capital_expenditure", ("capital expenditure", "capital expenditures", "资本开支", "资本性支出")),
)

_NUMBER = r"(?:\(?[-+]?\d[\d,]*(?:\.\d+)?\)?)"
_NUMBER_RE = re.compile(_NUMBER)
_YEAR_RE = re.compile(r"(?:FY\s*)?(20\d{2})(?:\s*(?:年度|年|FY))?", re.IGNORECASE)
_CURRENCY_RE = re.compile(r"(?P<symbol>HK\$|US\$|RMB|CNY|USD|HKD|EUR|GBP|人民币|港币|美元)", re.IGNORECASE)

_UNIT_MULTIPLIER: dict[str, tuple[float, str]] = {
    "billion": (1_000_000_000, "billion"),
    "bn": (1_000_000_000, "billion"),
    "million": (1_000_000, "million"),
    "mn": (1_000_000, "million"),
    "thousand": (1_000, "thousand"),
    "百亿": (1_000_000_000, "billion"),
    "十亿": (1_000_000_000, "billion"),
    "亿元": (100_000_000, "hundred_million"),
    "亿": (100_000_000, "hundred_million"),
    "百万元": (1_000_000, "million"),
    "百万元": (1_000_000, "million"),
    "百万": (1_000_000, "million"),
    "千元": (1_000, "thousand"),
}


class FinancialFactExtractor:
    """Extract only unambiguous one-number metric lines.

    A financial table commonly contains several years on one row.  Rather
    than guessing which number belongs to which period, this extractor skips
    multi-number rows unless exactly one year and one amount are present.
    """

    def extract(self, chunks: Iterable[EvidenceChunk]) -> list[FactCandidate]:
        candidates: list[FactCandidate] = []
        for chunk in chunks:
            for offset, line in enumerate(chunk.text.splitlines()):
                candidate = self._extract_line(chunk, line, chunk.start_line + offset)
                if candidate is not None:
                    candidates.append(candidate)
        return candidates

    def _extract_line(self, chunk: EvidenceChunk, line: str, source_line: int) -> FactCandidate | None:
        normalized = line.strip()
        if not normalized:
            return None
        metric = self._find_metric(normalized)
        if metric is None:
            return None

        numbers = list(_NUMBER_RE.finditer(normalized))
        # A year is not an amount.  Remove year-like tokens before deciding
        # whether the line has exactly one financial value.
        amount_matches = [match for match in numbers if not (len(match.group().replace(",", "")) == 4 and match.group().startswith("20"))]
        if len(amount_matches) != 1:
            return None
        amount_match = amount_matches[0]
        raw_number = amount_match.group()
        value = self._parse_number(raw_number)
        if value is None:
            return None

        before = normalized[: amount_match.start()]
        after = normalized[amount_match.end() :]
        currency = self._find_currency(before, after)
        multiplier, unit = self._find_unit(before, after)
        value *= multiplier
        year_match = _YEAR_RE.search(normalized)
        period_end = date(int(year_match.group(1)), 12, 31) if year_match else None
        confidence = 0.92 if period_end and unit else 0.82 if period_end or unit else 0.72
        return FactCandidate(
            metric=metric,
            value=value,
            currency=currency,
            unit=unit,
            period_end=period_end,
            evidence_id=chunk.id,
            source_line=source_line,
            raw_text=normalized,
            confidence=confidence,
        )

    @staticmethod
    def _find_metric(line: str) -> str | None:
        lowered = line.casefold()
        matches: list[tuple[int, str]] = []
        for metric, labels in _METRICS:
            for label in labels:
                index = lowered.find(label.casefold())
                if index >= 0:
                    matches.append((index, metric))
        if not matches:
            return None
        # Prefer the longest label at the earliest location, avoiding the
        # generic Chinese “收入” label when “营业收入” is present.
        earliest = min(index for index, _metric in matches)
        options = [(len(label), metric) for metric, labels in _METRICS for label in labels if lowered.find(label.casefold()) == earliest]
        return max(options)[1]

    @staticmethod
    def _parse_number(raw: str) -> float | None:
        negative = raw.startswith("(") and raw.endswith(")")
        cleaned = raw.strip("()").replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            return None
        return -value if negative else value

    @staticmethod
    def _find_currency(before: str, after: str) -> str | None:
        match = _CURRENCY_RE.search(before[-12:] + " " + after[:12])
        if not match:
            return None
        normalized = match.group("symbol").upper()
        return {"HK$": "HKD", "US$": "USD", "人民币": "CNY", "港币": "HKD", "美元": "USD"}.get(normalized, normalized)

    @staticmethod
    def _find_unit(before: str, after: str) -> tuple[float, str | None]:
        window = (before[-24:] + " " + after[:24]).casefold()
        for unit, (multiplier, normalized) in sorted(_UNIT_MULTIPLIER.items(), key=lambda item: len(item[0]), reverse=True):
            if unit.casefold() in window:
                return multiplier, normalized
        return 1.0, None

