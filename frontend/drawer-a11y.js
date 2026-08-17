(() => {
  'use strict';

  const drawerIds = ['leftbar', 'rightbar'];
  const returnFocus = new Map();
  let activeDrawer = null;

  function narrow() {
    return matchMedia('(max-width:1080px)').matches;
  }

  function focusables(drawer) {
    return [...drawer.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])')]
      .filter(node => !node.hidden && node.getAttribute('aria-hidden') !== 'true');
  }

  function triggerFor(id) {
    return document.getElementById(id === 'leftbar' ? 'navToggle' : 'detailToggle');
  }

  function firstFocus(drawer) {
    const rows = focusables(drawer);
    const preferred = drawer.id === 'rightbar'
      ? drawer.querySelector('#rightClose,.right-tab:not(:disabled)')
      : drawer.querySelector('#newTaskBtn,.scene:not(:disabled)');
    return preferred || rows[0] || drawer;
  }

  function activate(drawer) {
    if (!narrow() || activeDrawer === drawer) return;
    activeDrawer = drawer;
    const current = document.activeElement;
    returnFocus.set(drawer.id, current && current !== document.body ? current : triggerFor(drawer.id));
    drawer.setAttribute('aria-modal', 'true');
    if (!drawer.hasAttribute('role')) drawer.setAttribute('role', 'dialog');
    requestAnimationFrame(() => firstFocus(drawer)?.focus?.());
  }

  function deactivate(drawer) {
    if (activeDrawer !== drawer) return;
    activeDrawer = null;
    drawer.removeAttribute('aria-modal');
    if (drawer.getAttribute('role') === 'dialog') drawer.removeAttribute('role');
    const target = returnFocus.get(drawer.id) || triggerFor(drawer.id);
    returnFocus.delete(drawer.id);
    if (target && document.contains(target)) requestAnimationFrame(() => target.focus());
  }

  function sync() {
    if (!narrow()) {
      if (activeDrawer) deactivate(activeDrawer);
      return;
    }
    const open = drawerIds.map(id => document.getElementById(id)).find(node => node?.classList.contains('open')) || null;
    if (open) activate(open);
    else if (activeDrawer) deactivate(activeDrawer);
  }

  document.addEventListener('keydown', event => {
    const drawer = activeDrawer;
    if (!drawer || event.key !== 'Tab') return;
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
    if (!drawer || drawer.contains(event.target)) return;
    firstFocus(drawer)?.focus?.();
  }, true);

  document.addEventListener('DOMContentLoaded', () => {
    const observer = new MutationObserver(sync);
    for (const id of drawerIds) {
      const drawer = document.getElementById(id);
      if (drawer) observer.observe(drawer, { attributes: true, attributeFilter: ['class'] });
    }
    matchMedia('(max-width:1080px)').addEventListener?.('change', sync);
    sync();
  });
})();