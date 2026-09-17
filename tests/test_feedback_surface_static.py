from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_workbench_feedback_surface_is_progressively_loaded():
    drawer = (ROOT / "frontend" / "drawer-a11y.js").read_text(encoding="utf-8")
    assert "/assets/feedback-surface.css" in drawer
    assert "/assets/feedback-surface.js" in drawer
    assert "data-ecomevo-feedback-surface" in drawer


def test_operator_feedback_surface_only_submits_quality_signal():
    js = (ROOT / "frontend" / "feedback-surface.js").read_text(encoding="utf-8")
    assert "/api/feedback/capabilities" in js
    assert "/feedback/targets" in js
    assert "/feedback`" in js or "/feedback" in js
    assert "/api/actions" not in js
    assert "/decision" not in js
    assert "tools/call" not in js
    assert "accepted_for_eval" not in js
    assert "反馈只会进入质量复核" in js


def test_admin_feedback_console_does_not_auto_promote_or_execute_actions():
    js = (ROOT / "frontend" / "feedback-admin.js").read_text(encoding="utf-8")
    html = (ROOT / "frontend" / "feedback-admin.html").read_text(encoding="utf-8")
    assert "/api/runtime/feedback" in js
    assert "accepted_for_eval" in js
    assert "evaluation-sample" in js
    assert "/api/actions" not in js
    assert "/decision" not in js
    assert "tools/call" not in js
    assert "不会自动加入 Gold Set" in html
    assert "不会修改 policy、routing、Verifier 或 BusinessAction" in html


def test_feedback_dialog_remains_mobile_usable():
    css = (ROOT / "frontend" / "feedback-surface.css").read_text(encoding="utf-8")
    assert "100dvh" in css
    assert "safe-area-inset-bottom" in css
    assert "font-size:16px" in css
