(() => {
  'use strict';

  function sanitize(root = document) {
    root.querySelectorAll?.('.answer-provider').forEach(node => {
      if (node.textContent !== '受控运行时 · 已完成') node.textContent = '受控运行时 · 已完成';
    });
  }

  function install() {
    sanitize();
    const root = document.querySelector('.app-shell') || document.body;
    if (!root) return;
    new MutationObserver(records => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (!(node instanceof Element)) continue;
          if (node.matches?.('.answer-provider')) {
            if (node.textContent !== '受控运行时 · 已完成') node.textContent = '受控运行时 · 已完成';
          } else {
            sanitize(node);
          }
        }
      }
    }).observe(root, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();
})();