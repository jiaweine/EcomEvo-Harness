from __future__ import annotations

from typing import Any


MULTI_NODE_REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {
        "id": "shared_product_transaction_domain",
        "surface": "product_state",
        "current_backend": "sqlite_wal_local_file",
        "required_capability": "shared_transactional_store_with_atomic_cas_and_monotonic_event_order",
        "current_satisfied": False,
        "reason": (
            "messages, task events, BusinessAction transitions, turn leases, and durable jobs "
            "currently coordinate through one local product.db transaction domain"
        ),
    },
    {
        "id": "cross_node_lease_fencing_clock",
        "surface": "lease_coordination",
        "current_backend": "sqlite_transaction_clock_with_monotonic_fencing",
        "required_capability": "shared_authoritative_lease_clock_and_fencing_tokens",
        "current_satisfied": False,
        "reason": (
            "turn/job lease expiry now uses the SQLite transaction-domain clock and monotonic "
            "fencing generations, removing application wall-clock ownership decisions; the "
            "transaction domain is still node-local and therefore not cross-node authoritative"
        ),
    },
    {
        "id": "shared_immutable_asset_storage",
        "surface": "asset_storage",
        "current_backend": "node_local_filesystem_paths",
        "required_capability": "shared_content_addressed_or_object_storage_with_hash_verification",
        "current_satisfied": False,
        "reason": (
            "durable workers reopen asset snapshots from server-local filesystem paths; another "
            "application node is not guaranteed to see identical bytes"
        ),
    },
    {
        "id": "shared_runtime_authority_state",
        "surface": "runtime_authority",
        "current_backend": "sqlite_wal_local_runtime_db",
        "required_capability": "shared_transactional_runtime_policy_skill_and_evolution_state",
        "current_satisfied": False,
        "reason": (
            "runtime policy, skill, and evolution state must resolve from one authoritative "
            "cross-node transaction domain before multiple nodes may serve decisions"
        ),
    },
    {
        "id": "shared_admin_control_state",
        "surface": "admin_control_plane",
        "current_backend": "multiple_node_local_sqlite_databases",
        "required_capability": "shared_durable_admin_and_release_evidence_state",
        "current_satisfied": False,
        "reason": (
            "evaluation, knowledge, decision-export, release-readiness, and Skill Studio state "
            "are persisted in deployment-local SQLite databases"
        ),
    },
)

CERTIFICATION_GATES: tuple[dict[str, str], ...] = (
    {
        "id": "cross_node_job_lease_handoff",
        "required_evidence": "two real application nodes contend, renew, lose, and reclaim one durable job without duplicate provider/tool work",
    },
    {
        "id": "cross_node_business_action_cas",
        "required_evidence": "concurrent approval/execution transitions across nodes preserve one authoritative BusinessAction outcome",
    },
    {
        "id": "cross_node_event_reconnect",
        "required_evidence": "event ordering and after_id reconnect remain complete while different nodes accept, execute, and serve websocket clients",
    },
    {
        "id": "cross_node_asset_snapshot_integrity",
        "required_evidence": "a job accepted on one node can verify and consume the exact immutable asset bytes on another node",
    },
    {
        "id": "cross_node_authority_consistency",
        "required_evidence": "Policy, Runtime Skill, approval, and routing authority resolve identically on every serving node",
    },
    {
        "id": "cross_node_failure_recovery",
        "required_evidence": "node loss during provider/tool execution preserves uncertain-side-effect semantics and prevents blind replay",
    },
)


def authority_contract() -> dict[str, bool]:
    return {
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


def multi_node_migration_readiness(
    *,
    declared_nodes: int | None,
    declaration_valid: bool,
) -> dict[str, Any]:
    """Describe the currently unmet prerequisites for a safe cross-node control plane.

    This is an architecture contract, not backend auto-detection and not a feature flag.
    Current EcomEvo production state is intentionally reported as not multi-node ready.
    Future implementations must replace the hard-coded unsatisfied evidence with verified
    backend capabilities and rerun the certification gates; changing deployment intent
    alone can never make this report ready.
    """

    nodes = declared_nodes if declaration_valid and isinstance(declared_nodes, int) else None
    requested = bool(nodes is not None and nodes > 1)
    requirements = [dict(row) for row in MULTI_NODE_REQUIREMENTS]
    blockers = [row for row in requirements if row["current_satisfied"] is not True]

    return {
        "schema_version": 1,
        "scope": "deployment",
        "declared_nodes": nodes,
        "multi_node_requested": requested,
        "ready": False,
        "status": "blocked" if requested else "not_ready_not_requested",
        "current_architecture": {
            "product_state": "sqlite_wal_local_file",
            "runtime_authority": "sqlite_wal_local_runtime_db",
            "asset_storage": "node_local_filesystem_paths",
            "lease_clock": "sqlite_transaction_domain",
            "turn_lease_fencing_generation": True,
            "job_lease_fencing_generation": True,
            "cross_node_lease_authority": False,
            "admin_control_state": "multiple_node_local_sqlite_databases",
        },
        "requirements": requirements,
        "blocker_count": len(blockers),
        "blocker_ids": [str(row["id"]) for row in blockers],
        "certification_gates": [
            {
                **row,
                "status": "not_run_against_multi_node_backend",
            }
            for row in CERTIFICATION_GATES
        ],
        "methodology": {
            "actual_backend_capability_detection": False,
            "replica_discovery": False,
            "self_attested_backend_capabilities_accepted": False,
            "database_url_swap_is_sufficient": False,
            "local_lease_fencing_is_cross_node_certification": False,
            "all_requirements_must_be_verified": True,
            "all_certification_gates_must_pass": True,
            "release_authority_granted": False,
        },
        "authority": authority_contract(),
    }
