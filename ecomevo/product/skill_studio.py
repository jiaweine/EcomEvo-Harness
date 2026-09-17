from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable


DOMAINS = {
    "product_governance",
    "merchant_review",
    "aftersales",
    "risk_review",
    "content_audit",
    "general",
}
EVENTS = {"created", "submitted", "evaluation_linked", "archived"}


def _clean_list(values: Iterable[str], limit: int) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))[:limit]


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def authority_contract() -> dict[str, bool]:
    return {
        "studio_changes_runtime_skill": False,
        "studio_changes_routing": False,
        "studio_changes_policy": False,
        "studio_grants_action_authority": False,
        "evaluation_link_auto_promotes": False,
        "can_promote_runtime": False,
    }


class SkillStudioStore:
    """Immutable human-authored procedure versions plus append-only review events.

    Studio state is stored separately from ``runtime_skills``. Runtime skills remain
    governed by AdaptiveSkillLibrary shadow/outcome promotion. A Studio version may be
    evaluated and reviewed, but this class has no path that can make it active in runtime.
    """

    def __init__(self, db_path: str | Path, runtime_skills: Any, tools: Any, evaluation_center: Any):
        self.path = str(db_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.runtime_skills = runtime_skills
        self.tools = tools
        self.evaluation_center = evaluation_center
        self._lock = threading.RLock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15.0, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def _init(self) -> None:
        with self._lock, self._conn() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS studio_skill_versions(
                    version_id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    domain TEXT NOT NULL,
                    name TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    guidance TEXT NOT NULL,
                    preferred_tools_json TEXT NOT NULL,
                    trigger_terms_json TEXT NOT NULL,
                    input_contract_json TEXT NOT NULL,
                    output_contract_json TEXT NOT NULL,
                    safety_notes TEXT NOT NULL,
                    source_skill_id TEXT,
                    content_hash TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(family_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_studio_skill_versions_family
                    ON studio_skill_versions(family_id, version DESC);
                CREATE INDEX IF NOT EXISTS idx_studio_skill_versions_domain
                    ON studio_skill_versions(domain, created_at DESC);
                CREATE TABLE IF NOT EXISTS studio_skill_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_studio_skill_events_version
                    ON studio_skill_events(version_id, id ASC);
                """
            )

    def _tool_keys(self) -> set[str]:
        describe = getattr(self.tools, "describe", None)
        if not callable(describe):
            return set()
        return {str(row.get("key")) for row in describe() if str(row.get("key") or "")}

    def _validate_payload(
        self,
        *,
        domain: str,
        name: str,
        purpose: str,
        guidance: str,
        preferred_tools: Iterable[str],
        trigger_terms: Iterable[str],
        input_contract: dict[str, Any] | None,
        output_contract: dict[str, Any] | None,
        safety_notes: str,
        source_skill_id: str | None,
    ) -> dict[str, Any]:
        domain = str(domain).strip()
        if domain not in DOMAINS:
            raise ValueError("invalid skill domain")
        name = str(name).strip()
        purpose = str(purpose).strip()
        guidance = str(guidance).strip()
        safety_notes = str(safety_notes or "").strip()
        if not name or len(name) > 120:
            raise ValueError("skill name must be 1..120 characters")
        if not purpose or len(purpose) > 600:
            raise ValueError("skill purpose must be 1..600 characters")
        if len(guidance) < 10 or len(guidance) > 6000:
            raise ValueError("skill guidance must be 10..6000 characters")
        if len(safety_notes) > 3000:
            raise ValueError("safety notes too long")
        preferred = _clean_list(preferred_tools, 8)
        unknown = [tool for tool in preferred if tool not in self._tool_keys()]
        if unknown:
            raise ValueError(f"unknown preferred tools: {', '.join(unknown)}")
        triggers = _clean_list(trigger_terms, 16)
        if not triggers:
            raise ValueError("at least one trigger term is required")
        input_contract = dict(input_contract or {})
        output_contract = dict(output_contract or {})
        if len(_stable_json(input_contract)) > 12000 or len(_stable_json(output_contract)) > 12000:
            raise ValueError("input/output contract too large")
        source_skill_id = str(source_skill_id).strip() if source_skill_id else None
        if source_skill_id:
            getter = getattr(self.runtime_skills, "get", None)
            if not callable(getter) or getter(source_skill_id) is None:
                raise ValueError("source runtime skill does not exist")
        return {
            "domain": domain,
            "name": name,
            "purpose": purpose,
            "guidance": guidance,
            "preferred_tools": preferred,
            "trigger_terms": triggers,
            "input_contract": input_contract,
            "output_contract": output_contract,
            "safety_notes": safety_notes,
            "source_skill_id": source_skill_id,
        }

    @staticmethod
    def _decode_version(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        return {
            "version_id": str(item["version_id"]),
            "family_id": str(item["family_id"]),
            "version": int(item["version"]),
            "domain": str(item["domain"]),
            "name": str(item["name"]),
            "purpose": str(item["purpose"]),
            "guidance": str(item["guidance"]),
            "preferred_tools": json.loads(str(item["preferred_tools_json"])),
            "trigger_terms": json.loads(str(item["trigger_terms_json"])),
            "input_contract": json.loads(str(item["input_contract_json"])),
            "output_contract": json.loads(str(item["output_contract_json"])),
            "safety_notes": str(item["safety_notes"]),
            "source_skill_id": item["source_skill_id"],
            "content_hash": str(item["content_hash"]),
            "created_by": str(item["created_by"]),
            "created_at": float(item["created_at"]),
        }

    def _events(self, version_id: str) -> list[dict[str, Any]]:
        with self._conn() as connection:
            rows = connection.execute(
                "SELECT id,event_type,actor_id,payload_json,created_at FROM studio_skill_events "
                "WHERE version_id=? ORDER BY id ASC",
                (str(version_id),),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "event_type": str(row["event_type"]),
                "actor_id": str(row["actor_id"]),
                "payload": json.loads(str(row["payload_json"])),
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    @staticmethod
    def _state(events: list[dict[str, Any]]) -> dict[str, Any]:
        state = "draft"
        evaluation: dict[str, Any] | None = None
        archived = False
        for event in events:
            kind = event["event_type"]
            if kind == "submitted":
                state = "review"
            elif kind == "evaluation_linked":
                evaluation = dict(event["payload"])
                state = "evaluated_pass" if evaluation.get("ok") else "evaluated_fail"
            elif kind == "archived":
                archived = True
                state = "archived"
        return {"state": state, "evaluation": evaluation, "archived": archived}

    def _with_state(self, version: dict[str, Any]) -> dict[str, Any]:
        events = self._events(version["version_id"])
        return {
            **version,
            **self._state(events),
            "events": events,
            "authority": authority_contract(),
        }

    def _insert_version(
        self,
        connection: sqlite3.Connection,
        family_id: str,
        version: int,
        *,
        actor_id: str,
        payload: dict[str, Any],
    ) -> str:
        clean = self._validate_payload(**payload)
        canonical = {"family_id": family_id, "version": int(version), **clean}
        digest = _content_hash(canonical)
        version_id = f"{family_id}@v{version}"
        now = time.time()
        connection.execute(
            "INSERT INTO studio_skill_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                version_id,
                family_id,
                int(version),
                clean["domain"],
                clean["name"],
                clean["purpose"],
                clean["guidance"],
                _stable_json(clean["preferred_tools"]),
                _stable_json(clean["trigger_terms"]),
                _stable_json(clean["input_contract"]),
                _stable_json(clean["output_contract"]),
                clean["safety_notes"],
                clean["source_skill_id"],
                digest,
                str(actor_id),
                now,
            ),
        )
        connection.execute(
            "INSERT INTO studio_skill_events(version_id,event_type,actor_id,payload_json,created_at) VALUES(?,?,?,?,?)",
            (version_id, "created", str(actor_id), "{}", now),
        )
        return version_id

    def create_family(self, *, actor_id: str, **payload: Any) -> dict[str, Any]:
        family_id = f"procedure-{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            version_id = self._insert_version(
                connection,
                family_id,
                1,
                actor_id=actor_id,
                payload=payload,
            )
        return self.get_version(version_id)  # type: ignore[return-value]

    def create_version(self, family_id: str, *, actor_id: str, **payload: Any) -> dict[str, Any]:
        family_id = str(family_id).strip()
        if not family_id:
            raise ValueError("family_id is required")
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT MAX(version) AS max_version FROM studio_skill_versions WHERE family_id=?",
                (family_id,),
            ).fetchone()
            if row is None or row["max_version"] is None:
                raise KeyError(family_id)
            version_id = self._insert_version(
                connection,
                family_id,
                int(row["max_version"]) + 1,
                actor_id=actor_id,
                payload=payload,
            )
        return self.get_version(version_id)  # type: ignore[return-value]

    def get_version(self, version_id: str) -> dict[str, Any] | None:
        with self._conn() as connection:
            row = connection.execute(
                "SELECT * FROM studio_skill_versions WHERE version_id=?",
                (str(version_id),),
            ).fetchone()
        return self._with_state(self._decode_version(row)) if row else None

    def list_versions(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        with self._conn() as connection:
            rows = connection.execute(
                "SELECT * FROM studio_skill_versions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._with_state(self._decode_version(row)) for row in rows]

    def latest_families(self, limit: int = 100) -> list[dict[str, Any]]:
        versions = self.list_versions(limit=max(200, limit * 4))
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for version in versions:
            family_id = version["family_id"]
            if family_id in seen:
                continue
            seen.add(family_id)
            out.append(version)
            if len(out) >= limit:
                break
        return out

    def submit(self, version_id: str, *, actor_id: str, note: str = "") -> dict[str, Any]:
        current = self.get_version(version_id)
        if current is None:
            raise KeyError(version_id)
        if current["state"] != "draft":
            raise ValueError("only draft versions can be submitted")
        self._append_event(version_id, "submitted", actor_id, {"note": str(note)[:2000]})
        return self.get_version(version_id)  # type: ignore[return-value]

    def link_evaluation(self, version_id: str, run_id: str, *, actor_id: str) -> dict[str, Any]:
        current = self.get_version(version_id)
        if current is None:
            raise KeyError(version_id)
        if current["state"] not in {"review", "evaluated_pass", "evaluated_fail"}:
            raise ValueError("submit the version before linking evaluation")
        run = self.evaluation_center.store.get_run(str(run_id))
        if run is None:
            raise ValueError("evaluation run does not exist")
        summary = dict(run.get("summary") or {})
        snapshot = {
            "run_id": str(run["id"]),
            "ok": bool(run.get("ok")),
            "source_hash": str(run.get("source_hash") or ""),
            "case_count": int(run.get("case_count") or summary.get("case_count") or 0),
            "failed_case_count": int(summary.get("failed_case_count") or 0),
            "drift_case_count": int(summary.get("drift_case_count") or 0),
            "run_created_at": float(run.get("created_at") or 0),
        }
        self._append_event(version_id, "evaluation_linked", actor_id, snapshot)
        return self.get_version(version_id)  # type: ignore[return-value]

    def archive(self, version_id: str, *, actor_id: str, note: str = "") -> dict[str, Any]:
        current = self.get_version(version_id)
        if current is None:
            raise KeyError(version_id)
        if current["state"] == "archived":
            return current
        self._append_event(version_id, "archived", actor_id, {"note": str(note)[:2000]})
        return self.get_version(version_id)  # type: ignore[return-value]

    def _append_event(self, version_id: str, event_type: str, actor_id: str, payload: dict[str, Any]) -> None:
        if event_type not in EVENTS:
            raise ValueError("invalid studio event")
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM studio_skill_versions WHERE version_id=?",
                (str(version_id),),
            ).fetchone()
            if not exists:
                raise KeyError(version_id)
            connection.execute(
                "INSERT INTO studio_skill_events(version_id,event_type,actor_id,payload_json,created_at) VALUES(?,?,?,?,?)",
                (str(version_id), event_type, str(actor_id), _stable_json(payload), time.time()),
            )

    def runtime_catalog(self, limit: int = 100) -> list[dict[str, Any]]:
        snapshot = getattr(self.runtime_skills, "snapshot", None)
        if not callable(snapshot):
            return []
        rows = snapshot(limit=max(1, min(200, int(limit))))
        return [
            {
                "skill_id": str(row.get("skill_id") or ""),
                "domain": str(row.get("domain") or ""),
                "name": str(row.get("name") or ""),
                "status": str(row.get("status") or ""),
                "guidance": str(row.get("guidance") or ""),
                "preferred_tools": list(row.get("preferred_tools") or []),
                "trigger_terms": list(row.get("trigger_terms") or []),
                "shadow_score": float(row.get("shadow_score") or 0),
                "uses": int(row.get("uses") or 0),
                "wins": int(row.get("wins") or 0),
                "losses": int(row.get("losses") or 0),
                "source_patch_id": row.get("source_patch_id"),
                "updated_at": float(row.get("updated_at") or 0),
            }
            for row in rows
        ]

    def runtime_policies(self) -> list[dict[str, Any]]:
        """Read already-existing evolution policies without invoking policy bootstrap."""
        path = str(getattr(self.runtime_skills, "path", "") or "")
        if not path or not Path(path).is_file():
            return []
        try:
            connection = sqlite3.connect(path, timeout=5.0)
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT domain,promotion_threshold,retirement_threshold,exploration,updates,updated_at "
                "FROM evolution_policy ORDER BY domain"
            ).fetchall()
            connection.close()
        except (sqlite3.Error, OSError):
            return []
        return [
            {
                "domain": str(row["domain"]),
                "promotion_threshold": float(row["promotion_threshold"]),
                "retirement_threshold": float(row["retirement_threshold"]),
                "exploration": float(row["exploration"]),
                "updates": int(row["updates"]),
                "updated_at": float(row["updated_at"]),
            }
            for row in rows
        ]

    def catalog(self) -> dict[str, Any]:
        return {
            "scope": "deployment",
            "runtime_skills": self.runtime_catalog(200),
            "studio_families": self.latest_families(100),
            "evolution_policies": self.runtime_policies(),
            "registered_tools": sorted(self._tool_keys()),
            "authority": authority_contract(),
        }
