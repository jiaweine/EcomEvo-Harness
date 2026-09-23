from __future__ import annotations

from fastapi.testclient import TestClient

from ecomevo.api.app import app
from ecomevo.product.multi_node_certification import certification_contract
from ecomevo.product.multi_node_readiness import CERTIFICATION_GATES


def _identity(monkeypatch, *, role="admin", user="certification-admin"):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", "tenant-certification")
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", user)
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", role)
    monkeypatch.setenv("ECOMEVO_DEPLOYMENT_NODES", "1")


def test_contract_exactly_covers_migration_certification_gates():
    result = certification_contract()
    expected_ids = [str(row["id"]) for row in CERTIFICATION_GATES]

    assert result["contract_status"] == "complete_not_executed"
    assert result["gate_count"] == len(expected_ids) == 6
    assert [row["id"] for row in result["gates"]] == expected_ids
    assert result["contract_integrity"] == {
        "matches_migration_gate_ids": True,
        "missing_gate_contract_ids": [],
        "unexpected_gate_contract_ids": [],
    }


def test_every_gate_requires_real_distinct_node_evidence_and_is_not_passed():
    result = certification_contract()

    assert result["certification_ready"] is False
    assert result["certification_executed"] is False
    assert result["certification_passed"] is False
    for gate in result["gates"]:
        assert gate["status"] == "not_run_against_real_multi_node_backend"
        assert gate["passed"] is False
        assert gate["minimum_distinct_application_nodes"] == 2
        assert gate["requires_distinct_node_ids"] is True
        assert gate["requires_real_cross_node_execution"] is True
        assert gate["same_process_simulation_accepted"] is False
        assert gate["same_host_multi_process_accepted"] is False
        assert gate["client_supplied_pass_claim_accepted"] is False
        assert gate["static_configuration_only_accepted"] is False
        assert gate["required_observations"]


def test_gate_observations_preserve_failure_and_authority_boundaries():
    gates = {row["id"]: row for row in certification_contract()["gates"]}

    lease = gates["cross_node_job_lease_handoff"]["required_observations"]
    assert "stale_node_a_fenced_write_is_rejected" in lease
    assert "provider_or_tool_side_effect_is_not_duplicated" in lease

    action = gates["cross_node_business_action_cas"]["required_observations"]
    assert "exactly_one_authoritative_terminal_transition_is_committed" in action
    assert "stale_or_duplicate_transition_is_rejected" in action

    reconnect = gates["cross_node_event_reconnect"]["required_observations"]
    assert "after_id_reconnect_from_another_node_returns_the_complete_ordered_suffix" in reconnect
    assert "reconnect_contains_no_gap_or_duplicate" in reconnect

    assets = gates["cross_node_asset_snapshot_integrity"]["required_observations"]
    assert "node_b_verifies_the_expected_sha256_before_use" in assets
    assert "missing_or_mismatched_bytes_fail_closed" in assets

    authority = gates["cross_node_authority_consistency"]["required_observations"]
    assert "both_nodes_converge_without_split_brain_or_stale_authority_serving" in authority
    assert "process_local_plugin_identity_is_accounted_for_separately" in authority

    recovery = gates["cross_node_failure_recovery"]["required_observations"]
    assert "successor_node_preserves_uncertain_side_effect_state_when_outcome_is_ambiguous" in recovery
    assert "successor_does_not_blindly_replay_the_side_effect" in recovery


def test_contract_methodology_and_authority_never_grant_release():
    result = certification_contract()

    assert result["methodology"] == {
        "all_gates_must_pass": True,
        "minimum_distinct_application_nodes": 2,
        "actual_replica_discovery_required_before_execution": True,
        "shared_backend_capabilities_required_before_execution": True,
        "same_process_simulation_sufficient": False,
        "same_host_multi_process_sufficient": False,
        "self_attested_backend_capabilities_accepted": False,
        "client_supplied_evidence_accepted": False,
        "static_configuration_only_sufficient": False,
        "historical_single_node_ci_sufficient": False,
        "release_authority_granted": False,
    }
    assert result["authority"] == {
        "read_only": True,
        "runs_certification_tests": False,
        "accepts_client_evidence": False,
        "changes_runtime_topology": False,
        "changes_storage_backend": False,
        "changes_policy": False,
        "changes_routing": False,
        "changes_runtime_skills": False,
        "approves_business_actions": False,
        "executes_tools": False,
        "grants_release_authority": False,
        "merges_or_deploys_code": False,
    }


def test_certification_contract_route_is_admin_only(monkeypatch):
    path = "/api/runtime/readiness/multi-node/certification-contract"
    with TestClient(app) as client:
        _identity(monkeypatch)
        response = client.get(path)
        assert response.status_code == 200
        body = response.json()
        assert body["contract_status"] == "complete_not_executed"
        assert body["gate_count"] == 6
        assert body["certification_passed"] is False
        assert body["authority"]["read_only"] is True
        assert body["authority"]["runs_certification_tests"] is False
        assert body["authority"]["accepts_client_evidence"] is False

        _identity(monkeypatch, role="operator", user="certification-operator")
        assert client.get(path).status_code == 403

        _identity(monkeypatch, role="viewer", user="certification-viewer")
        assert client.get(path).status_code == 403
