from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTRO_JS = (ROOT / 'frontend' / 'intro.js').read_text(encoding='utf-8')
INTRO_CSS = (ROOT / 'frontend' / 'intro.css').read_text(encoding='utf-8')
LIFECYCLE_JS = (ROOT / 'frontend' / 'lifecycle.js').read_text(encoding='utf-8')
LIFECYCLE_CSS = (ROOT / 'frontend' / 'lifecycle.css').read_text(encoding='utf-8')
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
    assert "method:'PATCH'" in INTRO_JS and 'hasMessages' in INTRO_JS and '/api/conversations/' in INTRO_JS


def test_incomplete_runtime_state_overrides_percent_ring():
    assert "label.includes('待补资料')" in INTRO_JS and "percent.textContent='待补'" in INTRO_JS and "label.includes('需要重试')" in INTRO_JS


def test_simulated_action_copy_is_explicit_and_real_execution_is_not_downgraded():
    assert '演示已完成' in LIFECYCLE_JS
    assert '没有调用真实业务系统' in LIFECYCLE_JS
    assert '未产生真实业务副作用' in LIFECYCLE_JS
    assert 'action-status executed' not in LIFECYCLE_JS


def test_asset_lifecycle_has_exclude_restore_and_guarded_delete():
    assert '/scope' in LIFECYCLE_JS
    assert '排除后续分析' in LIFECYCLE_JS
    assert '重新启用' in LIFECYCLE_JS
    assert '再次点击确认删除' in LIFECYCLE_JS
    assert '永久删除' in LIFECYCLE_JS
    assert '.asset-scope-state.excluded' in LIFECYCLE_CSS


def test_product_tour_remains_accessible():
    assert 'aria-describedby="productTourDescription"' in INDEX
    assert 'href="/assets/intro.css"' in INDEX
    assert INDEX.index('/assets/intro.css') < INDEX.index('/assets/visual.css')
    assert 'stopImmediatePropagation' in INTRO_JS
    assert 'focusables()' in INTRO_JS


def test_command_search_loads_more_recent_tasks():
    assert "/api/conversations?limit=100" in INTRO_JS
    assert 'setupExtendedCommandSearch' in INTRO_JS
    assert "searchParams.set('conversation'" in INTRO_JS


def test_lifecycle_module_is_loaded_as_product_layer():
    assert "import('/assets/lifecycle.js')" in INTRO_JS
