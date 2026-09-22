from __future__ import annotations

from ecomevo.product.multi_node_readiness import (
    CERTIFICATION_GATES,
    MULTI_NODE_REQUIREMENTS,
    multi_node_migration_readiness,
)


EXPECTED_BLOCKERS = {
    "shared_product_transaction_domain",
    "cross_node_lease_fencing_clock",
    "shared_immutable_asset_storage",
    "shared_runtime_authority_state",
    "shared_admin_control_state",
}


def test_single_node_does_not_claim_multi_node_readiness():
    result = multi_node_migration_readiness(
        declared_nodes=1,
        declaration_valid=True,
    )

    assert result["scope"] == "deployment"
    assert result["declared_nodes"] == 1
    assert result["multi_node_requested"] is False
    assert result["ready"] is False
    assert result["status"] == "not_ready_not_requested"
    assert result["blocker_count"] == len(EXPECTED_BLOCKERS)
    assert set(result["blocker_ids"]) == EXPECTED_BLOCKERS
    assert all(row["current_satisfied"] is False for row in result["requirements"])
    assert all(
        row["status"] == "not_run_against_multi_node_backend"
        for row in result["certification_gates"]
    )


def test_multi_node_intent_remains_blocked_by_real_architecture_gaps():
    result = multi_node_migration_readiness(
        declared_nodes=3,
        declaration_valid=True,
    )

    assert result["multi_node_requested"] is True
    assert result["status"] == "blocked"
    assert result["ready"] is False
    assert result["current_architecture"] == {
        "product_state": "sqlite_wal_local_file",
        "runtime_authority": "sqlite_wal_local_runtime_db",
        "asset_storage": "node_local_filesystem_paths",
        "lease_clock": "application_wall_clock",
        "admin_control_state": "multiple_node_local_sqlite_databases",
    }
    assert set(result["blocker_ids"]) == EXPECTED_BLOCKERS


def test_contract_rejects_self_attestation_as_evidence():
    result = multi_node_migration_readiness(
        declared_nodes=2,
        declaration_valid=True,
    )
    methodology = result["methodology"]

    assert methodology["actual_backend_capability_detection"] is False
    assert methodology["replica_discovery"] is False
    assert methodology["self_attested_backend_capabilities_accepted"] is False
    assert methodology["database_url_swap_is_sufficient"] is False
    assert methodology["all_requirements_must_be_verified"] is True
    assert methodology["all_certification_gates_must_pass"] is True
    assert methodology["release_authority_granted"] is False


def test_contract_covers_fencing_assets_authority_and_failure_recovery():
    requirement_ids = {str(row["id"]) for row in MULTI_NODE_REQUIREMENTS}
    certification_ids = {str(row["id"]) for row in CERTIFICATION_GATES}

    assert "cross_node_lease_fencing_clock" in requirement_ids
    assert "shared_immutable_asset_storage" in requirement_ids
    assert "shared_runtime_authority_state" in requirement_ids
    assert "shared_admin_control_state" in requirement_ids
    assert "cross_node_job_lease_handoff" in certification_ids
    assert "cross_node_business_action_cas" in certification_ids
    assert "cross_node_asset_snapshot_integrity" in certification_ids
    assert "cross_node_failure_recovery" in certification_ids


def test_multi_node_readiness_is_observational_only():
    result = multi_node_migration_readiness(
        declared_nodes=2,
        declaration_valid=True,
    )

    assert result["authority"] == {
        "read_only": True,
        "changes_runtime_topology": False,
        "changes_storage_backend": False,
        "changes_routing": False,
        "changes_policy": False,
        "changes_runtime_skills": False,
        "approves_business_actions": False,
        "executes_tools": False,
        "deploys_code": False,
    }
