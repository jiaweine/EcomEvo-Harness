from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend/app.js").read_text(encoding="utf-8")
LOADER = (ROOT / "frontend/enhancements.js").read_text(encoding="utf-8")
CORE = (ROOT / "frontend/enhancements-core.js").read_text(encoding="utf-8")
REALTIME = (ROOT / "frontend/realtime-reconcile.js").read_text(encoding="utf-8")
SAFETY = (ROOT / "frontend/safety-guards.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/product-polish.css").read_text(encoding="utf-8")


def test_enhancement_loader_runs_safety_before_upload_bookkeeping_and_realtime():
    assert "/assets/enhancements-core.js" in LOADER
    assert "/assets/realtime-reconcile.js" in LOADER
    assert "/assets/safety-guards.js" in LOADER
    assert LOADER.index("/assets/safety-guards.js") < LOADER.index("/assets/enhancements-core.js") < LOADER.index("/assets/realtime-reconcile.js")
    for name in ("enhancements-core.js", "realtime-reconcile.js", "safety-guards.js"):
        assert (ROOT / "frontend" / name).is_file()
    assert 'document.readyState === \'loading\'' in LOADER


def test_runtime_pulse_mounts_into_existing_progress_panel():
    assert 'id="panel-progress"' in HTML
    assert "document.getElementById('panel-progress')" in CORE
    assert "document.getElementById('panel-trace')" not in CORE


def test_provider_metadata_is_generic_before_primary_app_renders_it():
    assert "function providerUrl(input)" in CORE
    assert "function genericProviderRows(rows)" in CORE
    assert "return new Response(JSON.stringify(payload)" in CORE
    assert "name: `认知引擎 ${String(externalIndex).padStart(2, '0')}`" in CORE


def test_left_scene_navigation_reuses_existing_quick_card_state_machine():
    assert "function installSceneBridge()" in CORE
    assert ".scene[data-scene]" in CORE
    assert ".quick-card[data-scene]" in CORE
    assert "bridge.click()" in CORE
    assert "bridge.dataset.scene = 'content_audit'" in CORE


def test_scene_bridge_keeps_mobile_drawer_behavior():
    assert "matchMedia('(max-width:820px)').matches" in CORE
    assert "document.getElementById('drawerScrim')?.click()" in CORE


def test_observer_avoids_character_data_churn():
    observer = CORE.split('function installObserver()', 1)[1].split('function installMotion()', 1)[0]
    assert 'childList: true' in observer
    assert 'characterData' not in observer


def test_handled_async_navigation_failure_does_not_become_noisy_unhandled_rejection():
    assert "window.addEventListener('unhandledrejection'" in CORE
    assert 'event.preventDefault()' in CORE
    assert 'resetUploadGuard()' in CORE


def test_empty_task_scene_switching_contract_still_exists_in_primary_app():
    assert "state.messages.length===0&&state.conversation.scene!==scene" in APP
    assert "method:'PATCH'" in APP


def test_upload_guard_starts_on_file_selection_and_finishes_per_upload_request():
    assert "document.addEventListener('change'" in CORE
    assert "event.target?.id === 'fileInput'" in CORE
    assert 'beginUploadBatch(event.target.files?.length || 0)' in CORE
    assert 'const isAssetUpload = assetUploadUrl(input, options)' in CORE
    assert 'if (isAssetUpload) finishUploadItem()' in CORE


def test_upload_guard_blocks_send_enter_command_enter_and_cross_task_navigation():
    assert "#sendBtn,.scene[data-scene],.conv-item,#newTaskBtn,.command-result" in CORE
    assert "event.target?.id === 'messageInput' && event.key === 'Enter'" in CORE
    assert "event.target?.id === 'commandInput' && event.key === 'Enter'" in CORE
    assert "event.stopImmediatePropagation()" in CORE
    assert '资料还在上传，完成后再发送' in CORE
    assert '资料还在上传，完成后再切换任务' in CORE


def test_upload_guard_exposes_busy_state_and_restores_send_control():
    assert "composer.setAttribute('aria-busy', String(uploading || remoteTurnBusy))" in CORE
    assert "send.dataset.uploadLocked = '1'" in CORE
    assert "send.textContent = uploadBatchPending > 1" in CORE
    assert 'send.disabled = sendDisabledBeforeUpload' in CORE
    assert '.composer.upload-busy' in CSS


def test_runtime_telemetry_restores_from_historical_conversation():
    assert 'function latestRuntimeFromConversation(payload)' in CORE
    assert 'function restoreConversationTelemetry(payload, expectedConversationId)' in CORE
    assert 'restoreConversationTelemetry(payload, cid)' in CORE
    assert "message?.payload?.runtime" in CORE


def test_runtime_telemetry_ignores_out_of_order_navigation_response():
    assert "new URLSearchParams(location.search).get('conversation')" in CORE
    assert 'currentConversationId !== expectedConversationId' in CORE
    assert 'setTimeout(() =>' in CORE


def test_new_turn_and_new_task_clear_previous_runtime_metrics():
    assert 'if (conversationCreateUrl(input, options) && response.ok)' in CORE
    assert "if (event.type === 'message.accepted')" in CORE
    assert "if (event.type === 'answer.error')" in CORE
    assert CORE.count('clearTurnTelemetry()') >= 3


def test_multi_tab_turn_event_forces_busy_until_terminal_event():
    accepted = CORE.split("if (event.type === 'message.accepted')", 1)[1].split("if (event.type === 'routing.policy.updated')", 1)[0]
    ready = CORE.split("if (event.type === 'answer.ready')", 1)[1].split("if (event.type === 'answer.error')", 1)[0]
    error = CORE.split("if (event.type === 'answer.error')", 1)[1].split('if (NativeWebSocket)', 1)[0]
    assert 'remoteTurnBusy = true' in accepted
    assert 'remoteTurnBusy = false' in ready
    assert 'remoteTurnBusy = false' in error
    assert "send.textContent = '任务处理中…'" in CORE
    assert "chip.classList.add('busy')" in CORE


def test_runtime_pulse_surfaces_structured_decision_state_not_chain_of_thought():
    for field in ('evidence_complete', 'missing_evidence', 'tool_cost_used', 'tool_cost_budget', 'tool_cost_remaining', 'stop_reason', 'autonomy_mode'):
        assert field in CORE
    assert '证据状态' in CORE
    assert '停止原因' in CORE
    assert '工具预算' in CORE
    assert '运行模式' in CORE
    assert 'chain-of-thought' not in CORE.lower()


def test_command_palette_scene_entries_reuse_scene_state_machine():
    assert 'function navSceneForCommandResult(result)' in CORE
    assert 'function runSceneCommand(result, event)' in CORE
    assert 'function installCommandSceneBridge()' in CORE
    assert "document.querySelector('#commandResults .command-result.active')" in CORE
    assert 'scene.click()' in CORE


def test_narrow_asset_library_opens_assets_without_toggling_open_drawer_closed():
    assert 'function installNarrowAssetDrawerFix()' in CORE
    assert "event.target?.closest?.('#assetLibraryBtn')" in CORE
    assert "document.getElementById('tab-assets')?.click()" in CORE
    assert "if (!rightbar?.classList.contains('open')) document.getElementById('detailToggle')?.click()" in CORE


def test_mobile_runtime_pulse_uses_two_column_six_cell_layout():
    assert '@media(max-width:620px)' in CSS
    assert '.runtime-pulse-grid{grid-template-columns:repeat(2,minmax(0,1fr))}' in CSS
    assert '.runtime-pulse-grid>div:nth-child(odd)' in CSS


def test_websocket_resume_cursor_is_scoped_per_conversation_and_bounded():
    assert 'const eventCutoffs = new Map()' in CORE
    assert 'function rememberEventCutoff(conversationId, eventId)' in CORE
    assert 'eventCutoffs.size > 100' in CORE
    assert 'rememberConversationEvents(payload, cid)' in CORE
    assert 'rememberEventCutoff(event.conversation_id, event.id)' in CORE


def test_websocket_reconnect_adds_after_id_only_when_cursor_exists():
    assert "parsed.pathname.match(/\\/ws\\/conversations\\/([^/]+)$/)" in CORE
    assert "parsed.searchParams.set('after_id', String(cutoff))" in CORE
    assert 'if (cutoff > 0)' in CORE


def test_realtime_reconcile_observes_message_posts_and_accepted_events():
    assert 'const UpstreamFetch = window.fetch.bind(window)' in REALTIME
    assert 'function messagePostConversationId(input, options)' in REALTIME
    assert "event.type !== 'message.accepted'" in REALTIME
    assert 'inflightTurns' in REALTIME
    assert 'scheduleCurrentTaskRefresh()' in REALTIME


def test_realtime_reconcile_uses_persisted_message_id_not_text_or_time_windows():
    assert 'lastRenderedUserText' not in REALTIME
    assert 'normalize(message?.content)' not in REALTIME
    assert 'recentLocalSuccess' not in REALTIME
    assert 'localAcceptedIds' in REALTIME
    assert 'acceptedIds' in REALTIME
    assert 'response.clone().json()' in REALTIME
    assert "payload?.message?.id" in REALTIME


def test_action_network_failure_is_uncertain_and_blocks_immediate_duplicate_confirmation():
    assert 'function actionDecisionId(input, options)' in SAFETY
    assert 'const blockedActionIds = new Set()' in SAFETY
    assert 'blockedActionIds.add(actionId)' in SAFETY
    assert "blockedActionIds.has(action.dataset.action)" in SAFETY
    assert '业务操作响应中断，实际状态待核对，请勿重复确认' in SAFETY
    assert 'uncertain.status = 502' in SAFETY
    assert 'retry' not in SAFETY.lower()


def test_approved_and_uncertain_actions_override_misleading_completed_chip():
    assert "function setTaskAttention(kind)" in SAFETY
    assert "chip.innerHTML = '<i></i><b>执行中</b>'" in SAFETY
    assert "chip.innerHTML = '<i></i><b>执行待核对</b>'" in SAFETY
    assert ".action-status.uncertain" in SAFETY
    assert ".action-status.approved" in SAFETY
    assert 'new MutationObserver(scheduleActionSafetySync)' in SAFETY


def test_busy_turn_blocks_attachment_and_drag_entry_points_before_upload():
    assert 'function taskBusy()' in SAFETY
    assert "event.target?.closest?.('.attach')" in SAFETY
    assert "event.target?.id !== 'fileInput'" in SAFETY
    assert "for (const type of ['dragenter', 'dragover'])" in SAFETY
    assert "window.addEventListener('drop'" in SAFETY
    assert '当前任务正在处理中，本轮结束后再追加资料' in SAFETY
