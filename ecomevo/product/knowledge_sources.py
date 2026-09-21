from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable


AUTHORABLE_TIERS = {"S2", "S4"}
EVENT_TYPES = {"created", "reviewed", "published", "superseded", "retired"}
DOMAINS = {
    "product_governance",
    "merchant_review",
    "aftersales",
    "risk_review",
    "content_audit",
    "general",
}
_WORD_RE = re.compile(r"[A-Za-z0-9_./:-]{2,}|[\u4e00-\u9fff]{2,}")


def authority_contract() -> dict[str, bool]:
    return {
        "changes_runtime_evidence": False,
        "changes_production_authority": False,
        "changes_policy": False,
        "changes_routing": False,
        "executes_tools": False,
        "grants_action_authority": False,
        "eligible_for_runtime_evidence": False,
        "s1_assignment_allowed": False,
        "s3_assignment_allowed": False,
        "open_web_unlocks_high_impact_actions": False,
    }


def source_hierarchy() -> list[dict[str, Any]]:
    return [
        {
            "tier": "S1",
            "label": "Authoritative internal systems",
            "authorable_here": False,
            "note": "只能来自受控业务系统 / Data Connections；Knowledge Center 不能人工声明为 S1。",
        },
        {
            "tier": "S2",
            "label": "Controlled knowledge",
            "authorable_here": True,
            "note": "受版本、评审、有效期与负责人治理的内部知识。",
        },
        {
            "tier": "S3",
            "label": "Submitted evidence",
            "authorable_here": False,
            "note": "只能来自具体任务中的提交证据；Knowledge Center 不能替代任务证据链。",
        },
        {
            "tier": "S4",
            "label": "External reference",
            "authorable_here": True,
            "note": "外部参考资料。即使发布，也不能单独解锁高影响操作。",
        },
    ]


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clean_list(values: Iterable[str], limit: int, *, item_limit: int = 120) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        value = value[:item_limit]
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


class KnowledgeSourceStore:
    """Tenant-scoped immutable knowledge source versions.

    This catalog governs source identity, review, freshness and publication. It does not
    inject content into a conversation, runtime evidence set, verifier input, policy,
    routing or BusinessAction authority.
    """

    def __init__(self, db_path: str | Path):
        self.path = str(db_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15.0, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def _init(self) -> None:
        with self._lock, self._conn() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS knowledge_sources(
                    source_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    source_tier TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    description TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    jurisdiction TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_sources_tenant
                    ON knowledge_sources(tenant_id,created_at DESC);

                CREATE TABLE IF NOT EXISTS knowledge_versions(
                    version_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    content_text TEXT NOT NULL,
                    effective_from REAL,
                    effective_until REAL,
                    review_due_at REAL,
                    provenance TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(source_id,version),
                    UNIQUE(source_id,content_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_versions_source
                    ON knowledge_versions(tenant_id,source_id,version DESC);

                CREATE TABLE IF NOT EXISTS knowledge_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_events_version
                    ON knowledge_events(tenant_id,version_id,id ASC);
                """
            )

    @staticmethod
    def _validate_source(
        *,
        name: str,
        source_tier: str,
        domain: str,
        description: str,
        owner: str,
        jurisdiction: str,
        tags: Iterable[str],
    ) -> dict[str, Any]:
        name = str(name or "").strip()
        tier = str(source_tier or "").strip().upper()
        domain = str(domain or "").strip()
        description = str(description or "").strip()
        owner = str(owner or "").strip()
        jurisdiction = str(jurisdiction or "").strip()
        if not name or len(name) > 180:
            raise ValueError("source name must be 1..180 characters")
        if tier not in AUTHORABLE_TIERS:
            raise ValueError("Knowledge Center can author only S2 controlled knowledge or S4 external reference")
        if domain not in DOMAINS:
            raise ValueError("invalid knowledge domain")
        if len(description) > 3000:
            raise ValueError("source description too long")
        if not owner or len(owner) > 180:
            raise ValueError("source owner must be 1..180 characters")
        if len(jurisdiction) > 180:
            raise ValueError("jurisdiction too long")
        return {
            "name": name,
            "source_tier": tier,
            "domain": domain,
            "description": description,
            "owner": owner,
            "jurisdiction": jurisdiction,
            "tags": _clean_list(tags, 20),
        }

    @staticmethod
    def _validate_version(
        *,
        title: str,
        content_text: str,
        effective_from: float | None,
        effective_until: float | None,
        review_due_at: float | None,
        provenance: str,
    ) -> dict[str, Any]:
        title = str(title or "").strip()
        content = str(content_text or "").strip()
        provenance = str(provenance or "").strip()
        if not title or len(title) > 240:
            raise ValueError("version title must be 1..240 characters")
        if len(content) < 20 or len(content) > 200_000:
            raise ValueError("knowledge content must be 20..200000 characters")
        if not provenance or len(provenance) > 2000:
            raise ValueError("provenance must be 1..2000 characters")
        start = float(effective_from) if effective_from is not None else None
        end = float(effective_until) if effective_until is not None else None
        review = float(review_due_at) if review_due_at is not None else None
        if any(value is not None and not math.isfinite(value) for value in (start, end, review)):
            raise ValueError("knowledge lifecycle timestamps must be finite")
        if start is not None and end is not None and end <= start:
            raise ValueError("effective_until must be later than effective_from")
        return {
            "title": title,
            "content_text": content,
            "effective_from": start,
            "effective_until": end,
            "review_due_at": review,
            "provenance": provenance,
        }

    @staticmethod
    def _decode_source(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        return {
            "source_id": str(item["source_id"]),
            "tenant_id": str(item["tenant_id"]),
            "name": str(item["name"]),
            "source_tier": str(item["source_tier"]),
            "domain": str(item["domain"]),
            "description": str(item["description"]),
            "owner": str(item["owner"]),
            "jurisdiction": str(item["jurisdiction"]),
            "tags": list(json.loads(str(item["tags_json"]))),
            "created_by": str(item["created_by"]),
            "created_at": float(item["created_at"]),
        }

    @staticmethod
    def _decode_version(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        return {
            "version_id": str(item["version_id"]),
            "source_id": str(item["source_id"]),
            "tenant_id": str(item["tenant_id"]),
            "version": int(item["version"]),
            "title": str(item["title"]),
            "content_text": str(item["content_text"]),
            "effective_from": float(item["effective_from"]) if item["effective_from"] is not None else None,
            "effective_until": float(item["effective_until"]) if item["effective_until"] is not None else None,
            "review_due_at": float(item["review_due_at"]) if item["review_due_at"] is not None else None,
            "provenance": str(item["provenance"]),
            "content_hash": str(item["content_hash"]),
            "created_by": str(item["created_by"]),
            "created_at": float(item["created_at"]),
        }

    def _events(self, tenant_id: str, version_id: str) -> list[dict[str, Any]]:
        with self._conn() as db:
            rows = db.execute(
                """
                SELECT id,event_type,actor_id,note,created_at
                FROM knowledge_events
                WHERE tenant_id=? AND version_id=?
                ORDER BY id ASC
                """,
                (tenant_id, version_id),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "event_type": str(row["event_type"]),
                "actor_id": str(row["actor_id"]),
                "note": str(row["note"]),
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    @staticmethod
    def _state(events: list[dict[str, Any]]) -> str:
        state = "draft"
        for event in events:
            kind = event["event_type"]
            if kind == "reviewed":
                state = "reviewed"
            elif kind == "published":
                state = "published"
            elif kind == "superseded":
                state = "superseded"
            elif kind == "retired":
                state = "retired"
        return state

    @staticmethod
    def _freshness(version: dict[str, Any], *, now: float | None = None) -> str:
        current = time.time() if now is None else float(now)
        end = version.get("effective_until")
        review_due = version.get("review_due_at")
        start = version.get("effective_from")
        if start is not None and current < float(start):
            return "future"
        if end is not None and current >= float(end):
            return "expired"
        if review_due is not None and current >= float(review_due):
            return "review_due"
        return "current"

    def _with_state(self, version: dict[str, Any]) -> dict[str, Any]:
        events = self._events(version["tenant_id"], version["version_id"])
        return {
            **version,
            "state": self._state(events),
            "freshness": self._freshness(version),
            "events": events,
            "authority": authority_contract(),
        }

    def _source_row(self, tenant_id: str, source_id: str) -> sqlite3.Row | None:
        with self._conn() as db:
            return db.execute(
                "SELECT * FROM knowledge_sources WHERE tenant_id=? AND source_id=?",
                (tenant_id, source_id),
            ).fetchone()

    def create_source(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        name: str,
        source_tier: str,
        domain: str,
        description: str,
        owner: str,
        jurisdiction: str,
        tags: Iterable[str],
        version: dict[str, Any],
    ) -> dict[str, Any]:
        tenant = str(tenant_id or "").strip()
        if not tenant:
            raise ValueError("tenant_id is required")
        source = self._validate_source(
            name=name,
            source_tier=source_tier,
            domain=domain,
            description=description,
            owner=owner,
            jurisdiction=jurisdiction,
            tags=tags,
        )
        clean_version = self._validate_version(**version)
        source_id = f"knowledge-{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            db.execute(
                """
                INSERT INTO knowledge_sources(
                    source_id,tenant_id,name,source_tier,domain,description,owner,
                    jurisdiction,tags_json,created_by,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    source_id,
                    tenant,
                    source["name"],
                    source["source_tier"],
                    source["domain"],
                    source["description"],
                    source["owner"],
                    source["jurisdiction"],
                    _stable_json(source["tags"]),
                    str(actor_id),
                    now,
                ),
            )
            version_id = self._insert_version(
                db,
                tenant_id=tenant,
                source_id=source_id,
                version_no=1,
                actor_id=actor_id,
                payload=clean_version,
            )
        return self.get_source(tenant, source_id, include_content=True)  # type: ignore[return-value]

    def _insert_version(
        self,
        db: sqlite3.Connection,
        *,
        tenant_id: str,
        source_id: str,
        version_no: int,
        actor_id: str,
        payload: dict[str, Any],
    ) -> str:
        canonical = {
            "title": payload["title"],
            "content_text": payload["content_text"],
            "effective_from": payload["effective_from"],
            "effective_until": payload["effective_until"],
            "review_due_at": payload["review_due_at"],
            "provenance": payload["provenance"],
        }
        digest = _content_hash(canonical)
        version_id = f"{source_id}@v{int(version_no)}"
        now = time.time()
        try:
            db.execute(
                """
                INSERT INTO knowledge_versions(
                    version_id,source_id,tenant_id,version,title,content_text,
                    effective_from,effective_until,review_due_at,provenance,content_hash,
                    created_by,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    version_id,
                    source_id,
                    tenant_id,
                    int(version_no),
                    payload["title"],
                    payload["content_text"],
                    payload["effective_from"],
                    payload["effective_until"],
                    payload["review_due_at"],
                    payload["provenance"],
                    digest,
                    str(actor_id),
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("identical knowledge version already exists for this source") from exc
        db.execute(
            """
            INSERT INTO knowledge_events(
                tenant_id,source_id,version_id,event_type,actor_id,note,created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (tenant_id, source_id, version_id, "created", str(actor_id), "", now),
        )
        return version_id

    def create_version(
        self,
        source_id: str,
        *,
        tenant_id: str,
        actor_id: str,
        title: str,
        content_text: str,
        effective_from: float | None,
        effective_until: float | None,
        review_due_at: float | None,
        provenance: str,
    ) -> dict[str, Any]:
        clean = self._validate_version(
            title=title,
            content_text=content_text,
            effective_from=effective_from,
            effective_until=effective_until,
            review_due_at=review_due_at,
            provenance=provenance,
        )
        with self._lock, self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            source = db.execute(
                "SELECT source_id FROM knowledge_sources WHERE tenant_id=? AND source_id=?",
                (tenant_id, source_id),
            ).fetchone()
            if source is None:
                raise KeyError(source_id)
            row = db.execute(
                "SELECT MAX(version) AS max_version FROM knowledge_versions WHERE tenant_id=? AND source_id=?",
                (tenant_id, source_id),
            ).fetchone()
            next_version = int(row["max_version"] or 0) + 1
            version_id = self._insert_version(
                db,
                tenant_id=tenant_id,
                source_id=source_id,
                version_no=next_version,
                actor_id=actor_id,
                payload=clean,
            )
        return self.get_version(tenant_id, version_id)  # type: ignore[return-value]

    def get_version(self, tenant_id: str, version_id: str) -> dict[str, Any] | None:
        with self._conn() as db:
            row = db.execute(
                "SELECT * FROM knowledge_versions WHERE tenant_id=? AND version_id=?",
                (tenant_id, version_id),
            ).fetchone()
        return self._with_state(self._decode_version(row)) if row else None

    def _append_event(
        self,
        db: sqlite3.Connection,
        *,
        tenant_id: str,
        source_id: str,
        version_id: str,
        event_type: str,
        actor_id: str,
        note: str,
    ) -> None:
        if event_type not in EVENT_TYPES:
            raise ValueError("invalid knowledge event")
        db.execute(
            """
            INSERT INTO knowledge_events(
                tenant_id,source_id,version_id,event_type,actor_id,note,created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                tenant_id,
                source_id,
                version_id,
                event_type,
                str(actor_id),
                str(note or "")[:2000],
                time.time(),
            ),
        )

    def transition(
        self,
        version_id: str,
        event_type: str,
        *,
        tenant_id: str,
        actor_id: str,
        note: str = "",
    ) -> dict[str, Any]:
        if event_type not in {"reviewed", "published", "retired"}:
            raise ValueError("invalid lifecycle transition")
        with self._lock, self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM knowledge_versions WHERE tenant_id=? AND version_id=?",
                (tenant_id, version_id),
            ).fetchone()
            if row is None:
                raise KeyError(version_id)
            version = self._decode_version(row)
            event_rows = db.execute(
                "SELECT event_type FROM knowledge_events WHERE tenant_id=? AND version_id=? ORDER BY id",
                (tenant_id, version_id),
            ).fetchall()
            state = self._state([{"event_type": str(item["event_type"])} for item in event_rows])
            if event_type == "reviewed" and state != "draft":
                raise ValueError("only draft versions can be reviewed")
            if event_type == "published" and state != "reviewed":
                raise ValueError("only reviewed versions can be published")
            if event_type == "retired" and state not in {"reviewed", "published"}:
                raise ValueError("only reviewed or published versions can be retired")

            if event_type == "published":
                latest = db.execute(
                    "SELECT MAX(version) AS max_version FROM knowledge_versions WHERE tenant_id=? AND source_id=?",
                    (tenant_id, version["source_id"]),
                ).fetchone()
                if latest is None or int(latest["max_version"] or 0) != int(version["version"]):
                    raise ValueError("only the latest knowledge version can be published; create a new version to roll forward")
                published_rows = db.execute(
                    """
                    SELECT v.version_id
                    FROM knowledge_versions v
                    WHERE v.tenant_id=? AND v.source_id=? AND v.version_id<>?
                    ORDER BY v.version DESC
                    """,
                    (tenant_id, version["source_id"], version_id),
                ).fetchall()
                for published_row in published_rows:
                    other_id = str(published_row["version_id"])
                    other_events = db.execute(
                        "SELECT event_type FROM knowledge_events WHERE tenant_id=? AND version_id=? ORDER BY id",
                        (tenant_id, other_id),
                    ).fetchall()
                    other_state = self._state([{"event_type": str(item["event_type"])} for item in other_events])
                    if other_state == "published":
                        self._append_event(
                            db,
                            tenant_id=tenant_id,
                            source_id=version["source_id"],
                            version_id=other_id,
                            event_type="superseded",
                            actor_id=actor_id,
                            note=f"superseded by {version_id}",
                        )
            self._append_event(
                db,
                tenant_id=tenant_id,
                source_id=version["source_id"],
                version_id=version_id,
                event_type=event_type,
                actor_id=actor_id,
                note=note,
            )
        return self.get_version(tenant_id, version_id)  # type: ignore[return-value]

    def get_source(
        self,
        tenant_id: str,
        source_id: str,
        *,
        include_content: bool = False,
    ) -> dict[str, Any] | None:
        row = self._source_row(tenant_id, source_id)
        if row is None:
            return None
        source = self._decode_source(row)
        with self._conn() as db:
            versions = db.execute(
                "SELECT * FROM knowledge_versions WHERE tenant_id=? AND source_id=? ORDER BY version DESC",
                (tenant_id, source_id),
            ).fetchall()
        decoded = [self._with_state(self._decode_version(item)) for item in versions]
        if not include_content:
            decoded = [
                {key: value for key, value in item.items() if key != "content_text"}
                for item in decoded
            ]
        current = next((item for item in decoded if item["state"] == "published"), None)
        return {
            **source,
            "versions": decoded,
            "current_published_version_id": current["version_id"] if current else None,
            "authority": authority_contract(),
        }

    def list_sources(self, tenant_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT * FROM knowledge_sources WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, max(1, min(200, int(limit)))),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = self.get_source(tenant_id, str(row["source_id"]), include_content=False)
            if item is not None:
                out.append(item)
        return out

    def search_published(
        self,
        tenant_id: str,
        query: str,
        *,
        domain: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        words = [token.casefold() for token in _WORD_RE.findall(str(query or ""))][:24]
        if not words:
            return []
        candidates = self.list_sources(tenant_id, limit=200)
        scored: list[tuple[float, dict[str, Any]]] = []
        for source in candidates:
            if domain and source["domain"] != domain:
                continue
            version_id = source.get("current_published_version_id")
            if not version_id:
                continue
            version = self.get_version(tenant_id, str(version_id))
            if version is None or version["freshness"] in {"expired", "future"}:
                continue
            haystack = " ".join(
                [
                    source["name"],
                    source["description"],
                    " ".join(source["tags"]),
                    version["title"],
                    version["content_text"],
                ]
            ).casefold()
            hits = sum(1 for word in words if word in haystack)
            if not hits:
                continue
            exact = str(query or "").strip().casefold()
            score = float(hits) + (2.5 if exact and exact in haystack else 0.0)
            scored.append(
                (
                    score,
                    {
                        "source_id": source["source_id"],
                        "version_id": version["version_id"],
                        "name": source["name"],
                        "source_tier": source["source_tier"],
                        "domain": source["domain"],
                        "title": version["title"],
                        "freshness": version["freshness"],
                        "score": round(score, 3),
                        "matched_terms": [word for word in words if word in haystack][:12],
                        "excerpt": version["content_text"][:900],
                        "content_hash": version["content_hash"],
                        "authority": authority_contract(),
                    },
                )
            )
        scored.sort(key=lambda item: (-item[0], item[1]["source_id"]))
        return [item for _score, item in scored[: max(1, min(50, int(limit)))]]

    def retrieval_projection(self, tenant_id: str, version_id: str) -> dict[str, Any]:
        version = self.get_version(tenant_id, version_id)
        if version is None:
            raise KeyError(version_id)
        source = self.get_source(tenant_id, version["source_id"], include_content=False)
        if source is None:
            raise KeyError(version["source_id"])
        return {
            "schema_version": 1,
            "source_id": source["source_id"],
            "version_id": version["version_id"],
            "version": version["version"],
            "name": source["name"],
            "source_tier": source["source_tier"],
            "domain": source["domain"],
            "content_hash": version["content_hash"],
            "state": version["state"],
            "freshness": version["freshness"],
            "provenance": version["provenance"],
            "runtime_projection_status": "blocked_pending_explicit_source_integration_gate",
            "authority": authority_contract(),
        }

    def catalog(self, tenant_id: str) -> dict[str, Any]:
        items = self.list_sources(tenant_id, limit=200)
        return {
            "tenant_scope": tenant_id,
            "count": len(items),
            "items": items,
            "source_hierarchy": source_hierarchy(),
            "authority": authority_contract(),
        }
