from __future__ import annotations

import os
from typing import Any


DEPLOYMENT_NODES_ENV = "ECOMEVO_DEPLOYMENT_NODES"
CERTIFIED_MAX_NODES = 1


def evaluate_deployment_topology(
    raw_nodes: str | int | None,
    *,
    source: str = "explicit",
) -> dict[str, Any]:
    """Compare declared deployment intent with the current storage boundary.

    This is declarative rather than replica discovery. The current durable control
    plane uses SQLite WAL and is validated for multiple processes on one node, not
    for multiple application nodes sharing a filesystem.
    """

    explicit = raw_nodes is not None
    text = "" if raw_nodes is None else str(raw_nodes).strip()
    present = bool(text)
    declared_nodes: int | None = None
    valid = False
    if present:
        try:
            declared_nodes = int(text)
            valid = declared_nodes >= 1
        except (TypeError, ValueError):
            declared_nodes = None

    supported_declared_topology = bool(
        valid and declared_nodes == CERTIFIED_MAX_NODES
    )
    # Missing declaration remains a backwards-compatible local/runtime mode, but is
    # never enough to satisfy Release Readiness. An explicit invalid or unsupported
    # declaration is different: the process must not knowingly start in that topology.
    runtime_start_allowed = bool(
        not explicit or supported_declared_topology
    )

    if not explicit:
        reason = (
            f"{DEPLOYMENT_NODES_ENV} is not declared; runtime may start in "
            "compatibility mode, but release readiness cannot assume a single-node "
            "topology"
        )
    elif not present:
        reason = (
            f"{DEPLOYMENT_NODES_ENV} is explicitly blank; runtime startup is refused "
            "because an explicit deployment declaration must be a positive integer"
        )
    elif not valid:
        reason = (
            f"{DEPLOYMENT_NODES_ENV} must be a positive integer; runtime startup and "
            "release readiness fail closed on an invalid declaration"
        )
    elif declared_nodes and declared_nodes > CERTIFIED_MAX_NODES:
        reason = (
            "the current SQLite WAL control plane is certified only for one application "
            "node; runtime startup is refused until product state, event log, and durable "
            "queue use a centralized transactional backend"
        )
    else:
        reason = (
            "declared single-node deployment matches the current SQLite WAL durability "
            "boundary; multiple worker processes on that node remain supported"
        )

    return {
        "schema_version": 1,
        "storage_backend": "sqlite_wal",
        "declaration_env": DEPLOYMENT_NODES_ENV,
        "declaration_source": source,
        "declaration_explicit": explicit,
        "declaration_present": present,
        "declaration_valid": valid,
        "declared_nodes": declared_nodes if valid else None,
        "certified_max_nodes": CERTIFIED_MAX_NODES,
        "same_node_multi_process_supported": True,
        "cross_node_supported": False,
        "actual_replica_discovery": False,
        "requires_central_transactional_backend_for_multi_node": True,
        "runtime_start_allowed": runtime_start_allowed,
        "release_supported": supported_declared_topology,
        "reason": reason,
    }


def current_deployment_topology() -> dict[str, Any]:
    if DEPLOYMENT_NODES_ENV not in os.environ:
        return evaluate_deployment_topology(None, source="missing")
    return evaluate_deployment_topology(
        os.environ.get(DEPLOYMENT_NODES_ENV),
        source="environment",
    )


def require_runtime_topology_supported() -> dict[str, Any]:
    """Reject an explicitly unsupported topology before the application serves."""

    topology = current_deployment_topology()
    if topology["runtime_start_allowed"]:
        return topology

    declared = topology.get("declared_nodes")
    declared_text = (
        str(declared)
        if declared is not None
        else "invalid"
    )
    raise RuntimeError(
        "unsupported EcomEvo deployment topology: "
        f"{topology['declaration_env']}={declared_text}; "
        f"{topology['reason']}"
    )
