(() => {
  'use strict';

  const drawerIds = ['leftbar', 'rightbar'];
  const returnFocus = new Map();
  const leftDrawerMedia = matchMedia('(max-width:820px)');
  // workbench-v5 promotes the right rail to persistent context at 1380px.
  // Keep modal semantics/focus trapping for every width below that threshold.
  const rightDrawerMedia = matchMedia('(max-width:1379px)');
  let activeDrawer = null;

  function installMobileStylesheet() {
    if (document.querySelector('link[data-ecomevo-mobile-shell]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/mobile-shell.css';
    link.media = '(max-width:820px)';
    link.dataset.ecomevoMobileShell = '1';
    document.head.appendChild(link);
  }

  function syncViewport() {
    const viewport = window.visualViewport;
    const height = Math.max(320, Math.round(viewport?.height || window.innerHeight || document.documentElement.clientHeight || 0));
    const layoutHeight = Math.max(height, Math.round(window.innerHeight || height));
    const keyboardOffset = viewport
      ? Math.max(0, Math.round(layoutHeight - viewport.height - viewport.offsetTop))
      : 0;
    document.documentElement.style.setProperty('--app-viewport-height', `${height}px`);
    document.documentElement.style.setProperty('--keyboard-offset', `${keyboardOffset}px`);
    document.documentElement.classList.toggle('keyboard-visible', keyboardOffset > 80);
  }

  function drawerMode(drawer) {
    if (!drawer) return false;
    return drawer.id === 'leftbar' ? leftDrawerMedia.matches : rightDrawerMedia.matches;
  }

  function focusables(drawer) {
    return [...drawer.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])')]
      .filter(node => !node.hidden && node.getAttribute('aria-hidden') !== 'true' && node.getClientRects().length > 0);
  }

  function triggerFor(id) {
    return document.getElementById(id === 'leftbar' ? 'navToggle' : 'detailToggle');
  }

  function firstFocus(drawer) {
    const rows = focusables(drawer);
    const preferred = drawer.id === 'rightbar'
      ? drawer.querySelector('#rightClose,.right-tab:not(:disabled)')
      : drawer.querySelector('#newTaskBtn,.scene:not(:disabled)');
    return (preferred && preferred.getClientRects().length > 0 ? preferred : null) || rows[0] || drawer;
  }

  function syncBodyState() {
    const locked = Boolean(activeDrawer && drawerMode(activeDrawer));
    document.documentElement.classList.toggle('drawer-active', locked);
    document.body?.classList.toggle('drawer-active', locked);
  }

  function deactivate(drawer, restore = true) {
    if (activeDrawer !== drawer) return;
    activeDrawer = null;
    drawer.removeAttribute('aria-modal');
    if (drawer.getAttribute('role') === 'dialog') drawer.removeAttribute('role');
    syncBodyState();
    const target = returnFocus.get(drawer.id) || triggerFor(drawer.id);
    returnFocus.delete(drawer.id);
    if (restore && target && document.contains(target)) requestAnimationFrame(() => target.focus());
  }

  function activate(drawer) {
    if (!drawerMode(drawer) || activeDrawer === drawer) return;
    if (activeDrawer && activeDrawer !== drawer) deactivate(activeDrawer, false);
    activeDrawer = drawer;
    const trigger = triggerFor(drawer.id);
    const current = document.activeElement;
    const currentInsideClosedDrawer = drawerIds.some(id => {
      const other = document.getElementById(id);
      return other && other !== drawer && other.contains(current) && !other.classList.contains('open');
    });
    returnFocus.set(drawer.id, !currentInsideClosedDrawer && current && current !== document.body ? current : trigger);
    drawer.setAttribute('aria-modal', 'true');
    if (!drawer.hasAttribute('role')) drawer.setAttribute('role', 'dialog');
    syncBodyState();
    requestAnimationFrame(() => firstFocus(drawer)?.focus?.());
  }

  function normalizeBreakpointState() {
    const left = document.getElementById('leftbar');
    const right = document.getElementById('rightbar');
    if (left && !leftDrawerMedia.matches) {
      left.classList.remove('open');
      document.getElementById('navToggle')?.setAttribute('aria-expanded', 'false');
    }
    if (right && !rightDrawerMedia.matches) {
      right.classList.remove('open');
      document.getElementById('detailToggle')?.setAttribute('aria-expanded', 'false');
    }
    const scrim = document.getElementById('drawerScrim');
    if (scrim && !left?.classList.contains('open') && !right?.classList.contains('open')) scrim.hidden = true;
  }

  function sync() {
    normalizeBreakpointState();
    const open = drawerIds
      .map(id => document.getElementById(id))
      .find(node => node?.classList.contains('open') && drawerMode(node)) || null;
    if (open) activate(open);
    else if (activeDrawer) deactivate(activeDrawer, true);
    else syncBodyState();
  }

  document.addEventListener('keydown', event => {
    const drawer = activeDrawer;
    if (!drawer || !drawerMode(drawer)) return;
    if (event.key === 'Escape') {
      triggerFor(drawer.id)?.click?.();
      return;
    }
    if (event.key !== 'Tab') return;
    const rows = focusables(drawer);
    if (!rows.length) {
      event.preventDefault();
      drawer.focus?.();
      return;
    }
    const first = rows[0];
    const last = rows.at(-1);
    if (event.shiftKey && (document.activeElement === first || !drawer.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !drawer.contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
    }
  }, true);

  document.addEventListener('focusin', event => {
    const drawer = activeDrawer;
    if (!drawer || !drawerMode(drawer) || drawer.contains(event.target)) return;
    firstFocus(drawer)?.focus?.();
  }, true);

  installMobileStylesheet();
  syncViewport();
  window.visualViewport?.addEventListener?.('resize', syncViewport, { passive: true });
  window.visualViewport?.addEventListener?.('scroll', syncViewport, { passive: true });
  window.addEventListener('resize', syncViewport, { passive: true });
  window.addEventListener('orientationchange', syncViewport, { passive: true });

  document.addEventListener('DOMContentLoaded', () => {
    const observer = new MutationObserver(sync);
    for (const id of drawerIds) {
      const drawer = document.getElementById(id);
      if (drawer) observer.observe(drawer, { attributes: true, attributeFilter: ['class'] });
    }
    leftDrawerMedia.addEventListener?.('change', sync);
    rightDrawerMedia.addEventListener?.('change', sync);
    syncViewport();
    sync();
  });
})();