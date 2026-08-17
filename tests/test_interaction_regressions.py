from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend/app.js").read_text(encoding="utf-8")
ENH = (ROOT / "frontend/enhancements.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")


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


def test_upload_guard_starts_before_primary_file_change_handler_and_finishes_per_request():
    assert "document.addEventListener('change'" in ENH
    assert "event.target?.id !== 'fileInput'" in ENH
    assert 'beginUploadBatch(event.target.files?.length || 0)' in ENH
    assert 'const isAssetUpload = assetUploadUrl(args[0], args[1])' in ENH
    assert 'if (isAssetUpload) finishUploadItem()' in ENH


def test_upload_guard_blocks_send_enter_and_cross_task_navigation():
    assert "#sendBtn,.scene[data-scene],.conv-item,#newTaskBtn,.command-result" in ENH
    assert "event.target?.id === 'messageInput' && event.key === 'Enter'" in ENH
    assert "event.stopImmediatePropagation()" in ENH
    assert '资料还在上传，完成后再发送' in ENH
    assert '资料还在上传，完成后再切换任务' in ENH


def test_upload_guard_exposes_busy_state_and_restores_send_control():
    assert "composer.setAttribute('aria-busy', String(active))" in ENH
    assert "send.dataset.uploadLocked = '1'" in ENH
    assert "send.textContent = uploadBatchPending > 1" in ENH
    assert 'send.disabled = sendDisabledBeforeUpload' in ENH
