from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable


_SURFACE_NAMES = (
    "policy_versions",
    "runtime_skills",
    "evolution_policy",
    "routing_policy",
    "routing_tool_stats",
    "harness_components",
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decode_json(value: Any, expected_type: type) -> Any:
    parsed = json.loads(str(value or ""))
    if not isinstance(parsed, expected_type):
        raise ValueError("runtime authority JSON has unexpected type")
    return parsed


def _digest_rows(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical(rows).encode("utf-8")).hexdigest()


class RuntimeAuthoritySnapshot:
    """Read-only fingerprint of durable runtime authority state in one SQLite snapshot.

    The fingerprint is useful migration evidence only. Equality across two processes or
    exported databases does not prove that those processes share one transaction domain.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def authority() -> dict[str, bool]:
        return {
            "read_only": True,
            "changes_policy": False,
            "changes_routing": False,
            "promotes_runtime_skills": False,
            "changes_harness": False,
            "approves_business_actions": False,
            "executes_tools": False,
            "changes_storage_backend": False,
            "changes_runtime_topology": False,
        }

    @staticmethod
    def methodology() -> dict[str, Any]:
        return {
            "same_database_transaction_snapshot": True,
            "history_tables_included": False,
            "process_plugin_lifecycle_covered": False,
            "fingerprint_equality_proves_shared_transaction_domain": False,
            "self_attested_backend_capabilities_accepted": False,
            "cross_node_shared": False,
            "cross_node_supported": False,
            "multi_node_certification": False,
        }

    def _unavailable(
        self,
        status: str,
        *,
        missing_tables: list[str] | None = None,
        invalid_surface: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "scope": "deployment",
            "snapshot_status": status,
            "snapshot_sha256": None,
            "backend": "node_local_sqlite_wal",
            "transaction_mode": "sqlite_deferred_read",
            "surfaces": {},
            "missing_tables": list(missing_tables or []),
            "invalid_surface": invalid_surface,
            "methodology": self.methodology(),
            "authority": self.authority(),
        }

    @staticmethod
    def _policy_versions(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT policy_id,version,domain,status,rules_json,controls_json,scope_json,
                   authority,priority,effective_from,effective_to,owner,approver,source,
                   source_hash,created_at
            FROM policy_versions
            ORDER BY policy_id,version
            """
        ).fetchall()
        return [
            {
                "policy_id": str(row["policy_id"]),
                "version": int(row["version"]),
                "domain": str(row["domain"]),
                "status": str(row["status"]),
                "rules": _decode_json(row["rules_json"], list),
                "controls": _decode_json(row["controls_json"], dict),
                "scope": _decode_json(row["scope_json"], dict),
                "authority": int(row["authority"]),
                "priority": int(row["priority"]),
                "effective_from": str(row["effective_from"]),
                "effective_to": str(row["effective_to"]) if row["effective_to"] else None,
                "owner": str(row["owner"] or ""),
                "approver": str(row["approver"] or ""),
                "source": str(row["source"] or ""),
                "source_hash": str(row["source_hash"] or ""),
                "created_at": str(row["created_at"] or ""),
            }
            for row in rows
        ]

    @staticmethod
    def _runtime_skills(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT skill_id,domain,niche,name,guidance,preferred_tools_json,
                   trigger_terms_json,status,shadow_score,alpha,beta,uses,wins,losses,
                   created_at,updated_at,source_patch_id
            FROM runtime_skills
            ORDER BY skill_id
            """
        ).fetchall()
        return [
            {
                "skill_id": str(row["skill_id"]),
                "domain": str(row["domain"]),
                "niche": str(row["niche"]),
                "name": str(row["name"]),
                "guidance": str(row["guidance"]),
                "preferred_tools": _decode_json(row["preferred_tools_json"], list),
                "trigger_terms": _decode_json(row["trigger_terms_json"], list),
                "status": str(row["status"]),
                "shadow_score": float(row["shadow_score"]),
                "alpha": float(row["alpha"]),
                "beta": float(row["beta"]),
                "uses": int(row["uses"]),
                "wins": int(row["wins"]),
                "losses": int(row["losses"]),
                "created_at": float(row["created_at"]),
                "updated_at": float(row["updated_at"]),
                "source_patch_id": str(row["source_patch_id"]) if row["source_patch_id"] else None,
            }
            for row in rows
        ]

    @staticmethod
    def _evolution_policy(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT domain,promotion_threshold,retirement_threshold,exploration,updates,updated_at
            FROM evolution_policy
            ORDER BY domain
            """
        ).fetchall()
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

    @staticmethod
    def _routing_policy(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT policy_key,domain,scope,a_json,b_json,samples,reward_ewma,residual_ewma,updated_at
            FROM routing_policy
            ORDER BY policy_key
            """
        ).fetchall()
        return [
            {
                "policy_key": str(row["policy_key"]),
                "domain": str(row["domain"]),
                "scope": str(row["scope"]),
                "a": _decode_json(row["a_json"], list),
                "b": _decode_json(row["b_json"], list),
                "samples": int(row["samples"]),
                "reward_ewma": float(row["reward_ewma"]),
                "residual_ewma": float(row["residual_ewma"]),
                "updated_at": float(row["updated_at"]),
            }
            for row in rows
        ]

    @staticmethod
    def _routing_tool_stats(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT domain,tool,alpha,beta,uses,reward_ewma,updated_at
            FROM routing_tool_stats
            ORDER BY domain,tool
            """
        ).fetchall()
        return [
            {
                "domain": str(row["domain"]),
                "tool": str(row["tool"]),
                "alpha": float(row["alpha"]),
                "beta": float(row["beta"]),
                "uses": int(row["uses"]),
                "reward_ewma": float(row["reward_ewma"]),
                "updated_at": float(row["updated_at"]),
            }
            for row in rows
        ]

    @staticmethod
    def _harness_components(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT component_id,domain,kind,status,parent_id,content_json,hypothesis,
                   alpha,beta,uses,generation,created_at,updated_at
            FROM harness_components
            ORDER BY component_id
            """
        ).fetchall()
        return [
            {
                "component_id": str(row["component_id"]),
                "domain": str(row["domain"]),
                "kind": str(row["kind"]),
                "status": str(row["status"]),
                "parent_id": str(row["parent_id"]) if row["parent_id"] else None,
                "content": _decode_json(row["content_json"], dict),
                "hypothesis": str(row["hypothesis"] or ""),
                "alpha": float(row["alpha"]),
                "beta": float(row["beta"]),
                "uses": int(row["uses"]),
                "generation": int(row["generation"]),
                "created_at": float(row["created_at"]),
                "updated_at": float(row["updated_at"]),
            }
            for row in rows
        ]

    def snapshot(self) -> dict[str, Any]:
        readers: dict[str, Callable[[sqlite3.Connection], list[dict[str, Any]]]] = {
            "policy_versions": self._policy_versions,
            "runtime_skills": self._runtime_skills,
            "evolution_policy": self._evolution_policy,
            "routing_policy": self._routing_policy,
            "routing_tool_stats": self._routing_tool_stats,
            "harness_components": self._harness_components,
        }

        try:
            with self._connect() as connection:
                connection.execute("BEGIN")
                existing = {
                    str(row["name"])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                missing = [name for name in _SURFACE_NAMES if name not in existing]
                if missing:
                    return self._unavailable(
                        "unavailable_missing_tables",
                        missing_tables=missing,
                    )

                surfaces: dict[str, dict[str, Any]] = {}
                for name in _SURFACE_NAMES:
                    try:
                        rows = readers[name](connection)
                        surface_digest = _digest_rows(rows)
                    except (TypeError, ValueError, OverflowError, json.JSONDecodeError):
                        return self._unavailable(
                            "unavailable_invalid_state",
                            invalid_surface=name,
                        )
                    surfaces[name] = {
                        "row_count": len(rows),
                        "sha256": surface_digest,
                    }
        except sqlite3.Error:
            return self._unavailable("unavailable_read_error")

        digest_payload = {
            "schema_version": 1,
            "surfaces": surfaces,
        }
        try:
            snapshot_digest = hashlib.sha256(
                _canonical(digest_payload).encode("utf-8")
            ).hexdigest()
        except (TypeError, ValueError, OverflowError):
            return self._unavailable("unavailable_invalid_state")

        return {
            "schema_version": 1,
            "scope": "deployment",
            "snapshot_status": "available",
            "snapshot_sha256": snapshot_digest,
            "backend": "node_local_sqlite_wal",
            "transaction_mode": "sqlite_deferred_read",
            "surfaces": surfaces,
            "missing_tables": [],
            "invalid_surface": None,
            "methodology": self.methodology(),
            "authority": self.authority(),
        }
