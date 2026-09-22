from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readiness_ui_never_presents_readiness_as_release_approval():
    html = (ROOT / "frontend" / "release-readiness.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "release-readiness.js").read_text(encoding="utf-8")

    assert "Readiness ≠ Approval" in html
    assert "可进入人工发布评审" in js
    assert "这不是发布批准" in js
    assert "Warnings ≠ Blockers" in html


def test_readiness_frontend_has_no_production_action_surface():
    js = (ROOT / "frontend" / "release-readiness.js").read_text(encoding="utf-8")
    routes = (ROOT / "ecomevo" / "api" / "release_readiness_routes.py").read_text(encoding="utf-8")
    combined = js + routes

    assert "/api/actions" not in combined
    assert "/decision" not in combined
    assert "tools/call" not in combined
    assert "/publish" not in combined
    assert "/promote" not in combined
    assert "merge_pull_request" not in combined
    assert "method: 'PATCH'" not in js
    assert "method: 'DELETE'" not in js


def test_readiness_service_has_no_arbitrary_quality_threshold():
    service = (ROOT / "ecomevo" / "product" / "release_readiness.py").read_text(encoding="utf-8")

    assert '"success_rate_threshold": None' in service
    assert '"evidence_gap_threshold": None' in service
    assert "ready_for_human_release_review" in service
    assert '"approved_for_release": False' in service
    assert '"deployment_topology_is_declared_not_discovered": True' in service
    assert '"deployment_topology_can_change_runtime": False' in service


def test_readiness_page_has_mobile_layout():
    css = (ROOT / "frontend" / "release-readiness.css").read_text(encoding="utf-8")

    assert "@media(max-width:900px)" in css
    assert "@media(max-width:640px)" in css
    assert "@media(max-width:420px)" in css


def test_deployment_topology_guard_fails_closed_without_claiming_replica_discovery():
    service = (ROOT / "ecomevo" / "product" / "deployment_topology.py").read_text(encoding="utf-8")

    assert 'DEPLOYMENT_NODES_ENV = "ECOMEVO_DEPLOYMENT_NODES"' in service
    assert '"cross_node_supported": False' in service
    assert '"actual_replica_discovery": False' in service
    assert '"requires_central_transactional_backend_for_multi_node": True' in service


def test_readiness_frontend_uses_exact_feedback_count_contract():
    js = (ROOT / "frontend" / "release-readiness.js").read_text(encoding="utf-8")
    service = (ROOT / "ecomevo" / "product" / "release_readiness.py").read_text(encoding="utf-8")

    assert "feedback.open_count" in js
    assert "feedback.exact_count" in js
    assert "open_sample_count" not in js
    assert '"open_count": open_count' in service
    assert '"exact_count": True' in service


def test_connection_readiness_is_get_only_and_never_executes_business_tools():
    routes = (ROOT / "ecomevo" / "api" / "release_readiness_routes.py").read_text(encoding="utf-8")
    readiness = (ROOT / "ecomevo" / "product" / "release_readiness.py").read_text(encoding="utf-8")
    catalog = (ROOT / "ecomevo" / "product" / "connection_catalog.py").read_text(encoding="utf-8")
    assert '@app.get("/api/runtime/readiness/connections")' in routes
    assert '@app.post("/api/runtime/readiness/connections")' not in routes
    assert '@app.patch("/api/runtime/readiness/connections")' not in routes
    assert '@app.delete("/api/runtime/readiness/connections")' not in routes
    assert '"business_tool_execution": False' in catalog
    assert '"provider_rate_limits_certified": False' in catalog
    assert '"side_effect_idempotency_behavior_certified": False' in catalog
    assert '"connection_success_rate_threshold": None' in readiness
    assert '"connection_latency_threshold_ms": None' in readiness
    assert "tools/call" not in routes
    assert "tools/call" not in readiness
