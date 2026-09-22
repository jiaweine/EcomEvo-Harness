from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_skill_studio_has_no_production_promotion_or_action_execution_route():
    routes = (ROOT / "ecomevo/api/skill_studio_routes.py").read_text(encoding="utf-8")
    assert '"/api/runtime/skills/ui"' in routes
    assert '"/api/runtime/skills/studio/{version_id}/evaluate"' in routes
    assert "install_skill_studio_routes" in routes
    assert '"/api/runtime/skills/studio/{version_id}/promote"' not in routes
    assert '"/api/runtime/skills/studio/{version_id}/publish"' not in routes
    assert "/api/actions" not in routes
    assert "tools/call" not in routes
    assert ".promote(" not in routes


def test_skill_studio_ui_explains_evaluation_is_not_production_authority():
    html = (ROOT / "frontend/skill-studio.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend/skill-studio.js").read_text(encoding="utf-8")
    css = (ROOT / "frontend/skill-studio.css").read_text(encoding="utf-8")
    assert "评估通过不等于生产启用" in html
    assert "租户隔离" in html
    assert "隔离评估" in html
    assert "Studio 不会修改这些状态" in html
    assert "不把 guidance 当成 Evidence" in html
    assert "运行隔离候选评估" in html
    assert "Tenant scope" in html
    assert "Deployment · 只读" in html
    assert "导出 Release Candidate" in html
    assert "release-candidate" in routes
    assert "tenant_id=principal.tenant_id" in routes
    assert "innerHTML" not in js
    assert "textContent" in js
    assert "/api/actions" not in js
    assert "tools/call" not in js
    assert "@media(max-width:620px)" in css
    assert "44px" in css


def test_studio_store_has_separate_immutable_tables_and_false_authority_contract():
    source = (ROOT / "ecomevo/product/skill_studio.py").read_text(encoding="utf-8")
    assert "studio_skill_versions" in source
    assert "studio_skill_events" in source
    assert "studio_skill_evaluations" in source
    assert "evaluation_pass_auto_promotes" in source
    assert "candidate_evaluation_mutates_production" in source
    assert '"can_promote_runtime": False' in source
    assert '"studio_cross_tenant_visibility": False' in source
    assert '"release_candidate_activates_runtime": False' in source
    assert "tenant_id" in source
    assert "def release_candidate(" in source
    assert "TemporaryDirectory" in source
    assert '"production_runtime_mutated": False' in source
    assert '"production_skill_promoted": False' in source
    assert "UPDATE studio_skill_versions" not in source
    assert "DELETE FROM studio_skill_versions" not in source
    assert "runtime_policies" in source
