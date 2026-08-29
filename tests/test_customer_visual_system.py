from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THEME = (ROOT / "frontend/customer-theme.css").read_text(encoding="utf-8")
POLISH = (ROOT / "frontend/customer-polish.css").read_text(encoding="utf-8")


def test_customer_visual_system_has_one_distinct_brand_palette():
    for token in (
        "--customer-canvas:#f4f5f8",
        "--customer-sidebar:#15161a",
        "--customer-accent:#5b5bd6",
        "--customer-surface:#ffffff",
        "--customer-line:#dedfe5",
    ):
        assert token in THEME
    assert "--customer-canvas:#f5f1ec" not in THEME


def test_customer_visual_hierarchy_keeps_dark_navigation_and_light_workspace():
    assert ".leftbar{" in THEME
    assert "linear-gradient(180deg,#17181c 0%,#131418 100%)" in THEME
    assert ".workspace{" in THEME
    assert "background:var(--customer-canvas)!important" in THEME
    assert ".top-left{" in THEME
    assert "background:var(--customer-sidebar)!important" in THEME
    assert ".top-left" not in POLISH


def test_customer_welcome_uses_asymmetric_product_layout_not_dashboard_tiles():
    assert "grid-template-columns:1.12fr .88fr!important" in THEME
    assert ".quick-card:nth-child(1)" in THEME
    assert "grid-row:span 2!important" in THEME
    assert ".quick-card:nth-child(4)" in THEME
    assert "grid-column:1/-1!important" in THEME
    assert "border-radius:14px!important" in THEME


def test_customer_theme_keeps_semantic_colors_separate_from_brand_accent():
    assert "--customer-success:#23845b" in THEME
    assert "--customer-warning:#a76818" in THEME
    assert "--customer-danger:#c54545" in THEME
    assert "--customer-accent:#5b5bd6" in THEME
