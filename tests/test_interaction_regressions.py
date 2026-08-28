from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend/app.js").read_text(encoding="utf-8")
LOADER = (ROOT / "frontend/enhancements.js").read_text(encoding="utf-8")
CORE = (ROOT / "frontend/enhancements-core.js").read_text(encoding="utf-8")
REALTIME = (ROOT / "frontend/realtime-reconcile.js").read_text(encoding="utf-8")
PLUGIN_CONTROL = (ROOT / "frontend/plugin-control.js").read_text(encoding="utf-8")
SAFETY = (ROOT / "frontend/safety-guards.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/product-polish.css").read_text(encoding="utf-8")
PLUGIN_CSS = (ROOT / "frontend/plugin-control.css").read_text(encoding="utf-8")


def test_enhancement_loader_runs_safety_before_upload_bookkeeping_and_realtime():
    assert "/assets/enhancements-core.js" in LOADER
    assert "/assets/realtime-reconcile.js" in LOADER
    assert "/assets/safety-guards.js" in LOADER
    assert LOADER.index("/assets/safety-guards.js") < LOADER.index("/assets/enhancements-core.js") < LOADER.index("/assets/plugin-control.js") < LOADER.index("/assets/realtime-reconcile.js")
    for name in ("enhancements-core.js", "plugin-control.js", "realtime-reconcile.js", "safety-guards.js"):
        assert (ROOT / "frontend" / name).is_file()
    assert 'document.readyState === \'loading\'' in LOADER


def test_runtime_pulse_mounts_into_existing_progress_panel():
    assert 'id="panel-progress"' in HTML
    assert "document.getElementById('panel-progress')" in CORE
    assert "document.getElementById('panel-trace')" not in CORE


def test_plugin_control_plane_is_read_only_live_runtime_observability():
    assert 'id="runtimeModal"' in HTML and 'aria-modal="true"' in HTML
    assert 'id="runtimePluginGrid"' in HTML and 'id="runtimeLanes"' in HTML
    assert "fetch('/api/runtime'" in PLUGIN_CONTROL
    for field in ('contract_valid', 'contract_missing', 'generation', 'api_version', 'source'):
        assert field in PLUGIN_CONTROL
    assert 'plugin_start' not in PLUGIN_CONTROL and 'replace_plugin' not in PLUGIN_CONTROL
    assert '系统状态' in HTML and '重要操作仍需您的确认' in HTML
    assert '控制面只读' not in HTML and '部署管理员' not in HTML


def test_plugin_control_plane_escapes_api_content_and_has_modal_focus_contract():
    assert "const esc = value =>" in PLUGIN_CONTROL
    assert 'esc(plugin.name || plugin.key)' in PLUGIN_CONTROL
    assert 'modal.onkeydown = trapFocus' in PLUGIN_CONTROL
    assert "event.key === 'Escape'" in PLUGIN_CONTROL
    assert 'returnFocus = document.activeElement' in PLUGIN_CONTROL
    assert 'target.focus()' in PLUGIN_CONTROL
    assert '@media(max-width:760px)' in PLUGIN_CSS


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
    assert "uploadInFlight += files.length" in CORE
    assert "uploadInFlight = Math.max(0, uploadInFlight - 1)" in CORE


def test_upload_guard_defers_route_changes_and_replays_latest_scene():
    assert "if (uploadInFlight > 0)" in CORE
    assert "queuedScene = scene" in CORE
    assert "replayQueuedScene()" in CORE
    assert "等待当前资料上传完成后再切换业务" in CORE


def test_realtime_reconcile_has_sequence_and_inflight_guards():
    assert "lastSeqByConversation" in REALTIME
    assert "refreshInFlight" in REALTIME
    assert "refreshQueued" in REALTIME
    assert "event.seq <= lastSeq" in REALTIME
    assert "payload.conversation_id !== state.conversation?.id" in REALTIME


def test_safety_guards_block_cross_task_send_and_upload_during_unsettled_navigation():
    assert "navigationPending" in SAFETY
    assert "navigationSettled" in SAFETY
    assert "sendBtn" in SAFETY
    assert "fileInput" in SAFETY
    assert "业务切换尚未完成" in SAFETY
