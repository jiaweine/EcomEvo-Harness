(() => {
  'use strict';

  document.documentElement.dataset.ecomevoTheme = 'customer-service';
  const themeColor = document.querySelector('meta[name="theme-color"]');
  if (themeColor) themeColor.setAttribute('content', '#ffffff');

  // Keep the previous structural layer for layout compatibility. The customer
  // service theme is loaded later and owns all customer-facing visual decisions.
  const theme = document.createElement('link');
  theme.rel = 'stylesheet';
  theme.href = '/assets/carbon-theme.css';
  theme.dataset.ecomevoThemeBase = 'legacy-structure';
  document.head.appendChild(theme);

  const modules = [
    '/assets/safety-guards.js',
    '/assets/privacy-sanitize.js',
    '/assets/drawer-a11y.js',
    '/assets/enhancements-core.js',
    '/assets/ops-intelligence.js',
    '/assets/plugin-control.js',
    '/assets/realtime-reconcile.js',
    '/assets/customer-language.js',
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
        toast.textContent = '界面模块加载失败，请刷新页面';
        toast.classList.add('show');
      }
    }, { once: true });
    document.head.appendChild(script);
  }

  if (document.readyState === 'loading') {
    // Safety/privacy/a11y hooks install first. Customer-language.js is the final
    // presentation guard and never changes backend state, permissions or actions.
    document.write(modules.map(src => `<script src="${src}"><\/script>`).join(''));
  } else {
    fallbackLoad();
  }
})();