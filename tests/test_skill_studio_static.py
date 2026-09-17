from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_skill_studio_has_no_production_promotion_or_action_execution_route():
    routes = (ROOT / "ecomevo/api/skill_studio_routes.py").read_text(encoding="utf-8")
    assert '"/api/runtime/skills/ui"' in routes
    assert "install_skill_studio_routes" in routes
    assert "/promote" not in routes
    assert "/publish" not in routes
    assert "/api/actions" not in routes
    assert "tools/call" not in routes
    assert ".promote(" not in routes


def test_skill_studio_ui_explains_evaluation_is_not_production_authority():
    html = (ROOT / "frontend/skill-studio.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend/skill-studio.js").read_text(encoding="utf-8")
    css = (ROOT / "frontend/skill-studio.css").read_text(encoding="utf-8")
    assert "评估通过不等于生产启用" in html
    assert "不能直接上线" in html
    assert "不是证据或权限" in html
    assert "Studio 不会修改这些状态" in html
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
    assert "evaluation_link_auto_promotes" in source
    assert '"can_promote_runtime": False' in source
    assert "UPDATE studio_skill_versions" not in source
    assert "DELETE FROM studio_skill_versions" not in source
    assert "runtime_policies" in source
