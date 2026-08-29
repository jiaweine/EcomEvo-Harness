from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THEME = (ROOT / "frontend/customer-theme.css").read_text(encoding="utf-8")
POLISH = (ROOT / "frontend/customer-polish.css").read_text(encoding="utf-8")
DRAWER = (ROOT / "frontend/drawer-a11y.js").read_text(encoding="utf-8")


def test_customer_visual_system_uses_native_ai_product_font_stack():
    for token in (
        "ui-sans-serif",
        "-apple-system",
        "BlinkMacSystemFont",
        '"Segoe UI"',
        '"PingFang SC"',
        '"Microsoft YaHei"',
    ):
        assert token in POLISH
    assert "font-family:var(--font-ui)!important" in POLISH


def test_customer_shell_is_light_two_column_workbench_with_context_drawer():
    assert "--customer-sidebar:#f7f7f8" in POLISH
    assert "grid-template-columns:var(--left) minmax(0,1fr)!important" in POLISH
    assert ".rightbar{" in POLISH
    assert "position:fixed!important" in POLISH
    assert "right:-430px!important" in POLISH
    assert ".rightbar.open{right:0!important}" in POLISH
    assert "#detailToggle" in POLISH
    assert "display:inline-flex!important" in POLISH
    assert "return true;" in DRAWER


def test_customer_primary_column_matches_chat_first_ai_layout():
    assert "width:min(100%,780px)!important" in POLISH
    assert ".welcome-layout{display:block!important}" in POLISH
    assert "#welcomePanel .ops-overview{display:none!important}" in POLISH
    assert ".welcome .agent-map{display:none!important}" in POLISH
    assert "grid-template-columns:1fr 1fr!important" in POLISH
    assert "border-radius:24px!important" in POLISH
    assert '.send-btn::before{content:"↑"' in POLISH


def test_customer_theme_keeps_semantic_colors_separate_from_neutral_interaction_color():
    assert "--customer-success:#23845b" in THEME
    assert "--customer-warning:#a76818" in THEME
    assert "--customer-danger:#c54545" in THEME
    assert "--customer-accent:#202020" in POLISH
