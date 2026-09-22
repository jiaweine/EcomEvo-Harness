from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
LIFECYCLE_EVENTS = ("draft", "evaluation_candidate", "catalog_published")
ALLOWED_DOMAINS = {
    "product_governance",
    "merchant_review",
    "aftersales",
    "risk_review",
    "content_audit",
    "general",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _clean_list(values: list[str] | None, *, limit: int, item_limit: int = 240) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        value = str(raw).strip()
        if not value:
            continue
        value = value[:item_limit]
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(value)
        if len(items) >= limit:
            break
    return items


def normalize_spec(spec: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError("procedure spec must be an object")
    name = str(spec.get("name") or "").strip()[:120]
    domain = str(spec.get("domain") or "").strip()
    purpose = str(spec.get("purpose") or "").strip()[:1200]
    guidance = str(spec.get("guidance") or "").strip()[:8000]
    output_contract = str(spec.get("output_contract") or "").strip()[:5000]
    if not name:
        raise ValueError("procedure name is required")
    if domain not in ALLOWED_DOMAINS:
        raise ValueError("procedure domain is invalid")
    if not purpose:
        raise ValueError("procedure purpose is required")
    if not guidance:
        raise ValueError("procedure guidance is required")

    steps: list[dict[str, str]] = []
    for raw in spec.get("steps") or []:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()[:160]
        instruction = str(raw.get("instruction") or "").strip()[:1600]
        if not title or not instruction:
            continue
        steps.append({"title": title, "instruction": instruction})
        if len(steps) >= 24:
            break

    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "domain": domain,
        "purpose": purpose,
        "guidance": guidance,
        "trigger_terms": _clean_list(spec.get("trigger_terms"), limit=20),
        "preferred_tools": _clean_list(spec.get("preferred_tools"), limit=12),
        "required_evidence": _clean_list(spec.get("required_evidence"), limit=24, item_limit=360),
        "prohibited_actions": _clean_list(spec.get("prohibited_actions"), limit=24, item_limit=360),
        "steps": steps,
        "output_contract": output_contract,
    }


class ProcedureStudio:
    """Tenant-scoped authoring catalog isolated from runtime authority.

    Procedure versions are immutable. Lifecycle transitions are append-only events.
    Catalog publication does not install or activate a RuntimeSkill.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _init(self) -> None:
        with self._lock, self._conn() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS procedure_definitions(
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_procedure_definitions_tenant
                    ON procedure_definitions(tenant_id,created_at DESC);

                CREATE TABLE IF NOT EXISTS procedure_versions(
                    id TEXT PRIMARY KEY,
                    procedure_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    version_no INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(procedure_id,version_no)
                );
                CREATE INDEX IF NOT EXISTS idx_procedure_versions_tenant
                    ON procedure_versions(tenant_id,procedure_id,version_no DESC);

                CREATE TABLE IF NOT EXISTS procedure_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    procedure_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    actor_role TEXT NOT NULL,
                    note TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_procedure_events_version
                    ON procedure_events(tenant_id,version_id,id);
                CREATE INDEX IF NOT EXISTS idx_procedure_events_procedure
                    ON procedure_events(tenant_id,procedure_id,id);
                """
            )

    @staticmethod
    def authority() -> dict[str, bool]:
        return {
            "catalog_publication_activates_runtime_skill": False,
            "candidate_export_activates_runtime_skill": False,
            "changes_policy": False,
            "changes_routing": False,
            "grants_action_authority": False,
            "auto_promotes_to_gold_set": False,
        }

    @staticmethod
    def _version_row(row: sqlite3.Row | dict[str, Any], *, state: str) -> dict[str, Any]:
        item = dict(row)
        spec = json.loads(str(item["content_json"]))
        return {
            "id": str(item["id"]),
            "procedure_id": str(item["procedure_id"]),
            "version_no": int(item["version_no"]),
            "content_hash": str(item["content_hash"]),
            "spec": spec,
            "state": state,
            "created_by": str(item["created_by"]),
            "created_at": float(item["created_at"]),
        }

    @staticmethod
    def _state_in_db(db: sqlite3.Connection, tenant_id: str, version_id: str) -> str:
        row = db.execute(
            "SELECT event_type FROM procedure_events "
            "WHERE tenant_id=? AND version_id=? AND event_type IN (?,?,?) "
            "ORDER BY id DESC LIMIT 1",
            (tenant_id, version_id, *LIFECYCLE_EVENTS),
        ).fetchone()
        return str(row["event_type"]) if row else "draft"

    @staticmethod
    def _assert_procedure(db: sqlite3.Connection, tenant_id: str, procedure_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM procedure_definitions WHERE tenant_id=? AND id=?",
            (tenant_id, procedure_id),
        ).fetchone()
        if row is None:
            raise KeyError(procedure_id)
        return row

    @staticmethod
    def _assert_version(
        db: sqlite3.Connection,
        tenant_id: str,
        procedure_id: str,
        version_id: str,
    ) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM procedure_versions "
            "WHERE tenant_id=? AND procedure_id=? AND id=?",
            (tenant_id, procedure_id, version_id),
        ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return row

    @staticmethod
    def _insert_event(
        db: sqlite3.Connection,
        *,
        tenant_id: str,
        procedure_id: str,
        version_id: str,
        event_type: str,
        actor_user_id: str,
        actor_role: str,
        note: str = "",
        payload: dict[str, Any] | None = None,
        now: float,
    ) -> None:
        db.execute(
            "INSERT INTO procedure_events"
            "(tenant_id,procedure_id,version_id,event_type,actor_user_id,actor_role,note,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                tenant_id,
                procedure_id,
                version_id,
                event_type,
                actor_user_id,
                actor_role,
                str(note or "")[:4000],
                _canonical_json(payload or {}),
                now,
            ),
        )

    def create(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        actor_role: str,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        clean = normalize_spec(spec)
        now = time.time()
        procedure_id = f"proc-{uuid.uuid4().hex[:16]}"
        version_id = f"procver-{uuid.uuid4().hex[:16]}"
        digest = _content_hash(clean)
        with self._lock, self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO procedure_definitions(id,tenant_id,created_by,created_at) VALUES(?,?,?,?)",
                (procedure_id, tenant_id, actor_user_id, now),
            )
            db.execute(
                "INSERT INTO procedure_versions"
                "(id,procedure_id,tenant_id,version_no,content_hash,content_json,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    version_id,
                    procedure_id,
                    tenant_id,
                    1,
                    digest,
                    _canonical_json(clean),
                    actor_user_id,
                    now,
                ),
            )
            self._insert_event(
                db,
                tenant_id=tenant_id,
                procedure_id=procedure_id,
                version_id=version_id,
                event_type="draft",
                actor_user_id=actor_user_id,
                actor_role=actor_role,
                payload={"version_no": 1, "content_hash": digest},
                now=now,
            )
        return self.get(procedure_id, tenant_id=tenant_id)

    def create_version(
        self,
        procedure_id: str,
        *,
        tenant_id: str,
        actor_user_id: str,
        actor_role: str,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        clean = normalize_spec(spec)
        digest = _content_hash(clean)
        now = time.time()
        version_id = f"procver-{uuid.uuid4().hex[:16]}"
        with self._lock, self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            self._assert_procedure(db, tenant_id, procedure_id)
            latest = db.execute(
                "SELECT version_no,content_hash FROM procedure_versions "
                "WHERE tenant_id=? AND procedure_id=? ORDER BY version_no DESC LIMIT 1",
                (tenant_id, procedure_id),
            ).fetchone()
            if latest is None:
                raise KeyError(procedure_id)
            if str(latest["content_hash"]) == digest:
                raise ValueError("new procedure version must change content")
            version_no = int(latest["version_no"]) + 1
            db.execute(
                "INSERT INTO procedure_versions"
                "(id,procedure_id,tenant_id,version_no,content_hash,content_json,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    version_id,
                    procedure_id,
                    tenant_id,
                    version_no,
                    digest,
                    _canonical_json(clean),
                    actor_user_id,
                    now,
                ),
            )
            self._insert_event(
                db,
                tenant_id=tenant_id,
                procedure_id=procedure_id,
                version_id=version_id,
                event_type="draft",
                actor_user_id=actor_user_id,
                actor_role=actor_role,
                payload={"version_no": version_no, "content_hash": digest},
                now=now,
            )
        return self.get(procedure_id, tenant_id=tenant_id)

    def list(self, *, tenant_id: str, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        with self._conn() as db:
            definitions = db.execute(
                "SELECT * FROM procedure_definitions WHERE tenant_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
            rows: list[dict[str, Any]] = []
            for definition in definitions:
                version = db.execute(
                    "SELECT * FROM procedure_versions WHERE tenant_id=? AND procedure_id=? "
                    "ORDER BY version_no DESC LIMIT 1",
                    (tenant_id, str(definition["id"])),
                ).fetchone()
                if version is None:
                    continue
                state = self._state_in_db(db, tenant_id, str(version["id"]))
                item = self._version_row(version, state=state)
                rows.append(
                    {
                        "id": str(definition["id"]),
                        "created_by": str(definition["created_by"]),
                        "created_at": float(definition["created_at"]),
                        "latest_version": item,
                    }
                )
        return rows

    def get(self, procedure_id: str, *, tenant_id: str) -> dict[str, Any]:
        with self._conn() as db:
            definition = self._assert_procedure(db, tenant_id, procedure_id)
            versions = db.execute(
                "SELECT * FROM procedure_versions WHERE tenant_id=? AND procedure_id=? "
                "ORDER BY version_no DESC",
                (tenant_id, procedure_id),
            ).fetchall()
            items = [
                self._version_row(
                    row,
                    state=self._state_in_db(db, tenant_id, str(row["id"])),
                )
                for row in versions
            ]
        return {
            "id": str(definition["id"]),
            "created_by": str(definition["created_by"]),
            "created_at": float(definition["created_at"]),
            "versions": items,
            "latest_version": items[0] if items else None,
            "authority": self.authority(),
        }

    def events(self, procedure_id: str, *, tenant_id: str) -> list[dict[str, Any]]:
        with self._conn() as db:
            self._assert_procedure(db, tenant_id, procedure_id)
            rows = db.execute(
                "SELECT id,version_id,event_type,actor_user_id,actor_role,note,payload_json,created_at "
                "FROM procedure_events WHERE tenant_id=? AND procedure_id=? ORDER BY id",
                (tenant_id, procedure_id),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "version_id": str(row["version_id"]),
                "event_type": str(row["event_type"]),
                "actor_user_id": str(row["actor_user_id"]),
                "actor_role": str(row["actor_role"]),
                "note": str(row["note"]),
                "payload": json.loads(str(row["payload_json"])),
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    def transition(
        self,
        procedure_id: str,
        version_id: str,
        *,
        tenant_id: str,
        actor_user_id: str,
        actor_role: str,
        target_state: str,
        note: str = "",
    ) -> dict[str, Any]:
        if target_state not in {"evaluation_candidate", "catalog_published"}:
            raise ValueError("procedure transition is invalid")
        expected = "draft" if target_state == "evaluation_candidate" else "evaluation_candidate"
        now = time.time()
        with self._lock, self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._assert_version(db, tenant_id, procedure_id, version_id)
            current = self._state_in_db(db, tenant_id, version_id)
            if current == target_state:
                return self._version_row(row, state=current)
            if current != expected:
                raise ValueError(f"procedure version must be {expected} before {target_state}")
            self._insert_event(
                db,
                tenant_id=tenant_id,
                procedure_id=procedure_id,
                version_id=version_id,
                event_type=target_state,
                actor_user_id=actor_user_id,
                actor_role=actor_role,
                note=note,
                payload={"from": current, "to": target_state},
                now=now,
            )
        return self.version(procedure_id, version_id, tenant_id=tenant_id)

    def version(self, procedure_id: str, version_id: str, *, tenant_id: str) -> dict[str, Any]:
        with self._conn() as db:
            row = self._assert_version(db, tenant_id, procedure_id, version_id)
            state = self._state_in_db(db, tenant_id, version_id)
        return self._version_row(row, state=state)

    def catalog(self, *, tenant_id: str, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self._conn() as db:
            rows = db.execute(
                "SELECT v.* FROM procedure_versions v "
                "JOIN procedure_events e ON e.version_id=v.id AND e.tenant_id=v.tenant_id "
                "WHERE v.tenant_id=? AND e.event_type='catalog_published' "
                "AND e.id=(SELECT MAX(e2.id) FROM procedure_events e2 "
                "WHERE e2.tenant_id=v.tenant_id AND e2.version_id=v.id "
                "AND e2.event_type IN ('draft','evaluation_candidate','catalog_published')) "
                "ORDER BY e.id DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        return [self._version_row(row, state="catalog_published") for row in rows]

    def skill_candidate(
        self,
        procedure_id: str,
        version_id: str,
        *,
        tenant_id: str,
    ) -> dict[str, Any]:
        version = self.version(procedure_id, version_id, tenant_id=tenant_id)
        if version["state"] not in {"evaluation_candidate", "catalog_published"}:
            raise ValueError("draft procedure version is not exportable")
        spec = version["spec"]
        step_guidance = "\n".join(
            f"{index + 1}. {step['title']}: {step['instruction']}"
            for index, step in enumerate(spec.get("steps") or [])
        )
        guidance_parts = [
            str(spec.get("purpose") or "").strip(),
            str(spec.get("guidance") or "").strip(),
            step_guidance.strip(),
        ]
        guidance = "\n\n".join(part for part in guidance_parts if part)[:12000]
        body = {
            "schema_version": 1,
            "source": {
                "kind": "procedure_studio",
                "procedure_id": procedure_id,
                "version_id": version_id,
                "version_no": version["version_no"],
                "content_hash": version["content_hash"],
                "state": version["state"],
            },
            "candidate": {
                "domain": spec["domain"],
                "name": spec["name"],
                "guidance": guidance,
                "preferred_tools": list(spec.get("preferred_tools") or []),
                "trigger_terms": list(spec.get("trigger_terms") or []),
                "required_evidence": list(spec.get("required_evidence") or []),
                "prohibited_actions": list(spec.get("prohibited_actions") or []),
                "output_contract": str(spec.get("output_contract") or ""),
            },
            "authority": self.authority(),
        }
        body["candidate_id"] = f"skill-candidate-{_content_hash(body)[:20]}"
        body["export_hash"] = _content_hash(body)
        return body
