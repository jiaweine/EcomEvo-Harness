(() => {
  'use strict';

  document.documentElement.dataset.ecomevoTheme = 'customer-service';

  // The final customer-facing CSS stack is declared statically in index.html so
  // the first paint does not depend on JavaScript. This loader owns behavior
  // modules only and keeps their proven execution order intact.
  const modules = [
    '/assets/safety-guards.js',
    '/assets/privacy-sanitize.js',
    '/assets/drawer-a11y.js',
    '/assets/enhancements-core.js',
    '/assets/ops-intelligence.js',
    '/assets/plugin-control.js',
    '/assets/customer-copy.js',
    '/assets/customer-dynamic-copy.js',
    '/assets/customer-polish.js',
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
    // Safety/privacy/a11y hooks install first. Customer-facing translation and
    // polish run after operational UI modules, so algorithms, API contracts,
    // permissions and runtime behavior remain unchanged.
    document.write(modules.map(src => `<script src="${src}"><\/script>`).join(''));
  } else {
    fallbackLoad();
  }
})();
