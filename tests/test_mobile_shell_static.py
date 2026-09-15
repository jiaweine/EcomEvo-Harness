from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mobile_shell_has_keyboard_and_safe_area_guards():
    css = (ROOT / "frontend" / "mobile-shell.css").read_text(encoding="utf-8")
    assert "--app-viewport-height" in css
    assert "env(safe-area-inset-bottom)" in css
    assert "font-size: 16px !important" in css
    assert "translate3d(-103%, 0, 0)" in css
    assert "min-width: 44px !important" in css


def test_drawer_controller_loads_mobile_layer_and_tracks_visual_viewport():
    js = (ROOT / "frontend" / "drawer-a11y.js").read_text(encoding="utf-8")
    assert "/assets/mobile-shell.css" in js
    assert "window.visualViewport" in js
    assert "matchMedia('(max-width:820px)')" in js
    assert "matchMedia('(max-width:1379px)')" in js
    assert "drawer-active" in js


def test_mobile_shell_reduces_empty_state_and_provider_noise():
    css = (ROOT / "frontend" / "mobile-shell.css").read_text(encoding="utf-8")
    assert ".workspace:not(:has(#messageList .msg)) .task-head" in css
    assert ".agent-map" in css
    assert ".ai-provider-trigger" in css
    assert ".provider-control" in css
