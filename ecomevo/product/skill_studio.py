from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from ecomevo.evaluation import asset_for, load_cases, validate
from ecomevo.runtime import EcomEvoEngine


DOMAINS = {
    "product_governance",
    "merchant_review",
    "aftersales",
    "risk_review",
    "content_audit",
    "general",
}
EVENTS = {"created", "submitted", "candidate_evaluated", "archived"}


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
        "candidate_evaluation_mutates_production": False,
        "evaluation_pass_auto_promotes": False,
        "can_promote_runtime": False,
        "release_candidate_activates_runtime": False,
        "studio_cross_tenant_visibility": False,
    }


class SkillStudioStore:
    """Tenant-scoped immutable procedure versions with isolated candidate evaluation.

    Studio state is separate from runtime_skills. Candidate evaluation creates a
    temporary EcomEvo runtime, activates the immutable candidate only inside that temporary
    database, runs same-domain Gold Set cases twice, and discards the runtime afterwards.
    No Studio API can activate a production RuntimeSkill.
    """

    def __init__(self, db_path: str | Path, runtime_skills: Any, tools: Any):
        self.path = str(db_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.runtime_skills = runtime_skills
        self.tools = tools
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
                    tenant_id TEXT NOT NULL DEFAULT 'local',
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
                CREATE TABLE IF NOT EXISTS studio_skill_evaluations(
                    id TEXT PRIMARY KEY,
                    version_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_studio_skill_eval_version
                    ON studio_skill_evaluations(version_id, created_at DESC);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(studio_skill_versions)"
                ).fetchall()
            }
            if "tenant_id" not in columns:
                connection.execute(
                    "ALTER TABLE studio_skill_versions "
                    "ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'local'"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_studio_skill_versions_tenant "
                "ON studio_skill_versions(tenant_id,created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_studio_skill_family_tenant "
                "ON studio_skill_versions(tenant_id,family_id,version DESC)"
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

    def _events(self, version_id: str, *, tenant_id: str) -> list[dict[str, Any]]:
        with self._conn() as connection:
            rows = connection.execute(
                "SELECT e.id,e.event_type,e.actor_id,e.payload_json,e.created_at "
                "FROM studio_skill_events e "
                "JOIN studio_skill_versions v ON v.version_id=e.version_id "
                "WHERE e.version_id=? AND v.tenant_id=? ORDER BY e.id ASC",
                (str(version_id), str(tenant_id)),
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
            elif kind == "candidate_evaluated":
                evaluation = dict(event["payload"])
                state = "evaluated_pass" if evaluation.get("ok") else "evaluated_fail"
            elif kind == "archived":
                archived = True
        if archived:
            state = "archived"
        return {"state": state, "evaluation": evaluation, "archived": archived}

    def _with_state(self, version: dict[str, Any], *, tenant_id: str) -> dict[str, Any]:
        events = self._events(version["version_id"], tenant_id=tenant_id)
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
        tenant_id: str,
        actor_id: str,
        payload: dict[str, Any],
    ) -> str:
        clean = self._validate_payload(**payload)
        canonical = {"family_id": family_id, "version": int(version), **clean}
        digest = _content_hash(canonical)
        version_id = f"{family_id}@v{version}"
        now = time.time()
        connection.execute(
            "INSERT INTO studio_skill_versions"
            "(version_id,family_id,version,domain,name,purpose,guidance,preferred_tools_json,"
            "trigger_terms_json,input_contract_json,output_contract_json,safety_notes,source_skill_id,"
            "content_hash,created_by,created_at,tenant_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                str(tenant_id),
            ),
        )
        connection.execute(
            "INSERT INTO studio_skill_events(version_id,event_type,actor_id,payload_json,created_at) VALUES(?,?,?,?,?)",
            (version_id, "created", str(actor_id), "{}", now),
        )
        return version_id

    def create_family(
        self,
        *,
        actor_id: str,
        tenant_id: str = "local",
        **payload: Any,
    ) -> dict[str, Any]:
        family_id = f"procedure-{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            version_id = self._insert_version(
                connection,
                family_id,
                1,
                tenant_id=tenant_id,
                actor_id=actor_id,
                payload=payload,
            )
        return self.get_version(version_id, tenant_id=tenant_id)  # type: ignore[return-value]

    def create_version(
        self,
        family_id: str,
        *,
        actor_id: str,
        tenant_id: str = "local",
        **payload: Any,
    ) -> dict[str, Any]:
        family_id = str(family_id).strip()
        if not family_id:
            raise ValueError("family_id is required")
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT MAX(version) AS max_version FROM studio_skill_versions "
                "WHERE family_id=? AND tenant_id=?",
                (family_id, str(tenant_id)),
            ).fetchone()
            if row is None or row["max_version"] is None:
                raise KeyError(family_id)
            version_id = self._insert_version(
                connection,
                family_id,
                int(row["max_version"]) + 1,
                tenant_id=tenant_id,
                actor_id=actor_id,
                payload=payload,
            )
        return self.get_version(version_id, tenant_id=tenant_id)  # type: ignore[return-value]

    def get_version(
        self,
        version_id: str,
        *,
        tenant_id: str = "local",
    ) -> dict[str, Any] | None:
        with self._conn() as connection:
            row = connection.execute(
                "SELECT * FROM studio_skill_versions WHERE version_id=? AND tenant_id=?",
                (str(version_id), str(tenant_id)),
            ).fetchone()
        return (
            self._with_state(self._decode_version(row), tenant_id=tenant_id)
            if row
            else None
        )

    def list_versions(
        self,
        limit: int = 100,
        *,
        tenant_id: str = "local",
    ) -> list[dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        with self._conn() as connection:
            rows = connection.execute(
                "SELECT * FROM studio_skill_versions WHERE tenant_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (str(tenant_id), limit),
            ).fetchall()
        return [
            self._with_state(self._decode_version(row), tenant_id=tenant_id)
            for row in rows
        ]

    def latest_families(
        self,
        limit: int = 100,
        *,
        tenant_id: str = "local",
    ) -> list[dict[str, Any]]:
        versions = self.list_versions(
            limit=max(200, limit * 4),
            tenant_id=tenant_id,
        )
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

    def submit(
        self,
        version_id: str,
        *,
        actor_id: str,
        note: str = "",
        tenant_id: str = "local",
    ) -> dict[str, Any]:
        current = self.get_version(version_id, tenant_id=tenant_id)
        if current is None:
            raise KeyError(version_id)
        if current["state"] != "draft":
            raise ValueError("only draft versions can be submitted")
        self._append_event(
            version_id,
            "submitted",
            actor_id,
            {"note": str(note)[:2000]},
            tenant_id=tenant_id,
        )
        return self.get_version(version_id, tenant_id=tenant_id)  # type: ignore[return-value]

    @staticmethod
    def _case_snapshot(case: dict[str, Any], summary: Any, failures: list[str]) -> dict[str, Any]:
        return {
            "id": str(case["id"]),
            "status": str(summary.status),
            "missing_evidence": list(summary.missing_evidence),
            "action_count": len(summary.proposed_actions),
            "event_chain_valid": bool(summary.event_chain_valid),
            "failures": list(failures),
        }

    async def _run_candidate_evaluation(self, version: dict[str, Any]) -> dict[str, Any]:
        cases = [case for case in load_cases() if str(case["domain"]) == version["domain"]]
        if not cases:
            raise ValueError("no Gold Set cases cover this skill domain")
        phases: list[dict[str, Any]] = []
        ephemeral_skill_id = ""
        with tempfile.TemporaryDirectory(prefix="ecomevo-studio-eval-") as tmp:
            runtime_db = Path(tmp) / "runtime.db"
            for phase_name in ("fresh", "persisted_replay"):
                engine = EcomEvoEngine(runtime_db)
                if phase_name == "fresh":
                    candidate = engine.skills.upsert_candidate(
                        domain=version["domain"],
                        name=version["name"],
                        guidance=version["guidance"],
                        preferred_tools=list(version["preferred_tools"]),
                        trigger_terms=list(version["trigger_terms"]),
                        shadow_score=1.0,
                        source_patch_id=f"studio:{version['version_id']}:{version['content_hash']}",
                        promote=True,
                    )
                    ephemeral_skill_id = candidate.skill_id
                case_rows: list[dict[str, Any]] = []
                phase_failures: list[str] = []
                for case in cases:
                    assets = [asset_for(case)] if case.get("asset_text") else []
                    summary = await engine.run(
                        str(case["text"]),
                        assets,
                        domain_hint=str(case["domain"]),
                    )
                    failures = validate(case, summary)
                    case_rows.append(self._case_snapshot(case, summary, failures))
                    phase_failures.extend(f"{case['id']}: {failure}" for failure in failures)
                phases.append({"phase": phase_name, "cases": case_rows, "failures": phase_failures})

        first = {row["id"]: row for row in phases[0]["cases"]}
        second = {row["id"]: row for row in phases[1]["cases"]}
        comparisons: list[dict[str, Any]] = []
        for case_id in sorted(first):
            a = first[case_id]
            b = second[case_id]
            stable = (
                a["status"] == b["status"]
                and a["missing_evidence"] == b["missing_evidence"]
                and a["action_count"] == b["action_count"]
            )
            comparisons.append({"id": case_id, "stable": stable})
        failures = [
            f"{phase['phase']}: {failure}"
            for phase in phases
            for failure in phase["failures"]
        ]
        failed_case_ids = {
            str(row["id"])
            for phase in phases
            for row in phase["cases"]
            if row.get("failures")
        }
        drift_count = sum(1 for row in comparisons if not row["stable"])
        return {
            "ok": not failures and drift_count == 0,
            "candidate": {
                "version_id": version["version_id"],
                "content_hash": version["content_hash"],
                "domain": version["domain"],
                "ephemeral_runtime_skill_id": ephemeral_skill_id,
            },
            "isolation": {
                "temporary_runtime": True,
                "production_runtime_mutated": False,
                "production_skill_promoted": False,
            },
            "case_count": len(cases),
            "phase_count": len(phases),
            "failed_case_count": len(failed_case_ids),
            "drift_case_count": drift_count,
            "phases": phases,
            "comparisons": comparisons,
            "failures": failures,
        }

    async def evaluate(
        self,
        version_id: str,
        *,
        actor_id: str,
        tenant_id: str = "local",
    ) -> dict[str, Any]:
        current = self.get_version(version_id, tenant_id=tenant_id)
        if current is None:
            raise KeyError(version_id)
        if current["state"] not in {"review", "evaluated_pass", "evaluated_fail"}:
            raise ValueError("submit the version before evaluation")
        result = await self._run_candidate_evaluation(current)
        evaluation_id = f"studio-eval-{uuid.uuid4().hex[:16]}"
        created_at = time.time()
        event_payload = {
            "evaluation_id": evaluation_id,
            "ok": bool(result["ok"]),
            "content_hash": current["content_hash"],
            "case_count": int(result["case_count"]),
            "failed_case_count": int(result["failed_case_count"]),
            "drift_case_count": int(result["drift_case_count"]),
            "isolated_runtime": True,
        }
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT content_hash FROM studio_skill_versions "
                "WHERE version_id=? AND tenant_id=?",
                (str(version_id), str(tenant_id)),
            ).fetchone()
            if not exists:
                raise KeyError(version_id)
            if str(exists["content_hash"]) != current["content_hash"]:
                raise RuntimeError("immutable skill content changed during evaluation")
            connection.execute(
                "INSERT INTO studio_skill_evaluations"
                "(id,version_id,content_hash,result_json,created_by,created_at) VALUES(?,?,?,?,?,?)",
                (
                    evaluation_id,
                    str(version_id),
                    current["content_hash"],
                    _stable_json(result),
                    str(actor_id),
                    created_at,
                ),
            )
            connection.execute(
                "INSERT INTO studio_skill_events"
                "(version_id,event_type,actor_id,payload_json,created_at) VALUES(?,?,?,?,?)",
                (
                    str(version_id),
                    "candidate_evaluated",
                    str(actor_id),
                    _stable_json(event_payload),
                    created_at,
                ),
            )
        return self.get_version(version_id, tenant_id=tenant_id)  # type: ignore[return-value]

    def get_evaluation(
        self,
        evaluation_id: str,
        *,
        tenant_id: str = "local",
    ) -> dict[str, Any] | None:
        with self._conn() as connection:
            row = connection.execute(
                "SELECT e.id,e.version_id,e.content_hash,e.result_json,e.created_by,e.created_at "
                "FROM studio_skill_evaluations e "
                "JOIN studio_skill_versions v ON v.version_id=e.version_id "
                "WHERE e.id=? AND v.tenant_id=?",
                (str(evaluation_id), str(tenant_id)),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "version_id": str(row["version_id"]),
            "content_hash": str(row["content_hash"]),
            "result": json.loads(str(row["result_json"])),
            "created_by": str(row["created_by"]),
            "created_at": float(row["created_at"]),
            "authority": authority_contract(),
        }

    def release_candidate(
        self,
        version_id: str,
        *,
        tenant_id: str = "local",
    ) -> dict[str, Any]:
        current = self.get_version(version_id, tenant_id=tenant_id)
        if current is None:
            raise KeyError(version_id)
        if current["state"] != "evaluated_pass" or not isinstance(current.get("evaluation"), dict):
            raise ValueError("only evaluated_pass versions can be exported")
        evaluation = dict(current["evaluation"])
        candidate = {
            "domain": current["domain"],
            "name": current["name"],
            "purpose": current["purpose"],
            "guidance": current["guidance"],
            "preferred_tools": list(current["preferred_tools"]),
            "trigger_terms": list(current["trigger_terms"]),
            "input_contract": dict(current["input_contract"]),
            "output_contract": dict(current["output_contract"]),
            "safety_notes": current["safety_notes"],
            "source_skill_id": current["source_skill_id"],
        }
        payload = {
            "schema_version": 1,
            "source": {
                "kind": "skill_studio",
                "family_id": current["family_id"],
                "version_id": current["version_id"],
                "version": current["version"],
                "content_hash": current["content_hash"],
            },
            "candidate": candidate,
            "evaluation": {
                "evaluation_id": evaluation.get("evaluation_id"),
                "ok": bool(evaluation.get("ok")),
                "case_count": int(evaluation.get("case_count") or 0),
                "failed_case_count": int(evaluation.get("failed_case_count") or 0),
                "drift_case_count": int(evaluation.get("drift_case_count") or 0),
                "isolated_runtime": bool(evaluation.get("isolated_runtime")),
            },
            "authority": authority_contract(),
        }
        payload["candidate_id"] = f"studio-candidate-{_content_hash(payload)[:20]}"
        payload["export_hash"] = _content_hash(payload)
        return payload

    def archive(
        self,
        version_id: str,
        *,
        actor_id: str,
        note: str = "",
        tenant_id: str = "local",
    ) -> dict[str, Any]:
        current = self.get_version(version_id, tenant_id=tenant_id)
        if current is None:
            raise KeyError(version_id)
        if current["state"] == "archived":
            return current
        self._append_event(
            version_id,
            "archived",
            actor_id,
            {"note": str(note)[:2000]},
            tenant_id=tenant_id,
        )
        return self.get_version(version_id, tenant_id=tenant_id)  # type: ignore[return-value]

    def _append_event(
        self,
        version_id: str,
        event_type: str,
        actor_id: str,
        payload: dict[str, Any],
        *,
        tenant_id: str = "local",
    ) -> None:
        if event_type not in EVENTS:
            raise ValueError("invalid studio event")
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM studio_skill_versions WHERE version_id=? AND tenant_id=?",
                (str(version_id), str(tenant_id)),
            ).fetchone()
            if not exists:
                raise KeyError(version_id)
            connection.execute(
                "INSERT INTO studio_skill_events(version_id,event_type,actor_id,payload_json,created_at) "
                "VALUES(?,?,?,?,?)",
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
            with sqlite3.connect(path, timeout=5.0) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT domain,promotion_threshold,retirement_threshold,exploration,updates,updated_at "
                    "FROM evolution_policy ORDER BY domain"
                ).fetchall()
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

    def catalog(self, *, tenant_id: str = "local") -> dict[str, Any]:
        return {
            "scope": "mixed",
            "scopes": {
                "studio_families": "tenant",
                "runtime_skills": "deployment_read_only",
                "evolution_policies": "deployment_read_only",
                "registered_tools": "deployment_read_only",
            },
            "runtime_skills": self.runtime_catalog(200),
            "studio_families": self.latest_families(100, tenant_id=tenant_id),
            "evolution_policies": self.runtime_policies(),
            "registered_tools": sorted(self._tool_keys()),
            "authority": authority_contract(),
        }
