from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_policy_workflow_routes_keep_immutable_maker_checker_surface():
    source = (ROOT / "ecomevo/api/policy_workflow_routes.py").read_text(encoding="utf-8")
    assert '"/api/runtime/policies/drafts"' in source
    assert '"/api/runtime/policies/{policy_id}/versions/{version}/approve"' in source
    assert '"/api/runtime/policies/{policy_id}/versions/{version}/publish"' in source
    assert '"/api/runtime/policies/{policy_id}/versions/{version}/retirement-requests"' in source
    assert '"/api/runtime/policies/{policy_id}/versions/{version}/retire"' in source
    assert "@app.patch(" not in source
    assert "@app.delete(" not in source
    assert "creator cannot self-approve" not in source  # enforced in workflow core, not client text


def test_policy_workflow_core_has_tenant_boundary_preview_hash_and_atomic_audit():
    source = (ROOT / "ecomevo/runtime/policy_workflow.py").read_text(encoding="utf-8")
    assert "policy_workflow_audit" in source
    assert "BEGIN IMMEDIATE" in source
    assert "source.backup(target)" in source
    assert "preview_hash" in source
    assert "policy environment changed after approval" in source
    assert "creator cannot self-approve" in source
    assert "retirement requester cannot self-approve retirement" in source
    assert "builtin policy versions are read-only" in source
    assert 'clean["tenant"] = str(tenant_id)' in source
    assert '"preview_mutates_production": False' in source
    assert '"cross_tenant_write_allowed": False' in source


def test_policy_center_ui_explains_authority_and_avoids_dynamic_html_injection():
    html = (ROOT / "frontend/policy-center.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend/policy-center.js").read_text(encoding="utf-8")
    css = (ROOT / "frontend/policy-center.css").read_text(encoding="utf-8")
    assert "Maker ≠ Checker" in html
    assert "Preview ≠ Publish" in html
    assert "Tenant scoped" in html
    assert "Audit atomic" in html
    assert "不会立即生效" in html
    assert "当前生效解析" in html
    assert "innerHTML" not in js
    assert "textContent" in js
    assert "/api/runtime/policies/drafts" in js
    assert "confirm(" in js
    assert "@media(max-width:620px)" in css
    assert "44px" in css
