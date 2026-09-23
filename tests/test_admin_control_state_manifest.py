from __future__ import annotations

import sqlite3

from ecomevo.product.admin_control_state_manifest import AdminControlStateManifest


DATABASES = (
    ("evaluation.db", "evaluation_runs"),
    ("knowledge.db", "knowledge_sources"),
    ("decision_exports.db", "decision_exports"),
    ("release_readiness.db", "release_readiness_snapshots"),
    ("skill_studio.db", "studio_skill_versions"),
    ("connection_governance.db", "connection_probe_history"),
)


def _build_control_plane(tmp_path, *, secret="sensitive-admin-state"):
    for index, (filename, marker) in enumerate(DATABASES):
        with sqlite3.connect(tmp_path / filename) as db:
            db.execute(
                f"CREATE TABLE {marker}(id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
            )
            db.execute(
                f"INSERT INTO {marker}(payload) VALUES(?)",
                (f"{secret}-{index}",),
            )


def test_manifest_is_deterministic_and_does_not_expose_rows(tmp_path):
    _build_control_plane(tmp_path)
    manifest = AdminControlStateManifest(tmp_path)

    first = manifest.snapshot()
    second = manifest.snapshot()

    assert first == second
    assert first["manifest_status"] == "available_local_only"
    assert len(first["manifest_sha256"]) == 64
    assert first["database_count"] == 6
    assert first["unavailable_database_ids"] == []
    assert {row["id"] for row in first["databases"]} == {
        "evaluation",
        "knowledge",
        "decision_exports",
        "release_readiness",
        "skill_studio",
        "connection_governance",
    }
    assert all(row["status"] == "available" for row in first["databases"])
    assert all(len(row["sha256"]) == 64 for row in first["databases"])
    assert "sensitive-admin-state" not in str(first)


def test_mutating_one_admin_database_changes_only_its_digest_and_manifest(tmp_path):
    _build_control_plane(tmp_path)
    manifest = AdminControlStateManifest(tmp_path)
    before = manifest.snapshot()
    before_by_id = {row["id"]: row["sha256"] for row in before["databases"]}

    with sqlite3.connect(tmp_path / "knowledge.db") as db:
        db.execute(
            "INSERT INTO knowledge_sources(payload) VALUES(?)",
            ("new knowledge governance state",),
        )

    after = manifest.snapshot()
    after_by_id = {row["id"]: row["sha256"] for row in after["databases"]}

    assert after["manifest_sha256"] != before["manifest_sha256"]
    assert after_by_id["knowledge"] != before_by_id["knowledge"]
    assert all(
        after_by_id[key] == value
        for key, value in before_by_id.items()
        if key != "knowledge"
    )


def test_missing_database_fails_closed_without_top_level_manifest(tmp_path):
    _build_control_plane(tmp_path)
    (tmp_path / "skill_studio.db").unlink()

    result = AdminControlStateManifest(tmp_path).snapshot()

    assert result["manifest_status"] == "unavailable"
    assert result["manifest_sha256"] is None
    assert result["unavailable_database_ids"] == ["skill_studio"]
    row = next(item for item in result["databases"] if item["id"] == "skill_studio")
    assert row["status"] == "missing"
    assert row["sha256"] is None


def test_schema_marker_missing_fails_closed(tmp_path):
    _build_control_plane(tmp_path)
    path = tmp_path / "evaluation.db"
    path.unlink()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated(id INTEGER PRIMARY KEY)")

    result = AdminControlStateManifest(tmp_path).snapshot()

    assert result["manifest_sha256"] is None
    assert result["unavailable_database_ids"] == ["evaluation"]
    row = next(item for item in result["databases"] if item["id"] == "evaluation")
    assert row["status"] == "schema_marker_missing"


def test_corrupt_database_fails_closed_without_raw_error(tmp_path):
    _build_control_plane(tmp_path)
    (tmp_path / "decision_exports.db").write_bytes(b"not a sqlite database")

    result = AdminControlStateManifest(tmp_path).snapshot()

    assert result["manifest_sha256"] is None
    assert "decision_exports" in result["unavailable_database_ids"]
    row = next(item for item in result["databases"] if item["id"] == "decision_exports")
    assert row["status"] in {"invalid", "read_error"}
    assert "not a sqlite database" not in str(result)


def test_manifest_explicitly_refuses_multi_node_authority(tmp_path):
    _build_control_plane(tmp_path)
    result = AdminControlStateManifest(tmp_path).snapshot()

    assert result["backend"] == "multiple_node_local_sqlite_databases"
    assert result["multi_node_ready"] is False
    assert result["methodology"] == {
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
    assert result["authority"] == {
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
