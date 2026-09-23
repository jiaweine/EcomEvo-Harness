from __future__ import annotations

import sqlite3

from ecomevo.product.runtime_authority_snapshot import RuntimeAuthoritySnapshot
from ecomevo.runtime import EcomEvoEngine


EXPECTED_SURFACES = {
    "policy_versions",
    "runtime_skills",
    "evolution_policy",
    "routing_policy",
    "routing_tool_stats",
    "harness_components",
}


def _engine(tmp_path):
    return EcomEvoEngine(tmp_path / "runtime.db")


def test_runtime_authority_snapshot_is_deterministic_and_redacted(tmp_path):
    engine = _engine(tmp_path)
    snapshotter = RuntimeAuthoritySnapshot(engine.policies.db_path)

    first = snapshotter.snapshot()
    second = snapshotter.snapshot()

    assert first == second
    assert first["snapshot_status"] == "available"
    assert len(first["snapshot_sha256"]) == 64
    assert set(first["surfaces"]) == EXPECTED_SURFACES
    assert all(len(row["sha256"]) == 64 for row in first["surfaces"].values())
    assert all(isinstance(row["row_count"], int) for row in first["surfaces"].values())
    assert "rules" not in first["surfaces"]["policy_versions"]
    assert "guidance" not in first["surfaces"]["runtime_skills"]


def test_authority_bearing_policy_change_changes_snapshot(tmp_path):
    engine = _engine(tmp_path)
    snapshotter = RuntimeAuthoritySnapshot(engine.policies.db_path)
    before = snapshotter.snapshot()

    engine.policies.create_version(
        policy_id="test.runtime-authority",
        domain="risk_review",
        rules=["snapshot-sensitive rule"],
        controls={"snapshot.control": "required"},
        scope={},
        authority=80,
        priority=1,
        status="active",
        effective_from="1970-01-01T00:00:00Z",
        owner="test",
        approver="test",
        source="test:runtime-authority",
    )

    after = snapshotter.snapshot()
    assert after["snapshot_sha256"] != before["snapshot_sha256"]
    assert (
        after["surfaces"]["policy_versions"]["sha256"]
        != before["surfaces"]["policy_versions"]["sha256"]
    )
    assert "snapshot-sensitive rule" not in str(after)
    assert "snapshot.control" not in str(after)


def test_history_only_growth_does_not_change_authority_snapshot(tmp_path):
    engine = _engine(tmp_path)
    snapshotter = RuntimeAuthoritySnapshot(engine.policies.db_path)
    before = snapshotter.snapshot()

    with sqlite3.connect(engine.policies.db_path) as db:
        db.execute(
            """
            INSERT INTO routing_outcomes(domain,phase,tool,reward,feature_json,meta_json,created_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            ("risk_review", "test", "catalog.lookup", 0.5, "[]", "{}", 123.0),
        )

    after = snapshotter.snapshot()
    assert after == before


def test_missing_authority_table_fails_closed(tmp_path):
    db_path = tmp_path / "partial.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE policy_versions(policy_id TEXT)")

    result = RuntimeAuthoritySnapshot(db_path).snapshot()

    assert result["snapshot_status"] == "unavailable_missing_tables"
    assert result["snapshot_sha256"] is None
    assert "runtime_skills" in result["missing_tables"]
    assert result["surfaces"] == {}
    assert result["methodology"]["cross_node_shared"] is False
    assert result["methodology"]["cross_node_supported"] is False


def test_corrupt_authority_json_fails_closed_without_fingerprint(tmp_path):
    engine = _engine(tmp_path)
    snapshotter = RuntimeAuthoritySnapshot(engine.policies.db_path)
    assert snapshotter.snapshot()["snapshot_status"] == "available"

    with sqlite3.connect(engine.policies.db_path) as db:
        db.execute(
            "UPDATE policy_versions SET controls_json=? WHERE policy_id=? AND version=?",
            ("{not-json", "builtin.risk-review", 1),
        )

    result = snapshotter.snapshot()
    assert result["snapshot_status"] == "unavailable_invalid_state"
    assert result["snapshot_sha256"] is None
    assert result["invalid_surface"] == "policy_versions"
    assert result["surfaces"] == {}
    assert "not-json" not in str(result)


def test_snapshot_authority_and_methodology_do_not_claim_multi_node_support(tmp_path):
    engine = _engine(tmp_path)
    result = RuntimeAuthoritySnapshot(engine.policies.db_path).snapshot()

    assert result["backend"] == "node_local_sqlite_wal"
    assert result["transaction_mode"] == "sqlite_deferred_read"
    assert result["methodology"] == {
        "same_database_transaction_snapshot": True,
        "history_tables_included": False,
        "process_plugin_lifecycle_covered": False,
        "fingerprint_equality_proves_shared_transaction_domain": False,
        "self_attested_backend_capabilities_accepted": False,
        "cross_node_shared": False,
        "cross_node_supported": False,
        "multi_node_certification": False,
    }
    assert result["authority"] == {
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
