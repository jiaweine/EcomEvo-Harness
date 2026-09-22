from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_routing_off_policy_surface_is_read_only_and_statistically_honest():
    html = (ROOT / "frontend" / "routing-quality.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "routing-quality.js").read_text(encoding="utf-8")
    route = (ROOT / "ecomevo" / "api" / "routing_quality_routes.py").read_text(encoding="utf-8")
    service = (ROOT / "ecomevo" / "product" / "routing_off_policy.py").read_text(encoding="utf-8")
    combined = html + js + route + service

    assert "/api/runtime/routing-quality/off-policy" in js
    assert "Off-policy readiness" in html
    assert "deterministic_ucb" in service
    assert '"status": "unavailable"' in service
    assert "Doubly-robust estimation requires logged behavior propensity" in service
    assert "never inferred from utility, rank, activation, or deterministic UCB scores" in service

    assert "/api/actions" not in combined
    assert "tools/call" not in combined
    assert "method:'POST'" not in js.replace(" ", "")
    assert "method:'PATCH'" not in js.replace(" ", "")
    assert "method:'DELETE'" not in js.replace(" ", "")
    assert '"changes_routing": False' in service
    assert '"changes_policy": False' in service
    assert '"changes_runtime_skills": False' in service
    assert '"executes_tools": False' in service
