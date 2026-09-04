"""Queue adapters and durable research job execution.

RQ is optional at import time so the local demo remains dependency-light. Set
``AI_STOCK_QUEUE=rq`` and ``REDIS_URL`` to enable the Redis-backed worker.
"""

from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from .storage import SQLiteStore


class QueueError(RuntimeError):
    pass


class ResearchQueue(Protocol):
    name: str
    def enqueue(self, job_id: UUID, db_path: str | Path) -> str: ...


MAX_JOB_ATTEMPTS = 3


def find_resumable_run(store: SQLiteStore, session_id: UUID, question: str) -> UUID | None:
    """Latest non-terminal run of this session whose question matches."""
    trimmed = question.strip()
    if not trimmed:
        return None
    for row in store.list_runs(session_id=session_id):
        if row["status"] in {"completed", "canceled"}:
            continue
        if str(row.get("question") or "").strip() == trimmed:
            return UUID(row["id"])
    return None


def execute_research_job(job_id: str, db_path: str) -> dict[str, Any]:
    """RQ target: load a durable job, execute it, and record its outcome."""
    store = SQLiteStore(db_path)
    try:
        job = store.load_job(UUID(job_id))
        job_uuid = UUID(job_id)
        store.update_job(job_uuid, status="running", attempts=int(job["attempts"]) + 1)
        store.append_session_event(UUID(job["session_id"]), "research/running", {"preview": "后台研究正在执行", "job_id": job_id}, datetime.now(timezone.utc))
        from .service import run_research_payload  # lazy import avoids service/jobs cycle
        payload = _attach_resume_run(store, job)
        result = run_research_payload(payload, db_path=db_path, session_id=UUID(job["session_id"]))
        store.update_job(job_uuid, status="completed", run_id=UUID(result["run_id"]))
        store.append_session_event(UUID(job["session_id"]), "research/completed", {"preview": "后台研究已完成", "job_id": job_id, "run_id": result["run_id"]}, datetime.now(timezone.utc))
        return result
    except Exception as exc:
        if "job" in locals():
            store.update_job(UUID(job_id), status="failed", error={"type": type(exc).__name__, "message": str(exc)})
            store.append_session_event(UUID(job["session_id"]), "research/failed", {"preview": f"后台研究失败：{exc}", "job_id": job_id}, datetime.now(timezone.utc))
        raise
    finally:
        store.close()


def _attach_resume_run(store: SQLiteStore, job: dict[str, Any]) -> dict[str, Any]:
    """Point the payload at a partially completed run from an earlier attempt."""
    payload = dict(job["payload"])
    try:
        session_id = UUID(str(job["session_id"]))
    except (TypeError, ValueError):
        return payload
    run_id = find_resumable_run(store, session_id, str(payload.get("question") or ""))
    if run_id is not None:
        payload["_resume_run_id"] = str(run_id)
    return payload


def recover_stuck_jobs(db_path: str | Path) -> dict[str, int]:
    """Re-execute jobs left queued/running by a dead process.

    Research payloads are durable in SQLite and the pipeline can resume a
    partially completed run, so re-execution is safe and idempotent at the
    artifact level.  Jobs that already exceeded ``MAX_JOB_ATTEMPTS`` are
    marked failed instead of retrying forever.
    """
    store = SQLiteStore(db_path)
    recovered = 0
    failed = 0
    try:
        active = store.list_jobs(active_only=True)
        for job in active:
            job_id = UUID(job["id"])
            attempts = int(job.get("attempts") or 0)
            if attempts >= MAX_JOB_ATTEMPTS:
                store.update_job(job_id, status="failed", error={"type": "JobRecoveryExhausted", "message": f"自动恢复 {attempts} 次后仍未完成，已停止重试"})
                failed += 1
                continue
            if job.get("queue_name") == "rq":
                try:
                    queue_id = RQQueue().enqueue(job_id, db_path)
                except QueueError:
                    execute_research_job(str(job_id), str(db_path))
                else:
                    store.set_job_queue_id(job_id, queue_id)
            else:
                execute_research_job(str(job_id), str(db_path))
            recovered += 1
    finally:
        store.close()
    return {"recovered": recovered, "failed": failed}


class InlineQueue:
    name = "inline"

    def enqueue(self, job_id: UUID, db_path: str | Path) -> str:
        execute_research_job(str(job_id), str(db_path))
        return str(job_id)


class RQQueue:
    name = "research"

    def __init__(self, redis_url: str | None = None, queue_name: str = "research") -> None:
        try:
            import redis
            from rq import Queue
        except ImportError as exc:
            raise QueueError("RQ mode requires the optional redis and rq packages") from exc
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
        self.queue_name = queue_name
        self._connection = redis.from_url(self.redis_url)
        self._queue = Queue(queue_name, connection=self._connection, default_timeout=900)
        self.name = queue_name

    def enqueue(self, job_id: UUID, db_path: str | Path) -> str:
        job = self._queue.enqueue(
            "stock_research.jobs.execute_research_job", str(job_id), str(db_path),
            job_timeout=900, result_ttl=86400, failure_ttl=604800,
        )
        return str(job.id)


def queue_from_env() -> ResearchQueue:
    if os.getenv("AI_STOCK_QUEUE", "inline").casefold() == "rq":
        return RQQueue()
    return InlineQueue()


def create_and_enqueue(store: SQLiteStore, session_id: UUID, payload: dict[str, Any], db_path: str | Path, queue: ResearchQueue | None = None) -> dict[str, str]:
    queue = queue or queue_from_env()
    job_id = uuid4()
    store.create_job(job_id, session_id, payload, queue_name=queue.name)
    store.append_session_event(session_id, "research/queued", {"preview": "已加入后台研究队列", "job_id": str(job_id)}, datetime.now(timezone.utc))
    try:
        queue_id = queue.enqueue(job_id, db_path)
        store.set_job_queue_id(job_id, queue_id)
    except Exception as exc:
        store.update_job(job_id, status="failed", error={"type": type(exc).__name__, "message": str(exc)})
        raise
    return {"job_id": str(job_id), "queue_job_id": queue_id}
