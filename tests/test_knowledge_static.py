from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_knowledge_ui_states_authority_boundary_and_mobile_breakpoints():
    html = (ROOT / "frontend" / "knowledge.html").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "knowledge.css").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "knowledge.js").read_text(encoding="utf-8")

    assert "Published ≠ Runtime Evidence" in html
    assert "S1 / S3" in html
    assert "eligible_for_runtime_evidence" in js
    assert "@media(max-width:720px)" in css
    assert "@media(max-width:420px)" in css
    assert "const esc =" in js


def test_knowledge_frontend_has_no_action_tool_or_runtime_injection_path():
    js = (ROOT / "frontend" / "knowledge.js").read_text(encoding="utf-8")
    routes = (ROOT / "ecomevo" / "api" / "knowledge_routes.py").read_text(encoding="utf-8")
    store = (ROOT / "ecomevo" / "product" / "knowledge_sources.py").read_text(encoding="utf-8")
    combined = js + routes + store

    assert "/api/actions" not in combined
    assert "/decision" not in combined
    assert "tools/call" not in combined
    assert ".upsert_candidate(" not in combined
    assert ".promote(" not in combined
    assert "add_asset(" not in routes
    assert "eligible_for_runtime_evidence" in store
    assert '"s1_assignment_allowed": False' in store
    assert '"open_web_unlocks_high_impact_actions": False' in store
