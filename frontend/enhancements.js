(() => {
  'use strict';

  document.documentElement.dataset.ecomevoTheme = 'customer-service';
  const themeColor = document.querySelector('meta[name="theme-color"]');
  if (themeColor) themeColor.setAttribute('content', '#f5f1ec');

  // Keep the proven Carbon layer as structural fallback; customer-copy.js adds
  // the final customer-facing theme after all existing enhancement modules.
  const theme = document.createElement('link');
  theme.rel = 'stylesheet';
  theme.href = '/assets/carbon-theme.css';
  theme.dataset.ecomevoThemeBase = 'carbon-operations';
  document.head.appendChild(theme);

  const modules = [
    '/assets/safety-guards.js',
    '/assets/privacy-sanitize.js',
    '/assets/drawer-a11y.js',
    '/assets/enhancements-core.js',
    '/assets/ops-intelligence.js',
    '/assets/plugin-control.js',
    '/assets/customer-copy.js',
    '/assets/customer-dynamic-copy.js',
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
    // Safety/privacy/a11y hooks install first. Customer copy is deliberately a
    // presentation-only layer loaded after operational UI modules, so backend
    // contracts and runtime behavior remain unchanged.
    document.write(modules.map(src => `<script src="${src}"><\/script>`).join(''));
  } else {
    fallbackLoad();
  }
})();
