from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


class DecisionExportCenter:
    """Immutable, tenant-scoped decision/audit evidence snapshots.

    Export snapshots are observational artifacts only. They preserve a point-in-time
    view of a conversation and its durable audit material without mutating runtime
    state or granting any approval/execution authority.
    """

    SCHEMA_VERSION = 1
    REDACTED = "[redacted]"

    _SENSITIVE_KEYS = {
        "path",
        "file_path",
        "local_path",
        "server_path",
        "keyframes",
        "authorization",
        "proxy_authorization",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "secret",
        "client_secret",
        "password",
        "credential",
        "credentials",
        "token_env",
        "endpoint_url",
    }
    _SENSITIVE_SUFFIXES = (
        "_token",
        "_secret",
        "_password",
        "_credential",
        "_credentials",
        "_path",
    )

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _init(self) -> None:
        with self._conn() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=30000")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS decision_exports(
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_decision_exports_tenant_created
                    ON decision_exports(tenant_id,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_decision_exports_tenant_conversation
                    ON decision_exports(tenant_id,conversation_id,created_at DESC);
                """
            )

    @staticmethod
    def _json(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}

    @classmethod
    def _is_sensitive_key(cls, key: Any) -> bool:
        normalized = str(key or "").strip().lower().replace("-", "_")
        return (
            normalized in cls._SENSITIVE_KEYS
            or any(normalized.endswith(suffix) for suffix in cls._SENSITIVE_SUFFIXES)
        )

    @classmethod
    def _sanitize(cls, value: Any, stats: dict[str, int]) -> Any:
        if isinstance(value, dict):
            clean: dict[str, Any] = {}
            for raw_key, item in value.items():
                key = str(raw_key)
                if cls._is_sensitive_key(key):
                    clean[key] = cls.REDACTED
                    stats["redacted_fields"] = stats.get("redacted_fields", 0) + 1
                else:
                    clean[key] = cls._sanitize(item, stats)
            return clean
        if isinstance(value, list):
            return [cls._sanitize(item, stats) for item in value]
        if isinstance(value, tuple):
            return [cls._sanitize(item, stats) for item in value]
        return value

    @staticmethod
    def _table_exists(db: sqlite3.Connection, table: str) -> bool:
        row = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return bool(row)

    @staticmethod
    def _canonical(payload: dict[str, Any]) -> str:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @classmethod
    def _hash(cls, payload: dict[str, Any]) -> str:
        return hashlib.sha256(cls._canonical(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _authority() -> dict[str, bool]:
        return {
            "export_changes_production_authority": False,
            "export_changes_action_state": False,
            "export_changes_policy": False,
            "export_changes_routing": False,
            "export_changes_runtime_skills": False,
            "export_executes_tools": False,
        }

    @staticmethod
    def _conversation(row: sqlite3.Row) -> dict[str, Any]:
        source = dict(row)
        allowed = (
            "id",
            "title",
            "scene",
            "created_at",
            "updated_at",
            "created_by",
            "queue_priority",
            "owner_user_id",
            "owner_claimed_at",
            "queue_updated_at",
        )
        return {key: source.get(key) for key in allowed if key in source}

    @classmethod
    def _messages(cls, db: sqlite3.Connection, cid: str) -> list[dict[str, Any]]:
        rows = db.execute(
            "SELECT id,conversation_id,role,content,payload,created_at "
            "FROM messages WHERE conversation_id=? ORDER BY created_at,id",
            (cid,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = cls._json(item.get("payload"))
            out.append(item)
        return out

    @classmethod
    def _assets(cls, db: sqlite3.Connection, cid: str) -> list[dict[str, Any]]:
        rows = db.execute(
            "SELECT * FROM assets WHERE conversation_id=? ORDER BY created_at,id",
            (cid,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            source = dict(row)
            item = {
                "id": source.get("id"),
                "conversation_id": source.get("conversation_id"),
                "name": source.get("name"),
                "mime": source.get("mime"),
                "size": source.get("size"),
                "meta": cls._json(source.get("meta")),
                "created_at": source.get("created_at"),
            }
            for optional in ("active", "excluded_at", "excluded_reason"):
                if optional in source:
                    item[optional] = source.get(optional)
            out.append(item)
        return out

    @classmethod
    def _actions(cls, db: sqlite3.Connection, cid: str) -> list[dict[str, Any]]:
        rows = db.execute(
            "SELECT id,conversation_id,session_id,kind,title,description,risk_level,"
            "side_effect,requires_confirmation,status,payload,created_at,updated_at "
            "FROM actions WHERE conversation_id=? ORDER BY created_at,id",
            (cid,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["side_effect"] = bool(item.get("side_effect"))
            item["requires_confirmation"] = bool(item.get("requires_confirmation"))
            item["payload"] = cls._json(item.get("payload"))
            out.append(item)
        return out

    @classmethod
    def _events(cls, db: sqlite3.Connection, cid: str) -> list[dict[str, Any]]:
        rows = db.execute(
            "SELECT id,conversation_id,type,payload,created_at "
            "FROM task_events WHERE conversation_id=? ORDER BY id",
            (cid,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = cls._json(item.get("payload"))
            out.append(item)
        return out

    @classmethod
    def _jobs(cls, db: sqlite3.Connection, cid: str) -> list[dict[str, Any]]:
        if not cls._table_exists(db, "conversation_jobs"):
            return []
        rows = db.execute(
            "SELECT id,conversation_id,message_id,status,payload,attempts,last_error,"
            "created_at,updated_at,session_id "
            "FROM conversation_jobs WHERE conversation_id=? ORDER BY created_at,id",
            (cid,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = cls._json(item.get("payload"))
            out.append(item)
        return out

    @classmethod
    def _feedback(
        cls,
        db: sqlite3.Connection,
        cid: str,
        tenant_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        disputes: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        if cls._table_exists(db, "evidence_disputes"):
            rows = db.execute(
                """
                SELECT d.*
                FROM evidence_disputes d
                JOIN conversations c ON c.id=d.conversation_id
                WHERE d.conversation_id=? AND c.tenant_id=?
                ORDER BY d.created_at,d.id
                """,
                (cid, tenant_id),
            ).fetchall()
            for row in rows:
                item = dict(row)
                item["target_snapshot"] = cls._json(item.get("target_snapshot"))
                disputes.append(item)
        if (
            disputes
            and cls._table_exists(db, "evidence_dispute_events")
        ):
            rows = db.execute(
                """
                SELECT e.*
                FROM evidence_dispute_events e
                JOIN evidence_disputes d ON d.id=e.dispute_id
                JOIN conversations c ON c.id=d.conversation_id
                WHERE d.conversation_id=? AND c.tenant_id=?
                ORDER BY e.id
                """,
                (cid, tenant_id),
            ).fetchall()
            for row in rows:
                item = dict(row)
                item["payload"] = cls._json(item.get("payload"))
                events.append(item)
        return {"disputes": disputes, "events": events}

    @classmethod
    def _collaboration(
        cls,
        db: sqlite3.Connection,
        cid: str,
        tenant_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        watchers: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        if cls._table_exists(db, "task_watchers"):
            rows = db.execute(
                """
                SELECT w.conversation_id,w.user_id,w.created_at
                FROM task_watchers w
                JOIN conversations c ON c.id=w.conversation_id
                WHERE w.conversation_id=? AND c.tenant_id=?
                ORDER BY w.created_at,w.user_id
                """,
                (cid, tenant_id),
            ).fetchall()
            watchers = [dict(row) for row in rows]
        if cls._table_exists(db, "task_collaboration_events"):
            rows = db.execute(
                """
                SELECT e.id,e.conversation_id,e.event_type,e.actor_user_id,
                       e.target_user_id,e.body,e.mentions,e.related_event_id,e.created_at
                FROM task_collaboration_events e
                JOIN conversations c ON c.id=e.conversation_id
                WHERE e.conversation_id=? AND c.tenant_id=?
                ORDER BY e.id
                """,
                (cid, tenant_id),
            ).fetchall()
            for row in rows:
                item = dict(row)
                mentions = cls._json(item.get("mentions"))
                item["mentions"] = mentions if isinstance(mentions, list) else []
                events.append(item)
        return {"watchers": watchers, "events": events}

    def _build_payload(
        self,
        store,
        *,
        tenant_id: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        tenant = str(tenant_id or "").strip()
        cid = str(conversation_id or "").strip()
        if not tenant or not cid:
            raise KeyError(cid or tenant)

        with store._conn() as db:
            db.execute("BEGIN")
            conversation = db.execute(
                "SELECT * FROM conversations WHERE id=? AND tenant_id=?",
                (cid, tenant),
            ).fetchone()
            if not conversation:
                raise KeyError(cid)
            raw = {
                "schema_version": self.SCHEMA_VERSION,
                "export_kind": "decision_audit_snapshot",
                "tenant_id": tenant,
                "conversation": self._conversation(conversation),
                "messages": self._messages(db, cid),
                "assets": self._assets(db, cid),
                "actions": self._actions(db, cid),
                "task_events": self._events(db, cid),
                "jobs": self._jobs(db, cid),
                "feedback": self._feedback(db, cid, tenant),
                "collaboration": self._collaboration(db, cid, tenant),
            }

        stats = {"redacted_fields": 0}
        clean = self._sanitize(raw, stats)
        clean["manifest"] = {
            "counts": {
                "messages": len(clean["messages"]),
                "assets": len(clean["assets"]),
                "actions": len(clean["actions"]),
                "task_events": len(clean["task_events"]),
                "jobs": len(clean["jobs"]),
                "feedback_disputes": len(clean["feedback"]["disputes"]),
                "feedback_events": len(clean["feedback"]["events"]),
                "collaboration_watchers": len(clean["collaboration"]["watchers"]),
                "collaboration_events": len(clean["collaboration"]["events"]),
            },
            "redacted_field_count": stats["redacted_fields"],
            "asset_binary_included": False,
            "server_local_paths_included": False,
            "content_hash_algorithm": "sha256",
            "snapshot_semantics": "point_in_time_read_only",
        }
        clean["authority"] = self._authority()
        return clean

    def create_snapshot(
        self,
        store,
        *,
        tenant_id: str,
        created_by: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        tenant = str(tenant_id or "").strip()
        cid = str(conversation_id or "").strip()
        actor = str(created_by or "").strip()
        payload = self._build_payload(
            store,
            tenant_id=tenant,
            conversation_id=cid,
        )
        content_hash = self._hash(payload)
        export_id = f"decision-export-{uuid.uuid4().hex[:16]}"
        created_at = time.time()
        canonical = self._canonical(payload)
        with self._conn() as db:
            db.execute(
                "INSERT INTO decision_exports("
                "id,tenant_id,conversation_id,created_by,schema_version,content_hash,payload,created_at"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    export_id,
                    tenant,
                    cid,
                    actor,
                    self.SCHEMA_VERSION,
                    content_hash,
                    canonical,
                    created_at,
                ),
            )
        return self.get_snapshot(export_id, tenant_id=tenant)

    def list_snapshots(
        self,
        *,
        tenant_id: str,
        conversation_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        tenant = str(tenant_id or "").strip()
        cid = str(conversation_id or "").strip() if conversation_id else None
        bounded = max(1, min(200, int(limit)))
        with self._conn() as db:
            if cid:
                rows = db.execute(
                    "SELECT id,tenant_id,conversation_id,created_by,schema_version,"
                    "content_hash,created_at FROM decision_exports "
                    "WHERE tenant_id=? AND conversation_id=? "
                    "ORDER BY created_at DESC,id DESC LIMIT ?",
                    (tenant, cid, bounded),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT id,tenant_id,conversation_id,created_by,schema_version,"
                    "content_hash,created_at FROM decision_exports "
                    "WHERE tenant_id=? ORDER BY created_at DESC,id DESC LIMIT ?",
                    (tenant, bounded),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_snapshot(self, export_id: str, *, tenant_id: str) -> dict[str, Any]:
        export_key = str(export_id or "").strip()
        tenant = str(tenant_id or "").strip()
        with self._conn() as db:
            row = db.execute(
                "SELECT * FROM decision_exports WHERE id=? AND tenant_id=?",
                (export_key, tenant),
            ).fetchone()
        if not row:
            raise KeyError(export_id)
        data = dict(row)
        data["payload"] = self._json(data.get("payload"))
        data["authority"] = self._authority()
        return data

    def verify_snapshot(self, export_id: str, *, tenant_id: str) -> dict[str, Any]:
        snapshot = self.get_snapshot(export_id, tenant_id=tenant_id)
        observed = self._hash(snapshot["payload"])
        expected = str(snapshot["content_hash"])
        return {
            "id": snapshot["id"],
            "tenant_id": snapshot["tenant_id"],
            "conversation_id": snapshot["conversation_id"],
            "algorithm": "sha256",
            "expected_hash": expected,
            "observed_hash": observed,
            "valid": hmac.compare_digest(expected, observed),
            "authority": self._authority(),
        }
