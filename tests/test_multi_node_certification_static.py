from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_certification_contract_has_no_execution_or_self_attested_unlock_surface():
    service = (
        ROOT / "ecomevo" / "product" / "multi_node_certification.py"
    ).read_text(encoding="utf-8")

    assert "os.environ" not in service
    assert "httpx" not in service
    assert "requests" not in service
    assert "subprocess" not in service
    assert "tools/call" not in service
    assert '"certification_passed": False' in service
    assert '"passed": False' in service
    assert '"requires_real_cross_node_execution": True' in service
    assert '"same_process_simulation_accepted": False' in service
    assert '"same_host_multi_process_accepted": False' in service
    assert '"client_supplied_pass_claim_accepted": False' in service
    assert '"self_attested_backend_capabilities_accepted": False' in service
    assert '"client_supplied_evidence_accepted": False' in service
    assert '"historical_single_node_ci_sufficient": False' in service
    assert '"release_authority_granted": False' in service


def test_certification_endpoint_is_get_only_and_admin_guarded():
    routes = (
        ROOT / "ecomevo" / "api" / "release_readiness_routes.py"
    ).read_text(encoding="utf-8")
    path = "/api/runtime/readiness/multi-node/certification-contract"

    assert f'@app.get("{path}")' in routes
    assert f'@app.post("{path}")' not in routes
    assert f'@app.put("{path}")' not in routes
    assert f'@app.patch("{path}")' not in routes
    assert f'@app.delete("{path}")' not in routes
    block = routes.split(f'@app.get("{path}")', 1)[1].split("@app.", 1)[0]
    assert "current_principal()" in block
    assert "certification_contract()" in block


def test_contract_names_all_six_real_certification_gates():
    service = (
        ROOT / "ecomevo" / "product" / "multi_node_certification.py"
    ).read_text(encoding="utf-8")
    for gate_id in (
        "cross_node_job_lease_handoff",
        "cross_node_business_action_cas",
        "cross_node_event_reconnect",
        "cross_node_asset_snapshot_integrity",
        "cross_node_authority_consistency",
        "cross_node_failure_recovery",
    ):
        assert gate_id in service
