from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_operator_activity_frontend_requires_foreground_recent_interaction():
    drawer = (ROOT / "frontend" / "drawer-a11y.js").read_text(encoding="utf-8")
    script = (ROOT / "frontend" / "operator-activity.js").read_text(encoding="utf-8")

    assert "/assets/operator-activity.js" in drawer
    assert 'document.visibilityState === "visible"' in script
    assert "document.hasFocus()" in script
    assert "ACTIVE_LEASE_MS = 30_000" in script
    for event_name in ("pointerdown", "keydown", "touchstart", "input"):
        assert event_name in script
    assert '"/api/operator-activity/heartbeat"' in script
    assert 'JSON.stringify({surface: "workbench"})' in script
    assert "duration_seconds" not in script
    assert "timestamp:" not in script
    assert "/api/actions" not in script
    assert "tools/call" not in script


def test_operator_activity_api_rejects_client_time_claims_by_schema():
    source = (ROOT / "ecomevo" / "api" / "operator_activity_routes.py").read_text(encoding="utf-8")
    assert 'ConfigDict(extra="forbid")' in source
    assert "tenant_id=principal.tenant_id" in source
    assert "user_id=principal.user_id" in source
    assert '"client_duration_accepted": False' in source
    assert '"changes_authority": False' in source


def test_observability_surfaces_real_vdph_without_timekeeping_overclaim():
    html = (ROOT / "frontend" / "observability.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "observability.js").read_text(encoding="utf-8")

    assert "Verified / operator hour" in js
    assert "Operator hours" in js
    assert "不是考勤或薪资证据" in html
    assert "verified_decisions_per_operator_hour" in js
    assert "operator_hours" in js
