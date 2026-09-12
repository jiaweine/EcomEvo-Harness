from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
V5 = (ROOT / "frontend/workbench-v5.css").read_text(encoding="utf-8")


def test_contextual_workbench_layer_loads_last():
    provider = HTML.index('href="/assets/provider-marketplace.css"')
    workbench = HTML.index('href="/assets/workbench-v5.css"')
    assert provider < workbench


def test_empty_state_exposes_all_five_business_scenes():
    quick_grid = HTML.split('<div class="quick-grid">', 1)[1].split('</div>\n            <div class="input-types"', 1)[0]
    for scene in (
        "product_governance",
        "merchant_review",
        "aftersales",
        "risk_review",
        "content_audit",
    ):
        assert f'data-scene="{scene}"' in quick_grid


def test_recent_cases_keep_operational_metadata_visible():
    assert ".conv-item small{" in V5
    assert "display:block!important" in V5


def test_active_case_docks_context_only_on_wide_screens():
    assert "@media (min-width:1380px)" in V5
    assert ".app-shell:has(#messageList .msg)" in V5
    assert "grid-template-columns:var(--left) minmax(0,1fr) var(--v5-context)!important" in V5
    assert "grid-column:3!important" in V5
    assert "position:relative!important" in V5


def test_mid_and_small_screens_keep_adaptive_reduction():
    assert "@media (max-width:1180px)" in V5
    assert "@media (max-width:820px)" in V5
    assert "@media (max-width:560px)" in V5
    assert "prefers-reduced-motion:reduce" in V5


def test_context_button_is_explicit_instead_of_ellipsis():
    assert 'id="detailToggle"' in HTML
    assert '>详情</button>' in HTML
