from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
PRIVACY = (ROOT / "frontend/privacy-sanitize.js").read_text(encoding="utf-8")


def test_privacy_sanitizer_loads_before_core_render_hooks():
    assert "/assets/privacy-sanitize.js" in HTML
    assert HTML.index("/assets/privacy-sanitize.js") < HTML.index("/assets/enhancements-core.js")


def test_answer_provider_is_rewritten_immediately_on_dom_insertion():
    assert ".answer-provider" in PRIVACY
    assert "受控运行时 · 已完成" in PRIVACY
    assert "MutationObserver" in PRIVACY
    assert "childList: true" in PRIVACY
    assert "requestAnimationFrame" not in PRIVACY
