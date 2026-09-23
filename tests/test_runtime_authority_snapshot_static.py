from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_authority_snapshot_is_read_only_and_fixed_sql():
    service = (ROOT / "ecomevo" / "product" / "runtime_authority_snapshot.py").read_text(
        encoding="utf-8"
    )

    assert 'connection.execute("BEGIN")' in service
    assert "BEGIN IMMEDIATE" not in service
    assert "PRAGMA query_only=ON" in service
    assert " INSERT " not in service.upper()
    assert " UPDATE " not in service.upper()
    assert " DELETE " not in service.upper()
    assert "os.environ" not in service
    assert "# nosec" not in service
    assert '"fingerprint_equality_proves_shared_transaction_domain": False' in service
    assert '"process_plugin_lifecycle_covered": False' in service
    assert '"cross_node_shared": False' in service
    assert '"cross_node_supported": False' in service
    assert '"multi_node_certification": False' in service


def test_runtime_authority_route_has_no_mutation_surface():
    routes = (ROOT / "ecomevo" / "api" / "runtime_authority_routes.py").read_text(
        encoding="utf-8"
    )

    path = "/api/runtime/readiness/runtime-authority"
    assert f'@app.get("{path}")' in routes
    assert f'@app.post("{path}")' not in routes
    assert f'@app.patch("{path}")' not in routes
    assert f'@app.delete("{path}")' not in routes
    assert "current_principal()" in routes
    assert "/api/actions" not in routes
    assert "tools/call" not in routes
