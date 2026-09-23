from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


_DATABASES: tuple[dict[str, str], ...] = (
    {
        "id": "evaluation",
        "filename": "evaluation.db",
        "marker_table": "evaluation_runs",
    },
    {
        "id": "knowledge",
        "filename": "knowledge.db",
        "marker_table": "knowledge_sources",
    },
    {
        "id": "decision_exports",
        "filename": "decision_exports.db",
        "marker_table": "decision_exports",
    },
    {
        "id": "release_readiness",
        "filename": "release_readiness.db",
        "marker_table": "release_readiness_snapshots",
    },
    {
        "id": "skill_studio",
        "filename": "skill_studio.db",
        "marker_table": "studio_skill_versions",
    },
    {
        "id": "connection_governance",
        "filename": "connection_governance.db",
        "marker_table": "connection_probe_history",
    },
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash_dump(connection: sqlite3.Connection) -> tuple[str, int]:
    digest = hashlib.sha256()
    statements = 0
    for statement in connection.iterdump():
        encoded = statement.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        statements += 1
    return digest.hexdigest(), statements


class AdminControlStateManifest:
    """Read-only fingerprints for the node-local durable admin control databases.

    Each database is observed in its own SQLite read transaction. The resulting manifest
    is migration evidence only: the six reads are not one atomic cross-database snapshot,
    and matching manifests cannot prove a shared cross-node transaction domain.
    """

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    @staticmethod
    def authority() -> dict[str, bool]:
        return {
            "read_only": True,
            "changes_admin_state": False,
            "changes_release_evidence": False,
            "changes_knowledge": False,
            "changes_skill_studio": False,
            "changes_decision_exports": False,
            "changes_connection_governance": False,
            "changes_storage_backend": False,
            "changes_runtime_topology": False,
            "grants_release_authority": False,
            "executes_tools": False,
            "merges_or_deploys_code": False,
        }

    @staticmethod
    def methodology() -> dict[str, Any]:
        return {
            "per_database_read_transaction": True,
            "cross_database_atomic_snapshot": False,
            "logical_dump_hashed": True,
            "raw_rows_exposed": False,
            "raw_errors_exposed": False,
            "shared_across_application_nodes": False,
            "cross_node_supported": False,
            "fingerprint_equality_proves_shared_admin_transaction_domain": False,
            "self_attested_backend_capabilities_accepted": False,
            "multi_node_requirement_id": "shared_admin_control_state",
            "requirement_current_satisfied": False,
            "release_authority_granted": False,
        }

    @staticmethod
    def _unavailable_database(spec: dict[str, str], status: str) -> dict[str, Any]:
        return {
            "id": spec["id"],
            "filename": spec["filename"],
            "status": status,
            "sha256": None,
            "table_count": None,
            "statement_count": None,
        }

    def _database_snapshot(self, spec: dict[str, str]) -> dict[str, Any]:
        path = self.data_dir / spec["filename"]
        if not path.is_file():
            return self._unavailable_database(spec, "missing")

        try:
            uri = f"{path.resolve().as_uri()}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=10.0) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA busy_timeout=10000")
                connection.execute("PRAGMA query_only=ON")
                connection.execute("BEGIN")

                quick_check = connection.execute("PRAGMA quick_check").fetchone()
                if quick_check is None or str(quick_check[0]) != "ok":
                    return self._unavailable_database(spec, "invalid")

                tables = {
                    str(row["name"])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                }
                if spec["marker_table"] not in tables:
                    return self._unavailable_database(spec, "schema_marker_missing")

                digest, statement_count = _hash_dump(connection)
                return {
                    "id": spec["id"],
                    "filename": spec["filename"],
                    "status": "available",
                    "sha256": digest,
                    "table_count": len(tables),
                    "statement_count": statement_count,
                }
        except (OSError, sqlite3.Error, UnicodeError, ValueError):
            return self._unavailable_database(spec, "read_error")

    def snapshot(self) -> dict[str, Any]:
        databases = [self._database_snapshot(spec) for spec in _DATABASES]
        unavailable = [
            str(row["id"])
            for row in databases
            if row.get("status") != "available" or not row.get("sha256")
        ]

        manifest_sha256: str | None = None
        if not unavailable:
            manifest_payload = {
                "schema_version": 1,
                "databases": [
                    {
                        "id": row["id"],
                        "filename": row["filename"],
                        "sha256": row["sha256"],
                        "table_count": row["table_count"],
                        "statement_count": row["statement_count"],
                    }
                    for row in databases
                ],
            }
            manifest_sha256 = hashlib.sha256(
                _canonical(manifest_payload).encode("utf-8")
            ).hexdigest()

        return {
            "schema_version": 1,
            "scope": "deployment",
            "manifest_status": "available_local_only" if manifest_sha256 else "unavailable",
            "manifest_sha256": manifest_sha256,
            "backend": "multiple_node_local_sqlite_databases",
            "database_count": len(databases),
            "databases": databases,
            "unavailable_database_ids": unavailable,
            "multi_node_ready": False,
            "methodology": self.methodology(),
            "authority": self.authority(),
        }
