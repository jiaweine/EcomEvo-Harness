from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend/app.js").read_text(encoding="utf-8")
ENH = (ROOT / "frontend/enhancements.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/product-polish.css").read_text(encoding="utf-8")


def test_runtime_pulse_mounts_into_existing_progress_panel():
    assert 'id="panel-progress"' in HTML
    assert "document.getElementById('panel-progress')" in ENH
    assert "document.getElementById('panel-trace')" not in ENH


def test_provider_metadata_is_generic_before_primary_app_renders_it():
    assert "function providerUrl(input)" in ENH
    assert "function genericProviderRows(rows)" in ENH
    assert "return new Response(JSON.stringify(payload)" in ENH
    assert "name: `认知引擎 ${String(externalIndex).padStart(2, '0')}`" in ENH


def test_left_scene_navigation_reuses_existing_quick_card_state_machine():
    assert "function installSceneBridge()" in ENH
    assert ".scene[data-scene]" in ENH
    assert ".quick-card[data-scene]" in ENH
    assert "bridge.click()" in ENH
    assert "bridge.dataset.scene = 'content_audit'" in ENH


def test_scene_bridge_keeps_mobile_drawer_behavior():
    assert "matchMedia('(max-width:820px)').matches" in ENH
    assert "document.getElementById('drawerScrim')?.click()" in ENH


def test_observer_avoids_character_data_churn():
    observer = ENH.split('function installObserver()', 1)[1].split('function installMotion()', 1)[0]
    assert 'childList: true' in observer
    assert 'characterData' not in observer


def test_handled_async_navigation_failure_does_not_become_noisy_unhandled_rejection():
    assert "window.addEventListener('unhandledrejection'" in ENH
    assert 'event.preventDefault()' in ENH
    assert 'resetUploadGuard()' in ENH


def test_empty_task_scene_switching_contract_still_exists_in_primary_app():
    assert "state.messages.length===0&&state.conversation.scene!==scene" in APP
    assert "method:'PATCH'" in APP


def test_upload_guard_starts_on_file_selection_and_finishes_per_upload_request():
    assert "document.addEventListener('change'" in ENH
    assert "event.target?.id === 'fileInput'" in ENH
    assert 'beginUploadBatch(event.target.files?.length || 0)' in ENH
    assert 'const isAssetUpload = assetUploadUrl(input, options)' in ENH
    assert 'if (isAssetUpload) finishUploadItem()' in ENH


def test_upload_guard_blocks_send_enter_command_enter_and_cross_task_navigation():
    assert "#sendBtn,.scene[data-scene],.conv-item,#newTaskBtn,.command-result" in ENH
    assert "event.target?.id === 'messageInput' && event.key === 'Enter'" in ENH
    assert "event.target?.id === 'commandInput' && event.key === 'Enter'" in ENH
    assert "event.stopImmediatePropagation()" in ENH
    assert '资料还在上传，完成后再发送' in ENH
    assert '资料还在上传，完成后再切换任务' in ENH


def test_upload_guard_exposes_busy_state_and_restores_send_control():
    assert "composer.setAttribute('aria-busy', String(uploading || remoteTurnBusy))" in ENH
    assert "send.dataset.uploadLocked = '1'" in ENH
    assert "send.textContent = uploadBatchPending > 1" in ENH
    assert 'send.disabled = sendDisabledBeforeUpload' in ENH
    assert '.composer.upload-busy' in CSS


def test_runtime_telemetry_restores_from_historical_conversation():
    assert 'function latestRuntimeFromConversation(payload)' in ENH
    assert 'function restoreConversationTelemetry(payload, expectedConversationId)' in ENH
    assert 'restoreConversationTelemetry(payload, cid)' in ENH
    assert "message?.payload?.runtime" in ENH


def test_runtime_telemetry_ignores_out_of_order_navigation_response():
    assert "new URLSearchParams(location.search).get('conversation')" in ENH
    assert 'currentConversationId !== expectedConversationId' in ENH
    assert 'setTimeout(() =>' in ENH


def test_new_turn_and_new_task_clear_previous_runtime_metrics():
    assert 'if (conversationCreateUrl(input, options) && response.ok)' in ENH
    assert "if (event.type === 'message.accepted')" in ENH
    assert "if (event.type === 'answer.error')" in ENH
    assert ENH.count('clearTurnTelemetry()') >= 3


def test_multi_tab_turn_event_forces_busy_until_terminal_event():
    accepted = ENH.split("if (event.type === 'message.accepted')", 1)[1].split("if (event.type === 'routing.policy.updated')", 1)[0]
    ready = ENH.split("if (event.type === 'answer.ready')", 1)[1].split("if (event.type === 'answer.error')", 1)[0]
    error = ENH.split("if (event.type === 'answer.error')", 1)[1].split('if (NativeWebSocket)', 1)[0]
    assert 'remoteTurnBusy = true' in accepted
    assert 'remoteTurnBusy = false' in ready
    assert 'remoteTurnBusy = false' in error
    assert "send.textContent = '任务处理中…'" in ENH
    assert "chip.classList.add('busy')" in ENH


def test_runtime_pulse_surfaces_structured_decision_state_not_chain_of_thought():
    for field in ('evidence_complete', 'missing_evidence', 'tool_cost_used', 'tool_cost_budget', 'tool_cost_remaining', 'stop_reason', 'autonomy_mode'):
        assert field in ENH
    assert '证据状态' in ENH
    assert '停止原因' in ENH
    assert '工具预算' in ENH
    assert '运行模式' in ENH
    assert 'chain-of-thought' not in ENH.lower()


def test_command_palette_scene_entries_reuse_scene_state_machine():
    assert 'function navSceneForCommandResult(result)' in ENH
    assert 'function runSceneCommand(result, event)' in ENH
    assert 'function installCommandSceneBridge()' in ENH
    assert "document.querySelector('#commandResults .command-result.active')" in ENH
    assert 'scene.click()' in ENH


def test_narrow_asset_library_opens_assets_without_toggling_open_drawer_closed():
    assert 'function installNarrowAssetDrawerFix()' in ENH
    assert "event.target?.closest?.('#assetLibraryBtn')" in ENH
    assert "document.getElementById('tab-assets')?.click()" in ENH
    assert "if (!rightbar?.classList.contains('open')) document.getElementById('detailToggle')?.click()" in ENH


def test_mobile_runtime_pulse_uses_two_column_six_cell_layout():
    assert '@media(max-width:620px)' in CSS
    assert '.runtime-pulse-grid{grid-template-columns:repeat(2,minmax(0,1fr))}' in CSS
    assert '.runtime-pulse-grid>div:nth-child(odd)' in CSS


def test_websocket_resume_cursor_is_scoped_per_conversation_and_bounded():
    assert 'const eventCutoffs = new Map()' in ENH
    assert 'function rememberEventCutoff(conversationId, eventId)' in ENH
    assert 'eventCutoffs.size > 100' in ENH
    assert 'rememberConversationEvents(payload, cid)' in ENH
    assert 'rememberEventCutoff(event.conversation_id, event.id)' in ENH


def test_websocket_reconnect_adds_after_id_only_when_cursor_exists():
    assert "parsed.pathname.match(/\\/ws\\/conversations\\/([^/]+)$/)" in ENH
    assert "parsed.searchParams.set('after_id', String(cutoff))" in ENH
    assert 'if (cutoff > 0)' in ENH
