from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_shadow_environment_cannot_execute_real_systems_or_mutate_runtime():
    service = (ROOT / "ecomevo" / "product" / "shadow_environment.py").read_text(encoding="utf-8")
    routes = (ROOT / "ecomevo" / "api" / "shadow_environment_routes.py").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "shadow-environment.js").read_text(encoding="utf-8")
    combined = service + routes

    forbidden = (
        "call_tool(",
        "tools/call",
        "subprocess",
        "playwright",
        "selenium",
        "os.system",
        "save_actions(",
        "update_action(",
        "add_event(",
        "routing.apply",
        "policy.publish",
    )
    for token in forbidden:
        assert token not in combined

    assert '"/api/runtime/shadow/simulate"' in routes
    assert '"/api/runtime/shadow/catalog"' in routes
    assert '"/api/runtime/shadow/simulate"' in js
    assert "/api/actions" not in js
    assert "/api/runtime/connections/" not in js
    assert '"executes_real_tools": False' in service
    assert '"launches_real_browser": False' in service
    assert '"launches_real_terminal": False' in service
    assert '"changes_business_action_state": False' in service


def test_shadow_ui_states_offline_and_non_authoritative_contract():
    html = (ROOT / "frontend" / "shadow-environment.html").read_text(encoding="utf-8")
    assert "OFFLINE REPLAY ONLY" in html
    assert "不会触发 MCP tools/call" in html
    assert "只生成 replay candidate" in html
    assert "Deterministic authority 不变" in html
