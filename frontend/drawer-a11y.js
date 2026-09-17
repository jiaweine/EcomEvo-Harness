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

  function installTrustSurface() {
    if (!document.querySelector('link[data-ecomevo-trust-surface]')) {
      const style = document.createElement('link');
      style.rel = 'stylesheet';
      style.href = '/assets/trust-surface.css';
      style.dataset.ecomevoTrustSurface = '1';
      document.head.appendChild(style);
    }
    if (!document.querySelector('script[data-ecomevo-trust-surface]')) {
      const script = document.createElement('script');
      script.src = '/assets/trust-surface.js';
      script.dataset.ecomevoTrustSurface = '1';
      document.head.appendChild(script);
    }
  }

  function installInboxEntry() {
    if (document.getElementById('taskInboxLink')) return;
    const actions = document.querySelector('.top-actions');
    if (!actions) return;
    const link = document.createElement('a');
    link.id = 'taskInboxLink';
    link.className = 'top-action';
    link.href = '/api/inbox/ui';
    link.setAttribute('aria-label', '打开任务队列');
    const icon = document.createElement('span');
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = '▦';
    const label = document.createElement('em');
    label.textContent = '任务队列';
    link.append(icon, label);
    actions.insertBefore(link, actions.firstChild);
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
      // MutationObserver below watches drawer class changes. Only mutate the class
      // when state actually changes, otherwise a desktop sync can recursively
      // schedule itself forever before DOMContentLoaded finishes.
      if (left.classList.contains('open')) left.classList.remove('open');
      const navToggle = document.getElementById('navToggle');
      if (navToggle?.getAttribute('aria-expanded') !== 'false') navToggle?.setAttribute('aria-expanded', 'false');
    }
    if (right && !rightDrawerMedia.matches) {
      if (right.classList.contains('open')) right.classList.remove('open');
      const detailToggle = document.getElementById('detailToggle');
      if (detailToggle?.getAttribute('aria-expanded') !== 'false') detailToggle?.setAttribute('aria-expanded', 'false');
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
  installTrustSurface();
  syncViewport();
  window.visualViewport?.addEventListener?.('resize', syncViewport, { passive: true });
  window.visualViewport?.addEventListener?.('scroll', syncViewport, { passive: true });
  window.addEventListener('resize', syncViewport, { passive: true });
  window.addEventListener('orientationchange', syncViewport, { passive: true });

  document.addEventListener('DOMContentLoaded', () => {
    installInboxEntry();
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
