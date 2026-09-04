"""SQLite persistence for the first production-shaped workflow.

The store mirrors the domain model without making the domain depend on
SQLite.  PostgreSQL can replace this adapter later while keeping the same
workflow calls.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .domain import ResearchProject, ResearchRun, ResearchSession, ResearchStep, RunStatus, SessionMessage
from .documents import RawDocument, EvidenceChunk
from .facts import FactCandidate
from .calculations import CalculationResult
from .events import RunEvent
from .trust import host_in_set, normalize_host


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class SQLiteStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, company_id TEXT NOT NULL,
                symbol TEXT NOT NULL, name TEXT NOT NULL, market TEXT NOT NULL,
                title TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                session_id TEXT REFERENCES sessions(id),
                question TEXT NOT NULL, as_of_date TEXT NOT NULL, run_type TEXT NOT NULL,
                status TEXT NOT NULL, plan_version INTEGER NOT NULL, model_version TEXT,
                created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS steps (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                step_key TEXT NOT NULL, step_order INTEGER NOT NULL, status TEXT NOT NULL,
                attempt INTEGER NOT NULL, input_json TEXT, output_json TEXT, error_json TEXT,
                started_at TEXT, completed_at TEXT, UNIQUE(run_id, step_key)
            );
            CREATE TABLE IF NOT EXISTS run_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, company_id TEXT NOT NULL, source_type TEXT NOT NULL,
                source_url TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL,
                content_hash TEXT NOT NULL, published_at TEXT, period_start TEXT,
                period_end TEXT, language TEXT
            );
            CREATE TABLE IF NOT EXISTS evidence_chunks (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                chunk_index INTEGER NOT NULL, text TEXT NOT NULL, start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL, published_at TEXT, page INTEGER, section TEXT
            );
            CREATE TABLE IF NOT EXISTS run_documents (
                run_id TEXT NOT NULL REFERENCES runs(id),
                document_id TEXT NOT NULL REFERENCES documents(id),
                PRIMARY KEY(run_id, document_id)
            );
            CREATE TABLE IF NOT EXISTS company_sources (
                id TEXT PRIMARY KEY, company_id TEXT NOT NULL,
                url TEXT NOT NULL, title TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(company_id, url)
            );
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                metric TEXT NOT NULL, value REAL NOT NULL, currency TEXT, unit TEXT,
                period_end TEXT, evidence_id TEXT NOT NULL, source_line INTEGER NOT NULL,
                raw_text TEXT NOT NULL, confidence REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS calculations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                calculation_type TEXT NOT NULL, formula_version TEXT NOT NULL,
                inputs_json TEXT NOT NULL, outputs_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                category TEXT NOT NULL, text TEXT NOT NULL, evidence_ids_json TEXT NOT NULL,
                confidence REAL NOT NULL, counter_evidence_ids_json TEXT NOT NULL,
                provider TEXT, model TEXT
            );
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), version INTEGER NOT NULL,
                payload_json TEXT NOT NULL, markdown TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(run_id, version)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                title TEXT NOT NULL, status TEXT NOT NULL, active_run_id TEXT,
                latest_event_at TEXT NOT NULL, last_event_type TEXT, last_event_preview TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS session_messages (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                role TEXT NOT NULL, message_type TEXT NOT NULL, content_json TEXT NOT NULL,
                run_id TEXT, report_id TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS session_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id),
                event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                run_id TEXT REFERENCES runs(id), payload_json TEXT NOT NULL,
                status TEXT NOT NULL, queue_name TEXT NOT NULL, rq_job_id TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, error_json TEXT,
                created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tags (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS project_tags (
                project_id TEXT NOT NULL REFERENCES projects(id),
                tag_id TEXT NOT NULL REFERENCES tags(id),
                PRIMARY KEY(project_id, tag_id)
            );
            CREATE TABLE IF NOT EXISTS trusted_hosts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                host TEXT NOT NULL UNIQUE,
                label TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS llm_providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_url TEXT NOT NULL,
                api_key TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                builtin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS llm_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id INTEGER NOT NULL REFERENCES llm_providers(id) ON DELETE CASCADE,
                model_id TEXT NOT NULL,
                display_name TEXT,
                thinking_levels TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                UNIQUE(provider_id, model_id)
            );
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(runs)").fetchall()}
        if "session_id" not in columns:
            self.connection.execute("ALTER TABLE runs ADD COLUMN session_id TEXT REFERENCES sessions(id)")
        document_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(documents)").fetchall()}
        if "source_class" not in document_columns:
            self.connection.execute("ALTER TABLE documents ADD COLUMN source_class TEXT")
        if "trust" not in document_columns:
            self.connection.execute("ALTER TABLE documents ADD COLUMN trust TEXT")
        source_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(company_sources)").fetchall()}
        if "source_class" not in source_columns:
            self.connection.execute("ALTER TABLE company_sources ADD COLUMN source_class TEXT DEFAULT 'private'")
        self._backfill_document_trust()
        self._seed_trusted_hosts()
        self._seed_llm_catalog()
        self.connection.commit()

    # Legacy documents predate trust classification; backfill them once from
    # their source_type so old reports keep meaningful badges.
    _PRIVATE_SOURCE_TYPES = ("user_text", "local_fixture", "local_file")
    _PUBLIC_SOURCE_TYPES = ("hkex_filing", "company_ir")

    def _backfill_document_trust(self) -> None:
        private = ",".join("?" * len(self._PRIVATE_SOURCE_TYPES))
        public = ",".join("?" * len(self._PUBLIC_SOURCE_TYPES))
        self.connection.execute(
            f"""UPDATE documents SET source_class='private', trust='verified'
                WHERE trust IS NULL AND source_type IN ({private})""",
            self._PRIVATE_SOURCE_TYPES,
        )
        self.connection.execute(
            f"""UPDATE documents SET source_class='public', trust='whitelist'
                WHERE trust IS NULL AND source_type IN ({public})""",
            self._PUBLIC_SOURCE_TYPES,
        )

    _DEFAULT_TRUSTED_HOSTS = (
        ("www1.hkexnews.hk", "港交所披露易"),
        ("www.hkexnews.hk", "港交所披露易"),
        ("hkexnews.hk", "港交所披露易"),
    )

    def _seed_trusted_hosts(self) -> None:
        for host, label in self._DEFAULT_TRUSTED_HOSTS:
            self.connection.execute(
                "INSERT OR IGNORE INTO trusted_hosts (host,label,created_at) VALUES (?,?,?)",
                (host, label, _dt(datetime.now(timezone.utc))),
            )

    def _seed_llm_catalog(self) -> None:
        """Seed builtin domestic providers/models once; user edits survive."""
        from .llm_catalog import BUILTIN_PROVIDERS, parse_thinking_levels

        now = _dt(datetime.now(timezone.utc))
        tombstones = set(self.get_setting("llm.deleted_models") or [])
        for provider in BUILTIN_PROVIDERS:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO llm_providers (name,base_url,api_key,enabled,builtin,created_at) VALUES (?,?,?,1,1,?)",
                (provider["name"], provider["base_url"], None, now),
            )
            if cursor.rowcount:
                provider_id = cursor.lastrowid
            else:
                row = self.connection.execute("SELECT id FROM llm_providers WHERE name=?", (provider["name"],)).fetchone()
                provider_id = row["id"]
            for model in provider["models"]:
                if f"{provider['name']}:{model['model_id']}" in tombstones:
                    continue
                self.connection.execute(
                    "INSERT OR IGNORE INTO llm_models (provider_id,model_id,display_name,thinking_levels,enabled) VALUES (?,?,?,?,1)",
                    (provider_id, model["model_id"], model["display_name"], _json(parse_thinking_levels(model["thinking_levels"]))),
                )

    # --- LLM provider/model hub -------------------------------------------------

    def list_llm_providers(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM llm_providers ORDER BY builtin DESC, id ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_llm_provider(self, provider_id: int) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM llm_providers WHERE id=?", (provider_id,)).fetchone()
        return dict(row) if row else None

    def add_llm_provider(self, name: str, base_url: str, api_key: str | None = None) -> dict[str, Any]:
        name = name.strip()
        base_url = base_url.strip().rstrip("/")
        if not name or not base_url:
            raise ValueError("name 和 base_url 必填")
        now = _dt(datetime.now(timezone.utc))
        cursor = self.connection.execute(
            "INSERT INTO llm_providers (name,base_url,api_key,enabled,builtin,created_at) VALUES (?,?,?,1,0,?)",
            (name, base_url, (api_key or "").strip() or None, now),
        )
        self.connection.commit()
        return self.get_llm_provider(cursor.lastrowid)  # type: ignore[return-value]

    def update_llm_provider(self, provider_id: int, *, name: str | None = None, base_url: str | None = None, api_key: str | None = None) -> dict[str, Any]:
        current = self.get_llm_provider(provider_id)
        if not current:
            raise KeyError("供应商不存在")
        fields: list[str] = []
        values: list[Any] = []
        if name is not None and name.strip():
            fields.append("name=?")
            values.append(name.strip())
        if base_url is not None and base_url.strip():
            fields.append("base_url=?")
            values.append(base_url.strip().rstrip("/"))
        if api_key is not None:
            fields.append("api_key=?")
            values.append(api_key.strip() or None)
        if fields:
            values.append(provider_id)
            self.connection.execute(f"UPDATE llm_providers SET {', '.join(fields)} WHERE id=?", values)
            self.connection.commit()
        return self.get_llm_provider(provider_id)  # type: ignore[return-value]

    def remove_llm_provider(self, provider_id: int) -> None:
        self.connection.execute("DELETE FROM llm_models WHERE provider_id=?", (provider_id,))
        self.connection.execute("DELETE FROM llm_providers WHERE id=?", (provider_id,))
        selection = self.get_setting("llm.selection")
        if selection and selection.get("provider_id") == provider_id:
            self.connection.execute("DELETE FROM app_settings WHERE key='llm.selection'")
        self.connection.commit()

    def list_llm_models(self, provider_id: int | None = None) -> list[dict[str, Any]]:
        if provider_id is None:
            rows = self.connection.execute(
                "SELECT * FROM llm_models ORDER BY provider_id, id ASC"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM llm_models WHERE provider_id=? ORDER BY id ASC", (provider_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_llm_model(self, model_row_id: int) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM llm_models WHERE id=?", (model_row_id,)).fetchone()
        return dict(row) if row else None

    def add_llm_model(self, provider_id: int, model_id: str, display_name: str | None = None, thinking_levels: Any = None) -> dict[str, Any]:
        from .llm_catalog import parse_thinking_levels

        model_id = model_id.strip()
        if not model_id:
            raise ValueError("model_id 必填")
        if not self.get_llm_provider(provider_id):
            raise KeyError("供应商不存在")
        levels = parse_thinking_levels(thinking_levels)
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO llm_models (provider_id,model_id,display_name,thinking_levels,enabled) VALUES (?,?,?,?,1)",
            (provider_id, model_id, (display_name or "").strip() or None, _json(levels)),
        )
        if not cursor.rowcount:
            raise ValueError("该供应商下已存在同名模型")
        self._drop_model_tombstone(provider_id, model_id)
        self.connection.commit()
        return self.get_llm_model(cursor.lastrowid)  # type: ignore[return-value]

    def _model_tombstone_key(self, provider_id: int, model_id: str) -> str | None:
        provider = self.get_llm_provider(provider_id)
        return f"{provider['name']}:{model_id}" if provider else None

    def _drop_model_tombstone(self, provider_id: int, model_id: str) -> None:
        key = self._model_tombstone_key(provider_id, model_id)
        if not key:
            return
        tombstones = [entry for entry in (self.get_setting("llm.deleted_models") or []) if entry != key]
        self.set_setting("llm.deleted_models", tombstones)

    def remove_llm_model(self, model_row_id: int) -> None:
        model = self.get_llm_model(model_row_id)
        if model:
            key = self._model_tombstone_key(int(model["provider_id"]), model["model_id"])
            if key:
                tombstones = (self.get_setting("llm.deleted_models") or []) + [key]
                self.set_setting("llm.deleted_models", tombstones)
        self.connection.execute("DELETE FROM llm_models WHERE id=?", (model_row_id,))
        selection = self.get_setting("llm.selection")
        if selection and selection.get("model_row_id") == model_row_id:
            self.connection.execute("DELETE FROM app_settings WHERE key='llm.selection'")
        self.connection.commit()

    # --- generic kv settings (selection, recents) -------------------------------

    def get_setting(self, key: str) -> Any:
        row = self.connection.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["value"])
        except (ValueError, TypeError):
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        self.connection.execute(
            "INSERT INTO app_settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, _json(value)),
        )
        self.connection.commit()

    def save_project(self, project: ResearchProject) -> None:
        self.connection.execute(
            """INSERT INTO projects (id,user_id,company_id,symbol,name,market,title,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, title=excluded.title, updated_at=excluded.updated_at""",
            (str(project.id), str(project.user_id), str(project.company_id), project.symbol, project.name, project.market, project.title, _dt(project.created_at), _dt(project.updated_at)),
        )
        self.connection.commit()
        self.set_project_tags(project.id, project.tags)

    @staticmethod
    def _normalize_tags(tags: list[str] | tuple[str, ...] | None) -> list[str]:
        values: list[str] = []
        for raw in tags or []:
            value = " ".join(str(raw).strip().split())[:48]
            if value and value.casefold() not in {item.casefold() for item in values}:
                values.append(value)
        return values[:12]

    def set_project_tags(self, project_id: UUID, tags: list[str] | tuple[str, ...] | None) -> list[str]:
        normalized = self._normalize_tags(tags)
        self.connection.execute("DELETE FROM project_tags WHERE project_id=?", (str(project_id),))
        for name in normalized:
            tag_id = self.connection.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            if tag_id is None:
                tag_uuid = uuid4()
                self.connection.execute("INSERT INTO tags (id,name,created_at) VALUES (?,?,?)", (str(tag_uuid), name, _dt(datetime.now(timezone.utc))))
                tag_value = str(tag_uuid)
            else:
                tag_value = tag_id["id"]
            self.connection.execute("INSERT OR IGNORE INTO project_tags (project_id,tag_id) VALUES (?,?)", (str(project_id), tag_value))
        self.connection.commit()
        return normalized

    def list_project_tags(self, project_id: UUID) -> list[str]:
        rows = self.connection.execute(
            "SELECT t.name FROM tags t JOIN project_tags pt ON pt.tag_id=t.id WHERE pt.project_id=? ORDER BY t.name COLLATE NOCASE",
            (str(project_id),),
        ).fetchall()
        return [row["name"] for row in rows]

    def list_tags(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT t.name, COUNT(pt.project_id) AS project_count FROM tags t LEFT JOIN project_tags pt ON pt.tag_id=t.id GROUP BY t.id ORDER BY t.name COLLATE NOCASE"
        ).fetchall()
        return [dict(row) for row in rows]

    def save_session(self, session: ResearchSession) -> None:
        self.connection.execute(
            """INSERT INTO sessions (id,project_id,title,status,active_run_id,latest_event_at,last_event_type,last_event_preview,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET title=excluded.title, status=excluded.status,
                 active_run_id=excluded.active_run_id, latest_event_at=excluded.latest_event_at,
                 last_event_type=excluded.last_event_type, last_event_preview=excluded.last_event_preview,
                 updated_at=excluded.updated_at""",
            (str(session.id), str(session.project_id), session.title, session.status,
             str(session.active_run_id) if session.active_run_id else None, _dt(session.latest_event_at),
             session.last_event_type, session.last_event_preview, _dt(session.created_at), _dt(session.updated_at)),
        )
        self.connection.commit()

    def append_session_event(self, session_id: UUID, event_type: str, payload: dict[str, Any], created_at: datetime) -> None:
        row = self.connection.execute("SELECT id FROM sessions WHERE id=?", (str(session_id),)).fetchone()
        if row is None:
            raise KeyError(f"unknown session: {session_id}")
        preview = str(payload.get("preview") or payload.get("text") or payload.get("question") or event_type)[:240]
        self.connection.execute(
            "INSERT INTO session_events (session_id,event_type,payload_json,created_at) VALUES (?,?,?,?)",
            (str(session_id), event_type, _json(payload), _dt(created_at)),
        )
        self.connection.execute(
            "UPDATE sessions SET latest_event_at=?, last_event_type=?, last_event_preview=?, updated_at=? WHERE id=?",
            (_dt(created_at), event_type, preview, _dt(created_at), str(session_id)),
        )
        self.connection.commit()

    def save_session_message(self, message: SessionMessage) -> None:
        self.connection.execute(
            """INSERT INTO session_messages (id,session_id,role,message_type,content_json,run_id,report_id,created_at)
               VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
            (str(message.id), str(message.session_id), message.role, message.message_type, _json(message.content),
             str(message.run_id) if message.run_id else None, str(message.report_id) if message.report_id else None, _dt(message.created_at)),
        )
        self.append_session_event(message.session_id, f"message/{message.role}", {"preview": self._message_preview(message.content)}, message.created_at)

    @staticmethod
    def _message_preview(content: dict[str, Any]) -> str:
        text = content.get("text") or content.get("content") or content.get("title") or ""
        return str(text)[:240]

    def list_sessions(self, project_id: UUID, include_archived: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM sessions WHERE project_id=?"
        params: list[Any] = [str(project_id)]
        if not include_archived:
            query += " AND status != 'archived'"
        query += " ORDER BY latest_event_at DESC, created_at DESC"
        return [dict(row) for row in self.connection.execute(query, params).fetchall()]

    def find_active_session(self, project_id: UUID) -> ResearchSession | None:
        row = self.connection.execute(
            "SELECT * FROM sessions WHERE project_id=? AND status='active' ORDER BY latest_event_at DESC LIMIT 1",
            (str(project_id),),
        ).fetchone()
        return self._session_from_row(row) if row else None

    def set_session_status(self, session_id: UUID, status: str) -> ResearchSession:
        if status not in {"active", "archived"}:
            raise ValueError("session status must be active or archived")
        session = self.load_session(session_id)
        now = datetime.now(timezone.utc)
        self.connection.execute("UPDATE sessions SET status=?, updated_at=? WHERE id=?", (status, _dt(now), str(session_id)))
        self.connection.commit()
        self.append_session_event(session_id, f"session/{status}", {"preview": f"会话已{('归档' if status == 'archived' else '激活')}"}, now)
        return self.load_session(session_id)

    def load_session(self, session_id: UUID) -> ResearchSession:
        row = self.connection.execute("SELECT * FROM sessions WHERE id=?", (str(session_id),)).fetchone()
        if row is None:
            raise KeyError(f"unknown session: {session_id}")
        return self._session_from_row(row)

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> ResearchSession:
        return ResearchSession(
            project_id=UUID(row["project_id"]), title=row["title"], status=row["status"], id=UUID(row["id"]),
            active_run_id=UUID(row["active_run_id"]) if row["active_run_id"] else None,
            latest_event_at=datetime.fromisoformat(row["latest_event_at"]), last_event_type=row["last_event_type"],
            last_event_preview=row["last_event_preview"], created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_session_messages(self, session_id: UUID) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM session_messages WHERE session_id=? ORDER BY created_at, id", (str(session_id),)).fetchall()
        return [{**dict(row), "content": json.loads(row["content_json"])} for row in rows]

    def list_session_events(self, session_id: UUID) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM session_events WHERE session_id=? ORDER BY seq", (str(session_id),)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def create_job(self, job_id: UUID, session_id: UUID, payload: dict[str, Any], queue_name: str = "inline", rq_job_id: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        self.connection.execute(
            "INSERT INTO jobs (id,session_id,payload_json,status,queue_name,rq_job_id,attempts,created_at) VALUES (?,?,?,?,?,?,0,?)",
            (str(job_id), str(session_id), _json(payload), "queued", queue_name, rq_job_id, _dt(now)),
        )
        self.connection.commit()

    def update_job(self, job_id: UUID, *, status: str, run_id: UUID | None = None, rq_job_id: str | None = None,
                   error: dict[str, Any] | None = None, attempts: int | None = None) -> None:
        now = datetime.now(timezone.utc)
        started = _dt(now) if status == "running" else None
        completed = _dt(now) if status in {"completed", "failed", "canceled"} else None
        self.connection.execute(
            """UPDATE jobs SET status=?, run_id=COALESCE(?,run_id), rq_job_id=COALESCE(?,rq_job_id),
               attempts=COALESCE(?,attempts), error_json=?, started_at=COALESCE(?,started_at), completed_at=COALESCE(?,completed_at) WHERE id=?""",
            (status, str(run_id) if run_id else None, rq_job_id, attempts, _json(error) if error else None, started, completed, str(job_id)),
        )
        self.connection.commit()

    def set_job_queue_id(self, job_id: UUID, queue_job_id: str) -> None:
        self.connection.execute("UPDATE jobs SET rq_job_id=? WHERE id=?", (queue_job_id, str(job_id)))
        self.connection.commit()

    def load_job(self, job_id: UUID) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM jobs WHERE id=?", (str(job_id),)).fetchone()
        if row is None:
            raise KeyError(f"unknown job: {job_id}")
        result = dict(row); result["payload"] = json.loads(result.pop("payload_json"))
        if result.get("error_json"):
            result["error"] = json.loads(result["error_json"])
        result.pop("error_json", None)
        return result

    def list_jobs(self, session_id: UUID | None = None, *, active_only: bool = False) -> list[dict[str, Any]]:
        """Return durable queue jobs, newest first.

        ``active_only`` is used by the chat client to resume polling after a
        page refresh.  Keeping this in SQLite means the UI does not need to
        know anything about Redis/RQ state.
        """
        query = "SELECT id FROM jobs"
        clauses: list[str] = []
        params: list[Any] = []
        if session_id:
            clauses.append("session_id=?")
            params.append(str(session_id))
        if active_only:
            clauses.append("status IN ('queued','running')")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"
        return [self.load_job(UUID(row["id"])) for row in self.connection.execute(query, params).fetchall()]

    def list_companies(self) -> list[dict[str, Any]]:
        # A company is identified by market + symbol. Older versions could
        # create multiple project rows for the same company, so aggregate in
        # Python and expose all project IDs for the UI to load their sessions.
        project_rows = self.connection.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for row in project_rows:
            key = (row["market"], row["symbol"])
            sessions = self.connection.execute(
                "SELECT latest_event_at,last_event_type,last_event_preview FROM sessions WHERE project_id=? ORDER BY latest_event_at DESC",
                (row["id"],),
            ).fetchall()
            latest_session = sessions[0] if sessions else None
            latest_event_at = latest_session["latest_event_at"] if latest_session else None
            item = grouped.get(key)
            if item is None:
                item = {
                    **dict(row),
                    "project_ids": [row["id"]],
                    "session_count": len(sessions),
                    "company_latest_event_at": latest_event_at,
                    "latest_event_preview": latest_session["last_event_preview"] if latest_session else None,
                    "latest_event_type": latest_session["last_event_type"] if latest_session else None,
                }
                grouped[key] = item
                continue
            item["project_ids"].append(row["id"])
            item["session_count"] += len(sessions)
            current_event = item["company_latest_event_at"] or ""
            if (latest_event_at or "") > current_event:
                item["company_latest_event_at"] = latest_event_at
                item["latest_event_preview"] = latest_session["last_event_preview"] if latest_session else None
                item["latest_event_type"] = latest_session["last_event_type"] if latest_session else None
        return sorted(grouped.values(), key=lambda item: (item["company_latest_event_at"] or item["updated_at"], item["updated_at"]), reverse=True)

    def save_run(self, run: ResearchRun) -> None:
        self.connection.execute(
            """INSERT INTO runs (id,project_id,session_id,question,as_of_date,run_type,status,plan_version,model_version,created_at,started_at,completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status, plan_version=excluded.plan_version,
                 model_version=excluded.model_version, session_id=excluded.session_id, started_at=excluded.started_at, completed_at=excluded.completed_at""",
            (str(run.id), str(run.project_id), str(run.session_id) if run.session_id else None, run.question, run.as_of_date.isoformat(), run.run_type, run.status.value, run.plan_version, run.model_version, _dt(run.created_at), _dt(run.started_at), _dt(run.completed_at)),
        )
        for step in run.steps:
            self.connection.execute(
                """INSERT INTO steps (id,run_id,step_key,step_order,status,attempt,input_json,output_json,error_json,started_at,completed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(run_id,step_key) DO UPDATE SET status=excluded.status, attempt=excluded.attempt,
                     input_json=excluded.input_json, output_json=excluded.output_json, error_json=excluded.error_json,
                     started_at=excluded.started_at, completed_at=excluded.completed_at""",
                (str(step.id), str(step.run_id), step.step_key, step.order, step.status, step.attempt, _json(step.input_data) if step.input_data is not None else None, _json(step.output_data) if step.output_data is not None else None, _json(step.error) if step.error is not None else None, _dt(step.started_at), _dt(step.completed_at)),
            )
        self.connection.commit()

    def append_event(self, run_id: UUID, event_type: str, payload: dict[str, Any], created_at: datetime) -> RunEvent:
        cursor = self.connection.execute(
            "INSERT INTO run_events (run_id,event_type,payload_json,created_at) VALUES (?,?,?,?)",
            (str(run_id), event_type, _json(payload), _dt(created_at)),
        )
        self.connection.commit()
        return RunEvent(int(cursor.lastrowid), run_id, event_type, dict(payload), created_at)

    def events_for_run(self, run_id: UUID) -> list[RunEvent]:
        rows = self.connection.execute("SELECT * FROM run_events WHERE run_id=? ORDER BY seq", (str(run_id),)).fetchall()
        return [RunEvent(int(row["seq"]), UUID(row["run_id"]), row["event_type"], json.loads(row["payload_json"]), datetime.fromisoformat(row["created_at"])) for row in rows]

    def save_document(self, document: RawDocument, run_id: UUID | None = None) -> None:
        self.connection.execute(
            """INSERT INTO documents (id,company_id,source_type,source_url,title,content,content_hash,published_at,period_start,period_end,language,source_class,trust)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET content=excluded.content, content_hash=excluded.content_hash,
                 title=excluded.title, published_at=excluded.published_at, period_start=excluded.period_start,
                 period_end=excluded.period_end, language=excluded.language,
                 source_class=excluded.source_class, trust=excluded.trust""",
            (str(document.id), str(document.company_id), document.source_type, document.source_url, document.title,
             document.content, document.content_hash, _dt(document.published_at), document.period_start.isoformat() if document.period_start else None,
             document.period_end.isoformat() if document.period_end else None, document.language,
             document.source_class, document.trust),
        )
        if run_id is not None:
            self.connection.execute(
                "INSERT OR IGNORE INTO run_documents (run_id,document_id) VALUES (?,?)",
                (str(run_id), str(document.id)),
            )

    def save_documents(self, run_id: UUID | list[RawDocument], documents: list[RawDocument] | None = None) -> None:
        # ``save_documents(documents)`` remains useful for company-level
        # ingestion; passing run_id additionally records run provenance.
        if documents is None:
            documents = run_id  # type: ignore[assignment]
            run_id = None
        for document in documents:
            self.save_document(document, run_id if isinstance(run_id, UUID) else None)
        self.connection.commit()

    def save_evidence_chunks(self, chunks: list[EvidenceChunk]) -> None:
        for chunk in chunks:
            self.connection.execute(
                """INSERT INTO evidence_chunks (id,document_id,chunk_index,text,start_line,end_line,published_at,page,section)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET text=excluded.text, start_line=excluded.start_line,
                     end_line=excluded.end_line, published_at=excluded.published_at, page=excluded.page, section=excluded.section""",
                (str(chunk.id), str(chunk.document_id), chunk.chunk_index, chunk.text, chunk.start_line, chunk.end_line,
                 _dt(chunk.published_at), chunk.page, chunk.section),
            )
        self.connection.commit()

    def save_evidence_chunk(self, chunk: EvidenceChunk) -> None:
        self.save_evidence_chunks([chunk])

    def save_facts(self, run_id: UUID, facts: list[FactCandidate]) -> None:
        self.connection.execute("DELETE FROM facts WHERE run_id=?", (str(run_id),))
        self.connection.executemany(
            "INSERT INTO facts (run_id,metric,value,currency,unit,period_end,evidence_id,source_line,raw_text,confidence) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(str(run_id), fact.metric, fact.value, fact.currency, fact.unit, fact.period_end.isoformat() if fact.period_end else None,
              str(fact.evidence_id), fact.source_line, fact.raw_text, fact.confidence) for fact in facts],
        )
        self.connection.commit()

    def save_calculations(self, run_id: UUID, calculations: list[CalculationResult]) -> None:
        self.connection.execute("DELETE FROM calculations WHERE run_id=?", (str(run_id),))
        self.connection.executemany(
            "INSERT INTO calculations (run_id,calculation_type,formula_version,inputs_json,outputs_json) VALUES (?,?,?,?,?)",
            [(str(run_id), calculation.calculation_type, calculation.formula_version, _json(calculation.inputs), _json(calculation.outputs)) for calculation in calculations],
        )
        self.connection.commit()

    def save_claims(self, run_id: UUID, claims: list[dict[str, Any]]) -> None:
        self.connection.execute("DELETE FROM claims WHERE run_id=?", (str(run_id),))
        self.connection.executemany(
            "INSERT INTO claims (run_id,category,text,evidence_ids_json,confidence,counter_evidence_ids_json,provider,model) VALUES (?,?,?,?,?,?,?,?)",
            [(str(run_id), claim.get("category", "general"), claim.get("text", ""), _json(claim.get("evidence_ids", [])), float(claim.get("confidence", 0.0)),
              _json(claim.get("counter_evidence_ids", [])), claim.get("provider"), claim.get("model")) for claim in claims],
        )
        self.connection.commit()

    def save_report(self, run_id: UUID, report: dict[str, Any], report_id: UUID | None = None) -> UUID:
        report_id = report_id or (UUID(report["report_id"]) if report.get("report_id") else uuid4())
        row = self.connection.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM reports WHERE run_id=?", (str(run_id),)).fetchone()
        version = int(row[0])
        payload = dict(report)
        payload["report_id"] = str(report_id)
        payload["run_id"] = str(run_id)
        payload["version"] = version
        self.connection.execute(
            "INSERT INTO reports (id,run_id,version,payload_json,markdown,created_at) VALUES (?,?,?,?,?,?)",
            (str(report_id), str(run_id), version, _json(payload), str(payload.get("markdown", "")), _dt(datetime.now(timezone.utc))),
        )
        self.connection.commit()
        return report_id

    def load_report(self, report_id: UUID) -> dict[str, Any]:
        row = self.connection.execute("SELECT payload_json FROM reports WHERE id=?", (str(report_id),)).fetchone()
        if row is None:
            raise KeyError(f"unknown report: {report_id}")
        return json.loads(row["payload_json"])

    def list_projects(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def find_project(self, symbol: str, market: str = "HK") -> ResearchProject | None:
        row = self.connection.execute(
            "SELECT * FROM projects WHERE symbol=? AND market=? ORDER BY updated_at DESC LIMIT 1",
            (symbol, market),
        ).fetchone()
        if row is None:
            return None
        return ResearchProject(
            user_id=UUID(row["user_id"]), company_id=UUID(row["company_id"]), symbol=row["symbol"],
            name=row["name"], market=row["market"], id=UUID(row["id"]), title=row["title"],
            tags=self.list_project_tags(UUID(row["id"])),
            created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def load_project(self, project_id: UUID) -> ResearchProject:
        row = self.connection.execute("SELECT * FROM projects WHERE id=?", (str(project_id),)).fetchone()
        if row is None:
            raise KeyError(f"unknown project: {project_id}")
        return ResearchProject(
            user_id=UUID(row["user_id"]), company_id=UUID(row["company_id"]), symbol=row["symbol"],
            name=row["name"], market=row["market"], id=UUID(row["id"]), title=row["title"],
            tags=self.list_project_tags(UUID(row["id"])),
            created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_runs(self, project_id: UUID | None = None, session_id: UUID | None = None) -> list[dict[str, Any]]:
        if session_id:
            rows = self.connection.execute("SELECT * FROM runs WHERE session_id=? ORDER BY created_at DESC", (str(session_id),)).fetchall()
        elif project_id:
            rows = self.connection.execute("SELECT * FROM runs WHERE project_id=? ORDER BY created_at DESC", (str(project_id),)).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def load_run_artifacts(self, run_id: UUID) -> dict[str, Any]:
        documents = [dict(row) for row in self.connection.execute(
            """SELECT d.* FROM documents d JOIN run_documents rd ON rd.document_id=d.id
               WHERE rd.run_id=? ORDER BY d.published_at, d.id""",
            (str(run_id),),
        ).fetchall()]
        evidence = [dict(row) for row in self.connection.execute(
            """SELECT e.* FROM evidence_chunks e JOIN run_documents rd ON rd.document_id=e.document_id
               WHERE rd.run_id=? ORDER BY e.document_id, e.chunk_index""",
            (str(run_id),),
        ).fetchall()]
        facts = [dict(row) for row in self.connection.execute("SELECT * FROM facts WHERE run_id=? ORDER BY id", (str(run_id),)).fetchall()]
        calculations = [dict(row) for row in self.connection.execute("SELECT * FROM calculations WHERE run_id=? ORDER BY id", (str(run_id),)).fetchall()]
        claims = [dict(row) for row in self.connection.execute("SELECT * FROM claims WHERE run_id=? ORDER BY id", (str(run_id),)).fetchall()]
        for row in calculations:
            row["inputs"] = json.loads(row.pop("inputs_json")); row["outputs"] = json.loads(row.pop("outputs_json"))
        for row in claims:
            row["evidence_ids"] = json.loads(row.pop("evidence_ids_json")); row["counter_evidence_ids"] = json.loads(row.pop("counter_evidence_ids_json"))
        return {"documents": documents, "evidence": evidence, "facts": facts, "calculations": calculations, "claims": claims, "reports": self._reports_for_run(run_id)}

    def _reports_for_run(self, run_id: UUID) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT id,version,created_at FROM reports WHERE run_id=? ORDER BY version", (str(run_id),)).fetchall()
        return [dict(row) for row in rows]

    def list_company_documents(self, company_id: UUID) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT id, source_type, source_url, title, published_at, source_class, trust FROM documents WHERE company_id=? ORDER BY published_at DESC, id",
            (str(company_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_seen_content_hashes(self, company_id: UUID) -> set[str]:
        """Content hashes of documents already ingested by any run of this company."""
        rows = self.connection.execute(
            """SELECT DISTINCT d.content_hash FROM documents d
               JOIN run_documents rd ON rd.document_id=d.id
               JOIN runs r ON r.id=rd.run_id
               JOIN projects p ON p.id=r.project_id
               WHERE p.company_id=?""",
            (str(company_id),),
        ).fetchall()
        return {row["content_hash"] for row in rows}

    def add_company_source(self, company_id: UUID, url: str, title: str | None = None, source_class: str = "private") -> dict[str, Any]:
        existing = self.connection.execute(
            "SELECT * FROM company_sources WHERE company_id=? AND url=?", (str(company_id), url),
        ).fetchone()
        if existing:
            return dict(existing)
        row_id = str(uuid4())
        self.connection.execute(
            "INSERT INTO company_sources (id, company_id, url, title, created_at, source_class) VALUES (?,?,?,?,?,?)",
            (row_id, str(company_id), url, title, _dt(datetime.now(timezone.utc)), source_class),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM company_sources WHERE id=?", (row_id,)).fetchone()
        return dict(row)

    def list_company_sources(self, company_id: UUID) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT id, url, title, created_at, source_class FROM company_sources WHERE company_id=? ORDER BY created_at DESC, id",
            (str(company_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def company_source_classes(self, company_id: UUID) -> dict[str, str]:
        """Registered URL -> source_class map (defaults to private)."""
        rows = self.connection.execute(
            "SELECT url, source_class FROM company_sources WHERE company_id=?",
            (str(company_id),),
        ).fetchall()
        return {row["url"]: (row["source_class"] or "private") for row in rows}

    def list_trusted_hosts(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT id, host, label, created_at FROM trusted_hosts ORDER BY created_at, id"
        ).fetchall()
        return [dict(row) for row in rows]

    def trusted_hosts_set(self) -> set[str]:
        return {row["host"] for row in self.connection.execute("SELECT host FROM trusted_hosts").fetchall()}

    def is_trusted_host(self, host: str | None) -> bool:
        return host_in_set(host, self.trusted_hosts_set())

    def add_trusted_host(self, host: str, label: str | None = None) -> dict[str, Any]:
        normalized = normalize_host(host)
        # Canonicalize to the bare host so www. variants dedupe to one row;
        # lookups still tolerate both forms via host_in_set.
        if normalized.startswith("www."):
            normalized = normalized[4:]
        if not normalized or "." not in normalized or " " in normalized:
            raise ValueError("host 必须是合法域名，例如 hkexnews.hk")
        existing = self.connection.execute("SELECT * FROM trusted_hosts WHERE host IN (?, ?)", (normalized, f"www.{normalized}")).fetchone()
        if existing:
            return dict(existing)
        self.connection.execute(
            "INSERT INTO trusted_hosts (host,label,created_at) VALUES (?,?,?)",
            (normalized, (label or "").strip() or None, _dt(datetime.now(timezone.utc))),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM trusted_hosts WHERE host=?", (normalized,)).fetchone()
        return dict(row)

    def remove_trusted_host(self, host_id: int) -> None:
        self.connection.execute("DELETE FROM trusted_hosts WHERE id=?", (int(host_id),))
        self.connection.commit()

    def remove_company_source(self, source_id: UUID) -> None:
        self.connection.execute("DELETE FROM company_sources WHERE id=?", (str(source_id),))
        self.connection.commit()

    def list_company_source_urls(self, company_id: UUID) -> list[str]:
        rows = self.connection.execute(
            "SELECT url FROM company_sources WHERE company_id=? ORDER BY created_at, id",
            (str(company_id),),
        ).fetchall()
        return [row["url"] for row in rows]

    def delete_company(self, company_id: UUID) -> dict[str, int]:
        """Delete everything belonging to one company, children first.

        Removal is total: projects, sessions and their messages/events, runs
        with steps/events/facts/calculations/claims/reports, documents with
        their evidence chunks, registered sources, jobs and tag links.
        """
        connection = self.connection
        run_ids = [row["id"] for row in connection.execute(
            "SELECT id FROM runs WHERE project_id IN (SELECT id FROM projects WHERE company_id=?)",
            (str(company_id),),
        ).fetchall()]
        session_ids = [row["id"] for row in connection.execute(
            "SELECT id FROM sessions WHERE project_id IN (SELECT id FROM projects WHERE company_id=?)",
            (str(company_id),),
        ).fetchall()]
        document_ids = [row["id"] for row in connection.execute(
            "SELECT id FROM documents WHERE company_id=?",
            (str(company_id),),
        ).fetchall()]
        counts: dict[str, int] = {}

        def execute_many(table: str, sql: str, ids: list[str]) -> None:
            if not ids:
                return
            marks = ",".join("?" for _ in ids)
            counts[table] = connection.execute(sql.format(marks=marks), ids).rowcount

        execute_many("reports", "DELETE FROM reports WHERE run_id IN ({marks})", run_ids)
        execute_many("calculations", "DELETE FROM calculations WHERE run_id IN ({marks})", run_ids)
        execute_many("claims", "DELETE FROM claims WHERE run_id IN ({marks})", run_ids)
        execute_many("facts", "DELETE FROM facts WHERE run_id IN ({marks})", run_ids)
        execute_many("run_events", "DELETE FROM run_events WHERE run_id IN ({marks})", run_ids)
        execute_many("steps", "DELETE FROM steps WHERE run_id IN ({marks})", run_ids)
        execute_many("run_documents", "DELETE FROM run_documents WHERE run_id IN ({marks})", run_ids)
        execute_many("runs", "DELETE FROM runs WHERE id IN ({marks})", run_ids)
        execute_many("evidence_chunks", "DELETE FROM evidence_chunks WHERE document_id IN ({marks})", document_ids)
        execute_many("documents", "DELETE FROM documents WHERE id IN ({marks})", document_ids)
        execute_many("jobs", "DELETE FROM jobs WHERE session_id IN ({marks})", session_ids)
        execute_many("session_messages", "DELETE FROM session_messages WHERE session_id IN ({marks})", session_ids)
        execute_many("session_events", "DELETE FROM session_events WHERE session_id IN ({marks})", session_ids)
        execute_many("sessions", "DELETE FROM sessions WHERE id IN ({marks})", session_ids)
        connection.execute(
            "DELETE FROM run_documents WHERE run_id NOT IN (SELECT id FROM runs)"
        )
        connection.execute(
            "DELETE FROM company_sources WHERE company_id=?", (str(company_id),)
        )
        connection.execute(
            """DELETE FROM tags WHERE id IN (
                 SELECT tag_id FROM project_tags
                 WHERE project_id IN (SELECT id FROM projects WHERE company_id=?)
               ) AND id NOT IN (SELECT tag_id FROM project_tags WHERE project_id NOT IN (SELECT id FROM projects WHERE company_id=?))""",
            (str(company_id), str(company_id)),
        )
        connection.execute(
            "DELETE FROM project_tags WHERE project_id IN (SELECT id FROM projects WHERE company_id=?)",
            (str(company_id),),
        )
        counts["projects"] = connection.execute(
            "DELETE FROM projects WHERE company_id=?", (str(company_id),)
        ).rowcount
        self.connection.commit()
        return counts

    def list_project_reports(self, project_id: UUID) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT r.id, r.run_id, r.version, r.created_at, ru.status AS run_status, ru.question
               FROM reports r JOIN runs ru ON ru.id=r.run_id
               WHERE ru.project_id=? ORDER BY r.created_at DESC""",
            (str(project_id),),
        ).fetchall()
        return [dict(row) for row in rows]


    def load_run(self, run_id: UUID) -> ResearchRun:
        row = self.connection.execute("SELECT * FROM runs WHERE id=?", (str(run_id),)).fetchone()
        if row is None:
            raise KeyError(f"unknown run: {run_id}")
        run = ResearchRun(
            project_id=UUID(row["project_id"]),
            question=row["question"],
            as_of_date=date.fromisoformat(row["as_of_date"]),
            run_type=row["run_type"],
            session_id=UUID(row["session_id"]) if row["session_id"] else None,
            id=UUID(row["id"]),
            status=RunStatus(row["status"]),
            plan_version=int(row["plan_version"]),
            model_version=row["model_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        )
        step_rows = self.connection.execute("SELECT * FROM steps WHERE run_id=? ORDER BY step_order", (str(run_id),)).fetchall()
        run.steps = [
            ResearchStep(
                run_id=UUID(step["run_id"]), step_key=step["step_key"], order=int(step["step_order"]),
                status=step["status"], attempt=int(step["attempt"]),
                input_data=json.loads(step["input_json"]) if step["input_json"] else None,
                output_data=json.loads(step["output_json"]) if step["output_json"] else None,
                error=json.loads(step["error_json"]) if step["error_json"] else None,
                id=UUID(step["id"]),
                started_at=datetime.fromisoformat(step["started_at"]) if step["started_at"] else None,
                completed_at=datetime.fromisoformat(step["completed_at"]) if step["completed_at"] else None,
            )
            for step in step_rows
        ]
        return run
