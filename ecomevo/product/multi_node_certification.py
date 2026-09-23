from __future__ import annotations

from typing import Any

from ecomevo.product.multi_node_readiness import CERTIFICATION_GATES


_GATE_OBSERVATIONS: dict[str, tuple[str, ...]] = {
    "cross_node_job_lease_handoff": (
        "node_a_claims_and_renews_one_durable_job",
        "node_b_cannot_claim_while_node_a_lease_is_authoritatively_valid",
        "node_b_reclaims_after_node_a_loses_ownership",
        "stale_node_a_fenced_write_is_rejected",
        "provider_or_tool_side_effect_is_not_duplicated",
    ),
    "cross_node_business_action_cas": (
        "two_distinct_nodes_attempt_the_same_business_action_transition",
        "exactly_one_authoritative_terminal_transition_is_committed",
        "stale_or_duplicate_transition_is_rejected",
        "durable_audit_order_is_consistent_across_nodes",
    ),
    "cross_node_event_reconnect": (
        "different_nodes_append_and_serve_events_for_one_conversation",
        "event_cursors_are_unique_and_monotonic_in_the_shared_domain",
        "after_id_reconnect_from_another_node_returns_the_complete_ordered_suffix",
        "reconnect_contains_no_gap_or_duplicate",
    ),
    "cross_node_asset_snapshot_integrity": (
        "node_a_accepts_an_immutable_asset_snapshot",
        "node_b_reads_the_exact_bytes_by_shared_identity_without_node_local_path_dependency",
        "node_b_verifies_the_expected_sha256_before_use",
        "missing_or_mismatched_bytes_fail_closed",
    ),
    "cross_node_authority_consistency": (
        "two_distinct_serving_nodes_resolve_the_same_durable_authority_state",
        "policy_skill_routing_and_evolution_changes_have_one_authoritative_order",
        "both_nodes_converge_without_split_brain_or_stale_authority_serving",
        "process_local_plugin_identity_is_accounted_for_separately",
    ),
    "cross_node_failure_recovery": (
        "one_node_is_lost_during_or_after_a_governed_side_effect_dispatch",
        "successor_node_preserves_uncertain_side_effect_state_when_outcome_is_ambiguous",
        "successor_does_not_blindly_replay_the_side_effect",
        "business_state_reconciliation_or_idempotent_fencing_prevents_duplicate_effect",
    ),
}


def authority_contract() -> dict[str, bool]:
    return {
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


def certification_contract() -> dict[str, Any]:
    """Return the fail-closed evidence contract for future real multi-node certification.

    This function deliberately does not execute tests and cannot turn declarations,
    simulations, or client-supplied evidence into a passing certification result.
    """

    gate_ids = [str(row["id"]) for row in CERTIFICATION_GATES]
    missing_contracts = [gate_id for gate_id in gate_ids if gate_id not in _GATE_OBSERVATIONS]
    extra_contracts = sorted(set(_GATE_OBSERVATIONS) - set(gate_ids))
    contract_complete = not missing_contracts and not extra_contracts

    gates: list[dict[str, Any]] = []
    for source in CERTIFICATION_GATES:
        gate_id = str(source["id"])
        gates.append(
            {
                "id": gate_id,
                "status": "not_run_against_real_multi_node_backend",
                "required_evidence": str(source["required_evidence"]),
                "required_observations": list(_GATE_OBSERVATIONS.get(gate_id, ())),
                "minimum_distinct_application_nodes": 2,
                "requires_distinct_node_ids": True,
                "requires_real_cross_node_execution": True,
                "same_process_simulation_accepted": False,
                "same_host_multi_process_accepted": False,
                "client_supplied_pass_claim_accepted": False,
                "static_configuration_only_accepted": False,
                "passed": False,
            }
        )

    return {
        "schema_version": 1,
        "scope": "deployment",
        "contract_status": "complete_not_executed" if contract_complete else "invalid_contract",
        "certification_ready": False,
        "certification_executed": False,
        "certification_passed": False,
        "gate_count": len(gates),
        "gates": gates,
        "contract_integrity": {
            "matches_migration_gate_ids": contract_complete,
            "missing_gate_contract_ids": missing_contracts,
            "unexpected_gate_contract_ids": extra_contracts,
        },
        "methodology": {
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
        },
        "authority": authority_contract(),
    }
