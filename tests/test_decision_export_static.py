from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_decision_export_surface_preserves_authority_and_safe_rendering():
    html = (ROOT / "frontend" / "decision-exports.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "decision-exports.js").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "decision-exports.css").read_text(encoding="utf-8")
    routes = (ROOT / "ecomevo" / "api" / "decision_export_routes.py").read_text(encoding="utf-8")
    service = (ROOT / "ecomevo" / "product" / "decision_exports.py").read_text(encoding="utf-8")

    assert "导出 ≠ 审批" in html
    assert "快照 ≠ 运行时真相" in html
    assert "SHA-256" in html
    assert "innerHTML" not in js
    assert "textContent" in js
    assert "@media (max-width:" in css

    assert ".transition_action(" not in routes
    assert ".update_action(" not in routes
    assert ".call_tool(" not in routes
    assert '"/api/actions/' not in routes
    assert "/publish" not in routes
    assert "/promote" not in routes
    assert "def update_snapshot(" not in service
    assert "def delete_snapshot(" not in service
    assert "UPDATE decision_exports" not in service
    assert "DELETE FROM decision_exports" not in service
    assert '"server_local_paths_included": False' in service
    assert '"collaboration": self._collaboration' in service
    assert '"collaboration_events": len(clean["collaboration"]["events"])' in service
    assert '"export_executes_tools": False' in service
