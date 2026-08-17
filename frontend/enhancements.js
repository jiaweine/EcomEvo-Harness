(() => {
  'use strict';

  const nativeFetch = window.fetch.bind(window);
  let lastActionResult = null;

  function actionUrl(input) {
    const value = typeof input === 'string' ? input : (input && input.url) || '';
    return /\/api\/actions\/[^/]+\/decision(?:\?|$)/.test(value);
  }

  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    if (actionUrl(args[0]) && response.ok) {
      try {
        const payload = await response.clone().json();
        if (payload && typeof payload === 'object') lastActionResult = payload;
      } catch (_) {}
    }
    return response;
  };

  function genericizeProviders() {
    const select = document.getElementById('providerSelect');
    if (select) {
      let externalIndex = 0;
      [...select.options].forEach(option => {
        if (option.value === 'auto') {
          option.textContent = '自动编排';
          return;
        }
        if (option.value === 'demo') {
          option.textContent = '本地受控';
          return;
        }
        externalIndex += 1;
        const unavailable = /未配置/.test(option.textContent || '');
        option.textContent = `认知引擎 ${String(externalIndex).padStart(2, '0')}${unavailable ? ' · 未配置' : ''}`;
      });
    }

    const grid = document.getElementById('providerGrid');
    if (grid) {
      [...grid.querySelectorAll('.provider-card')].forEach((card, index) => {
        const logo = card.querySelector('.provider-logo');
        const title = card.querySelector('b');
        if (!title || !logo) return;
        if ((title.textContent || '').trim() === '本地受控') {
          logo.textContent = '本';
          return;
        }
        logo.textContent = String(index + 1).padStart(2, '0');
      });
    }
  }

  function genericizeAnswerFooters(root = document) {
    root.querySelectorAll?.('.answer-provider').forEach(node => {
      node.textContent = '受控运行时 · 已完成';
    });
  }

  function rewriteActionToast(node) {
    if (!node || node.textContent !== '操作已完成并留痕' || !lastActionResult) return;
    const status = lastActionResult.status;
    const messages = {
      executed: '操作已执行并留痕',
      approved: '已确认，正在等待业务系统返回结果',
      uncertain: '执行结果待核对，请先确认真实业务状态',
      failed: '执行失败，业务状态未确认变更',
      proposed: '操作仍在等待确认',
      rejected: '本次操作已取消',
    };
    node.textContent = messages[status] || '操作状态已更新';
    lastActionResult = null;
  }

  function installObservers() {
    const toast = document.getElementById('toast');
    if (toast) {
      new MutationObserver(() => rewriteActionToast(toast)).observe(toast, {
        childList: true,
        characterData: true,
        subtree: true,
      });
    }

    const providerHost = document.getElementById('providerModal');
    if (providerHost) {
      new MutationObserver(genericizeProviders).observe(providerHost, { childList: true, subtree: true });
    }

    const select = document.getElementById('providerSelect');
    if (select) {
      new MutationObserver(genericizeProviders).observe(select, { childList: true, subtree: true });
    }

    const messages = document.getElementById('messageList');
    if (messages) {
      new MutationObserver(() => genericizeAnswerFooters(messages)).observe(messages, { childList: true, subtree: true });
    }
  }

  window.addEventListener('unhandledrejection', event => {
    const message = String(event.reason?.message || event.reason || '');
    if (!/请求失败|Failed to fetch|NetworkError|任务不存在|服务连接/.test(message)) return;
    const toast = document.getElementById('toast');
    if (toast) {
      toast.textContent = '操作未完成，请检查连接后重试';
      toast.classList.add('show');
    }
  });

  document.addEventListener('DOMContentLoaded', () => {
    document.documentElement.dataset.ui = 'ecomevo';
    installObservers();
    genericizeProviders();
    genericizeAnswerFooters();
  });
})();
