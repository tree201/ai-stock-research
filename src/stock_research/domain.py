"""Domain models and invariants for research projects and runs.

This module intentionally has no network, database, or model dependency.  It
is the small, deterministic core that future adapters can build around.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(StrEnum):
    CREATED = "created"
    PLANNED = "planned"
    COLLECTING_DATA = "collecting_data"
    EXTRACTING_FACTS = "extracting_facts"
    ANALYZING = "analyzing"
    CALCULATING = "calculating"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    PAUSED = "paused"
    CANCELED = "canceled"
    FAILED = "failed"


_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.PLANNED, RunStatus.CANCELED}),
    RunStatus.PLANNED: frozenset({RunStatus.COLLECTING_DATA, RunStatus.PAUSED, RunStatus.CANCELED, RunStatus.FAILED}),
    RunStatus.COLLECTING_DATA: frozenset({RunStatus.EXTRACTING_FACTS, RunStatus.PAUSED, RunStatus.CANCELED, RunStatus.FAILED}),
    RunStatus.EXTRACTING_FACTS: frozenset({RunStatus.ANALYZING, RunStatus.PAUSED, RunStatus.CANCELED, RunStatus.FAILED}),
    RunStatus.ANALYZING: frozenset({RunStatus.CALCULATING, RunStatus.PAUSED, RunStatus.CANCELED, RunStatus.FAILED}),
    RunStatus.CALCULATING: frozenset({RunStatus.REVIEWING, RunStatus.PAUSED, RunStatus.CANCELED, RunStatus.FAILED}),
    RunStatus.REVIEWING: frozenset({RunStatus.COMPLETED, RunStatus.PAUSED, RunStatus.CANCELED, RunStatus.FAILED}),
    RunStatus.PAUSED: frozenset({
        RunStatus.PLANNED,
        RunStatus.COLLECTING_DATA,
        RunStatus.EXTRACTING_FACTS,
        RunStatus.ANALYZING,
        RunStatus.CALCULATING,
        RunStatus.REVIEWING,
        RunStatus.CANCELED,
        RunStatus.FAILED,
    }),
    RunStatus.FAILED: frozenset({RunStatus.PAUSED, RunStatus.CANCELED}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.CANCELED: frozenset(),
}


@dataclass(slots=True)
class ResearchProject:
    user_id: UUID
    company_id: UUID
    symbol: str
    name: str
    market: str = "HK"
    id: UUID = field(default_factory=uuid4)
    title: str | None = None
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class ResearchSession:
    """A durable conversation thread within one company research project."""

    project_id: UUID
    title: str = "长期研究"
    status: str = "active"
    id: UUID = field(default_factory=uuid4)
    active_run_id: UUID | None = None
    latest_event_at: datetime = field(default_factory=utc_now)
    last_event_type: str | None = None
    last_event_preview: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class SessionMessage:
    session_id: UUID
    role: str
    message_type: str
    content: dict[str, Any]
    run_id: UUID | None = None
    report_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class ResearchStep:
    run_id: UUID
    step_key: str
    order: int
    status: str = "pending"
    attempt: int = 0
    input_data: dict[str, Any] | None = None
    output_data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    id: UUID = field(default_factory=uuid4)
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(slots=True)
class ResearchRun:
    project_id: UUID
    question: str
    as_of_date: date
    run_type: str = "initial"
    session_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)
    status: RunStatus = RunStatus.CREATED
    plan_version: int = 0
    model_version: str | None = None
    steps: list[ResearchStep] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def transition(self, target: RunStatus) -> None:
        """Move to a valid state, enforcing the workflow invariant."""
        if target == self.status:
            return
        allowed = _TRANSITIONS[self.status]
        if target not in allowed:
            raise ValueError(f"invalid run transition: {self.status.value} -> {target.value}")
        self.status = target
        if target == RunStatus.COLLECTING_DATA and self.started_at is None:
            self.started_at = utc_now()
        if target == RunStatus.COMPLETED:
            self.completed_at = utc_now()
