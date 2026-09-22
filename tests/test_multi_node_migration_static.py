from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_multi_node_contract_has_no_self_attested_unlock_surface():
    service = (ROOT / "ecomevo" / "product" / "multi_node_readiness.py").read_text(encoding="utf-8")
    routes = (ROOT / "ecomevo" / "api" / "release_readiness_routes.py").read_text(encoding="utf-8")

    assert "os.environ" not in service
    assert "ECOMEVO_" not in service
    assert '"ready": True' not in service
    assert '"ready": False' in service
    assert '"self_attested_backend_capabilities_accepted": False' in service
    assert '"database_url_swap_is_sufficient": False' in service
    assert '@app.get("/api/runtime/readiness/multi-node")' in routes
    assert '@app.post("/api/runtime/readiness/multi-node")' not in routes
    assert '@app.patch("/api/runtime/readiness/multi-node")' not in routes
    assert '@app.delete("/api/runtime/readiness/multi-node")' not in routes


def test_multi_node_contract_names_current_local_state_boundaries():
    service = (ROOT / "ecomevo" / "product" / "multi_node_readiness.py").read_text(encoding="utf-8")

    for token in (
        "sqlite_wal_local_file",
        "sqlite_transaction_clock_with_monotonic_fencing",
        "node_local_filesystem_paths",
        "sqlite_wal_local_runtime_db",
        "multiple_node_local_sqlite_databases",
        "shared_authoritative_lease_clock_and_fencing_tokens",
        "local_lease_fencing_is_cross_node_certification",
        "shared_content_addressed_or_object_storage_with_hash_verification",
        "cross_node_business_action_cas",
        "cross_node_failure_recovery",
    ):
        assert token in service


def test_multi_node_readiness_cannot_change_authority_or_deploy():
    service = (ROOT / "ecomevo" / "product" / "multi_node_readiness.py").read_text(encoding="utf-8")

    assert '"changes_runtime_topology": False' in service
    assert '"changes_storage_backend": False' in service
    assert '"changes_routing": False' in service
    assert '"changes_policy": False' in service
    assert '"changes_runtime_skills": False' in service
    assert '"approves_business_actions": False' in service
    assert '"executes_tools": False' in service
    assert '"deploys_code": False' in service
