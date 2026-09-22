from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_routing_quality_frontend_is_read_only():
    js = (ROOT / "frontend" / "routing-quality.js").read_text(encoding="utf-8")
    route = (ROOT / "ecomevo" / "api" / "routing_quality_routes.py").read_text(encoding="utf-8")
    service = (ROOT / "ecomevo" / "product" / "routing_quality.py").read_text(encoding="utf-8")
    combined = js + route + service

    assert "/api/runtime/routing-quality" in js
    assert "/api/actions" not in combined
    assert "tools/call" not in combined
    assert "method:'POST'" not in js.replace(" ", "")
    assert "method:'PATCH'" not in js.replace(" ", "")
    assert "method:'DELETE'" not in js.replace(" ", "")
    assert "changes_routing" in service
    assert '"changes_routing": False' in service
    assert '"changes_policy": False' in service
    assert '"changes_runtime_skills": False' in service
    assert '"executes_tools": False' in service


def test_routing_quality_does_not_claim_residual_delta_is_a_drift_verdict():
    html = (ROOT / "frontend" / "routing-quality.html").read_text(encoding="utf-8")
    service = (ROOT / "ecomevo" / "product" / "routing_quality.py").read_text(encoding="utf-8")
    assert "描述性趋势，不自动判定 drift" in html
    assert "descriptive, not drift verdicts" in service


def test_routing_quality_page_has_mobile_layout():
    css = (ROOT / "frontend" / "routing-quality.css").read_text(encoding="utf-8")
    assert "@media(max-width:980px)" in css
    assert "@media(max-width:640px)" in css
