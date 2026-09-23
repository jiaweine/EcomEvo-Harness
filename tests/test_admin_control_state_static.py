from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_admin_control_state_manifest_is_query_only_and_fixed_scope():
    service = (
        ROOT / "ecomevo" / "product" / "admin_control_state_manifest.py"
    ).read_text(encoding="utf-8")

    assert "?mode=ro" in service
    assert "PRAGMA query_only=ON" in service
    assert 'connection.execute("BEGIN")' in service
    assert "BEGIN IMMEDIATE" not in service
    assert "os.environ" not in service
    assert "# nosec" not in service
    assert " INSERT " not in service.upper()
    assert " UPDATE " not in service.upper()
    assert " DELETE " not in service.upper()
    for filename in (
        "evaluation.db",
        "knowledge.db",
        "decision_exports.db",
        "release_readiness.db",
        "skill_studio.db",
        "connection_governance.db",
    ):
        assert filename in service
    for marker in (
        "evaluation_runs",
        "knowledge_sources",
        "decision_exports",
        "release_readiness_snapshots",
        "studio_skill_versions",
        "connection_probe_history",
    ):
        assert marker in service
    assert '"cross_database_atomic_snapshot": False' in service
    assert '"shared_across_application_nodes": False' in service
    assert '"cross_node_supported": False' in service
    assert '"requirement_current_satisfied": False' in service
    assert '"multi_node_ready": False' in service


def test_admin_control_state_route_has_no_mutation_surface():
    routes = (
        ROOT / "ecomevo" / "api" / "admin_control_state_routes.py"
    ).read_text(encoding="utf-8")

    path = "/api/runtime/readiness/admin-control-state"
    assert f'@app.get("{path}")' in routes
    assert f'@app.post("{path}")' not in routes
    assert f'@app.patch("{path}")' not in routes
    assert f'@app.delete("{path}")' not in routes
    assert "current_principal()" in routes
    assert "/api/actions" not in routes
    assert "tools/call" not in routes


def test_admin_manifest_route_installs_after_all_six_database_owners():
    app_source = (ROOT / "ecomevo" / "api" / "app.py").read_text(encoding="utf-8")

    install_at = app_source.index('"admin_control_state_routes_installed"')
    assert app_source.index('"evaluation_router_installed"') < install_at
    assert app_source.index('"knowledge_routes_installed"') < install_at
    assert app_source.index('"decision_export_routes_installed"') < install_at
    assert app_source.index('"release_readiness_routes_installed"') < install_at
    assert app_source.index('"skill_studio_routes_installed"') < install_at
