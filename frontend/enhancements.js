(() => {
  'use strict';

  const modules = [
    '/assets/safety-guards.js',
    '/assets/privacy-sanitize.js',
    '/assets/drawer-a11y.js',
    '/assets/enhancements-core.js',
    '/assets/realtime-reconcile.js',
  ];

  function fallbackLoad(index = 0) {
    if (index >= modules.length) return;
    const script = document.createElement('script');
    script.src = modules[index];
    script.async = false;
    script.addEventListener('load', () => fallbackLoad(index + 1), { once: true });
    script.addEventListener('error', () => {
      const toast = document.getElementById('toast');
      if (toast) {
        toast.textContent = '界面增强模块加载失败，请刷新页面';
        toast.classList.add('show');
      }
    }, { once: true });
    document.head.appendChild(script);
  }

  if (document.readyState === 'loading') {
    // Parser-blocking loader: safety/privacy/a11y hooks install before the main module can render,
    // then core telemetry/fetch guards and cross-tab reconciliation are layered on top.
    document.write(modules.map(src => `<script src="${src}"><\/script>`).join(''));
  } else {
    fallbackLoad();
  }
})();