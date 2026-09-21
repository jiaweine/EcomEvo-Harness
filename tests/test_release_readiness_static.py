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


def test_readiness_page_has_mobile_layout():
    css = (ROOT / "frontend" / "release-readiness.css").read_text(encoding="utf-8")

    assert "@media(max-width:900px)" in css
    assert "@media(max-width:640px)" in css
    assert "@media(max-width:420px)" in css
