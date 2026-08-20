from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTRO_JS = (ROOT / 'frontend' / 'intro.js').read_text(encoding='utf-8')
INTRO_CSS = (ROOT / 'frontend' / 'intro.css').read_text(encoding='utf-8')
INDEX = (ROOT / 'frontend' / 'index.html').read_text(encoding='utf-8')


def test_intro_example_is_scene_aware():
    for scene in ('product_governance','merchant_review','aftersales','risk_review','content_audit'):
        assert scene in INTRO_JS
    assert "SCENE_COPY[activeScene()]" in INTRO_JS


def test_share_link_does_not_propagate_forced_tour():
    assert "searchParams.delete('tour')" in INTRO_JS


def test_asset_button_opens_instead_of_toggles_drawer():
    assert "classList.add('open')" in INTRO_JS
    assert "data-panel=\"assets\"" in INTRO_JS


def test_empty_task_scene_switch_reuses_current_conversation():
    assert "method:'PATCH'" in INTRO_JS
    assert "hasMessages" in INTRO_JS
    assert "/api/conversations/" in INTRO_JS


def test_incomplete_runtime_state_overrides_percent_ring():
    assert "label.includes('待补资料')" in INTRO_JS
    assert "percent.textContent='待补'" in INTRO_JS
    assert "label.includes('需要重试')" in INTRO_JS


def test_executed_action_copy_is_conservative():
    assert '处理记录已完成' in INTRO_JS
    assert '最终业务状态请以对应系统为准' in INTRO_JS
    assert '.execution-caveat' in INTRO_CSS


def test_asset_lifecycle_is_explicit_until_delete_api_exists():
    assert '资料范围' in INTRO_JS
    assert '持续参与后续核对' in INTRO_JS
    assert '.asset-policy-note' in INTRO_CSS


def test_product_tour_remains_accessible():
    assert 'aria-describedby="productTourDescription"' in INDEX
    assert 'stopImmediatePropagation' in INTRO_JS
    assert 'focusables()' in INTRO_JS
