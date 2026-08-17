from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOADER = (ROOT / 'frontend/enhancements.js').read_text(encoding='utf-8')
DRAWER = (ROOT / 'frontend/drawer-a11y.js').read_text(encoding='utf-8')


def test_drawer_guard_loads_before_main_core_hooks():
    assert '/assets/drawer-a11y.js' in LOADER
    assert LOADER.index('/assets/drawer-a11y.js') < LOADER.index('/assets/enhancements-core.js')


def test_mobile_drawer_traps_and_restores_keyboard_focus():
    assert "const drawerIds = ['leftbar', 'rightbar']" in DRAWER
    assert "event.key !== 'Tab'" in DRAWER
    assert "document.addEventListener('focusin'" in DRAWER
    assert "returnFocus.set(drawer.id" in DRAWER
    assert "target.focus()" in DRAWER
    assert "aria-modal" in DRAWER
    assert "matchMedia('(max-width:1080px)')" in DRAWER
