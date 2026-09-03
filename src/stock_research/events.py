"""Small append-only event log used by the in-memory workflow.

The interface is deliberately storage-agnostic.  A PostgreSQL implementation
can later preserve the same event shapes without changing the domain model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RunEvent:
    seq: int
    run_id: UUID
    event_type: str
    payload: dict[str, Any]
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InMemoryEventLog:
    def __init__(self) -> None:
        self._events: list[RunEvent] = []

    def append(self, run_id: UUID, event_type: str, payload: dict[str, Any], created_at: datetime) -> RunEvent:
        event = RunEvent(
            seq=len(self._events) + 1,
            run_id=run_id,
            event_type=event_type,
            payload=dict(payload),
            created_at=created_at,
        )
        self._events.append(event)
        return event

    def for_run(self, run_id: UUID) -> list[RunEvent]:
        return [event for event in self._events if event.run_id == run_id]

    def all(self) -> list[RunEvent]:
        return list(self._events)

