from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_evaluation_center_surface_is_release_focused_and_read_only():
    html = (ROOT / "frontend" / "evaluation.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "evaluation.js").read_text(encoding="utf-8")
    api = (ROOT / "ecomevo" / "api" / "evaluation_api.py").read_text(encoding="utf-8")

    assert "Fresh ↔ Persisted replay" in html
    assert "评估不会改写生产 routing、skill、evolution 或审批状态" in html
    assert "运行 Gold Set" in html
    assert "模型置信度" not in html
    assert "/api/runtime/evaluations" in js
    assert "verifier_score" not in html
    assert "@router.post(\"/runs\"" in api
    assert "delete" not in api.lower()
    assert "patch" not in api.lower()


def test_evaluation_center_has_mobile_layout_and_safe_dynamic_rendering():
    css = (ROOT / "frontend" / "evaluation.css").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "evaluation.js").read_text(encoding="utf-8")

    assert "@media(max-width:620px)" in css
    assert "textContent" in js
    assert "innerHTML" not in js
