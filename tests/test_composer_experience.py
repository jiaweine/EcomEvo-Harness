from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / 'frontend/provider-marketplace.js').read_text(encoding='utf-8')
CSS = (ROOT / 'frontend/provider-marketplace.css').read_text(encoding='utf-8')
OPS = (ROOT / 'frontend/ops-intelligence.css').read_text(encoding='utf-8')


def test_composer_has_first_class_voice_input_with_audio_fallback():
    for needle in (
        'id = \'voiceInputBtn\'',
        'window.SpeechRecognition || window.webkitSpeechRecognition',
        'navigator.mediaDevices?.getUserMedia',
        "typeof MediaRecorder === 'undefined'",
        'new MediaRecorder',
        'voiceRecorder.start(250)',
    ):
        assert needle in JS


def test_multimodal_intake_supports_menu_drag_and_clipboard_files():
    for needle in (
        'id="attachMenuBtn"',
        'data-accept="image/*"',
        'data-accept="video/*"',
        'data-accept="audio/*"',
        '.pdf,.docx,.xlsx,.xlsm,.csv',
        "event.clipboardData?.items",
        "item.kind === 'file'",
        "composer.classList.add('is-drop-target')",
        "event.dataTransfer?.files?.length",
    ):
        assert needle in JS


def test_pending_context_moves_inside_composer_and_attachment_only_send_is_valid():
    assert 'composer.prepend(pending)' in JS
    assert 'pendingAssetCount()' in JS
    assert '请分析我刚刚添加的资料，提取关键信息，并告诉我下一步建议。' in JS
    assert "sendButton.addEventListener('click', prepareAttachmentOnlySend, true)" in JS


def test_composer_visual_hierarchy_matches_modern_chat_products():
    for needle in (
        '.attachment-menu{',
        '.voice-input-btn.listening',
        '.composer.is-drop-target::after',
        '.composer>.pending-assets',
        'border-radius:25px!important',
        'min-height:66px!important',
    ):
        assert needle in CSS


def test_new_task_composer_is_lower_than_previous_upper_third_layout():
    assert 'padding:clamp(126px,19vh,228px) 0 22px!important' in OPS
    assert 'the lower-middle focal zone' in OPS


def test_mobile_and_reduced_motion_details_are_explicit():
    assert '@media(max-width:820px)' in CSS
    assert 'bottom:calc(78px + env(safe-area-inset-bottom))' in CSS
    assert '@media(prefers-reduced-motion:reduce)' in CSS
    assert 'animation:none!important' in CSS
