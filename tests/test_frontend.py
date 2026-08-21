import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'frontend/index.html').read_text(encoding='utf-8')
CSS=(ROOT/'frontend/app.css').read_text(encoding='utf-8')
JS=(ROOT/'frontend/app.js').read_text(encoding='utf-8')


def test_hidden_attribute_cannot_be_overridden_by_component_display():
    assert CSS.lstrip().startswith('[hidden]{display:none!important}')


def test_mobile_navigation_and_task_detail_drawers_exist():
    assert 'id="navToggle"' in HTML and 'id="detailToggle"' in HTML
    assert '.leftbar.open' in CSS and '.rightbar.open' in CSS and '@media(max-width:820px)' in CSS


def test_command_palette_and_keyboard_shortcuts_are_present():
    assert 'id="commandModal"' in HTML and 'id="commandInput"' in HTML
    assert "e.key.toLowerCase()==='k'" in JS and "e.key.toLowerCase()==='n'" in JS
    assert "$('productTour')?.hidden===false" in JS
    assert "$('providerModal')?.hidden===false" in JS


def test_action_ui_handles_inflight_and_uncertain_state():
    assert 'actionBusy:new Set()' in JS
    assert "uncertain:'结果待核对'" in JS
    assert '不要直接重复操作' in JS


def test_frontend_uses_safe_preview_endpoint_for_media():
    assert '/preview/0' in JS
    assert 'src="/api/assets/${a.id}/file"' not in JS


def test_frontend_surfaces_bounded_history_notice_and_task_summary():
    assert 'id="historyNotice"' in HTML and '最近 200 条记录' in HTML
    assert 'id="assetCountChip"' in HTML and 'id="actionCountChip"' in HTML and 'id="taskReadyChip"' in HTML
    assert 'historyTruncated' in JS and 'updateTaskSummary' in JS


def test_customer_surface_avoids_internal_runtime_jargon():
    customer=(HTML+'\n'+CSS).lower()
    for word in ['belief state','verifier','rollback','adaptive planner','recursive agent','harness 边界','策略先验']:
        assert word not in customer


def test_data_disclosure_is_accurate_for_external_providers():
    assert '选择外部服务时，当前任务内容会按配置发送到对应服务' in HTML
    assert '业务数据留在服务端' not in HTML


def test_mobile_brand_remains_identifiable():
    assert '.brand-copy small{display:none}' in CSS
    assert '.brand-copy b{font-size:15px}' in CSS
    assert '商业决策工作台' in HTML


def test_upload_captures_original_conversation_to_avoid_cross_task_race():
    assert 'const targetId=state.conversation?.id' in JS
    assert 'state.conversation?.id===targetId' in JS


def test_websocket_reconnect_uses_backoff_and_state_refresh():
    assert 'reconnectAttempt' in JS and 'Math.min(15000' in JS and 'refreshCurrentQuiet' in JS


def test_old_websocket_events_cannot_contaminate_new_task():
    assert "if(state.conversation?.id!==cid)return;try{handleEvent" in JS


def test_proposed_action_opens_detail_drawer_without_toggling_it_closed():
    assert "if(isNarrowRight())openDrawer('rightbar')" in JS


def test_failed_navigation_does_not_disconnect_current_task_before_fetch_succeeds():
    needle="async function openConversation(id){const seq=++state.navSeq;const d=await api"
    assert needle in JS

def test_live_message_window_is_bounded_and_count_is_preserved():
    js=(ROOT/'frontend/app.js').read_text(encoding='utf-8')
    assert 'function trimMessages()' in js
    assert "state.messages=state.messages.slice(-200)" in js
    assert 'state.messageCount+=1' in js


def test_right_detail_tabs_have_accessible_state_and_keyboard_focus():
    html=(ROOT/'frontend/index.html').read_text(encoding='utf-8')
    css=(ROOT/'frontend/app.css').read_text(encoding='utf-8')
    js=(ROOT/'frontend/app.js').read_text(encoding='utf-8')
    assert 'aria-controls="panel-progress"' in html and 'role="tabpanel"' in html
    assert "x.setAttribute('aria-selected',String(active))" in js
    assert 'button:focus-visible' in css
    assert 'prefers-reduced-motion:reduce' in css


def test_task_summary_reflects_business_outcome_not_only_busy_state():
    assert "runtime?.status==='needs_evidence'" in JS
    assert "label:'待补资料'" in JS
    assert "if(pending)return{label:'待确认'" in JS
    assert "runtime?.status==='completed'" in JS


def test_detail_tabs_support_arrow_key_navigation():
    assert "['ArrowLeft','ArrowRight','Home','End']" in JS
    assert "tabs[i].focus()" in JS


def test_rapid_new_task_scene_change_reuses_inflight_empty_task():
    assert 'createRequestedScene:null' in JS
    assert 'state.createRequestedScene=scene' in JS
    assert "if(c.scene!==desired)c=await api(`/api/conversations/${c.id}`" in JS


def test_customer_ui_does_not_ship_placeholder_more_action():
    assert 'id="moreBtn"' not in HTML
    assert "$('moreBtn')" not in JS


def test_customer_surface_hides_internal_algorithm_terms_more_strictly():
    surface=(HTML+'\n'+CSS).lower()
    for word in ['agent harness','belief state','adaptive planner','recursive agent','verifier','rollback','failure-driven','event sourcing']:
        assert word not in surface


def test_incomplete_task_does_not_present_large_100_percent_as_completion():
    assert "outcome.label==='待补资料'" in JS
    assert "display='待补'" in JS
    assert "display='重试'" in JS
    assert '.status-ring.word span' in CSS


def test_mobile_composer_uses_one_clear_attachment_entry():
    assert 'mobile-attach' in HTML and '添加资料' in HTML
    assert '.attach-row>.attach:not(.mobile-attach){display:none}' in CSS


def test_provider_dialog_restores_focus_to_explicit_trigger():
    assert "openProviderModal(returnTarget=document.activeElement)" in JS
    assert "openProviderModal($('providerBtn'))" in JS
    assert "openProviderModal($('settingsBtn'))" in JS


def test_workspace_rows_do_not_shift_when_history_notice_is_hidden():
    assert '.task-head{grid-row:1;' in CSS
    assert '.history-notice{grid-row:2;' in CSS
    assert '.chat-scroll{grid-row:3;' in CSS
    assert '.composer-zone{grid-row:4;' in CSS


def test_media_preview_failure_has_product_fallback_instead_of_broken_image():
    assert 'function bindPreviewFallbacks' in JS
    assert 'data-fallback' in JS
    assert 'evidence-fallback' in CSS


def test_multiline_user_instruction_keeps_its_structure():
    assert '.msg.user .msg-content' in CSS and 'white-space:pre-wrap' in CSS


def test_small_metadata_uses_readable_neutral_not_old_low_contrast_gray():
    assert '--muted-2:#697586' in CSS
    for old in ['#98a2b3','#9aa3b0','#a0a8b5','#9aa3af']:
        assert old not in CSS


def test_command_dialog_restores_focus_and_traps_tab_navigation():
    assert 'let commandReturnFocus=null' in JS
    assert "openCommand($('commandBtn'))" in JS
    assert "$('commandModal').onkeydown" in JS
    assert "document.activeElement===last" in JS


def test_customer_visible_frontend_has_no_algorithm_vocabulary():
    surface=HTML+'\n'+JS
    for pattern in [r'\bharness\b',r'\bplanner\b',r'\bverifier\b',r'\bbelief\b',r'\brollback\b',r'\bmcp\b',r'\bagent\b']:
        assert not re.search(pattern,surface,re.I)
    for word in ['算法','模型','策略先验','回滚','递归子']:
        assert word not in surface
