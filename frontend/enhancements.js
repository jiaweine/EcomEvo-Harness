(() => {
  'use strict';

  // Load the final refinement layer before the module application boots.
  if (!document.querySelector('link[data-ecomevo-polish]')) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/product-polish.css';
    link.dataset.ecomevoPolish = '1';
    document.head.appendChild(link);
  }

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

  function setText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function genericizeProviders() {
    const select = document.getElementById('providerSelect');
    if (select) {
      let externalIndex = 0;
      [...select.options].forEach(option => {
        if (option.value === 'auto') {
          setText(option, '自动编排');
          return;
        }
        if (option.value === 'demo') {
          setText(option, '本地受控');
          return;
        }
        externalIndex += 1;
        const unavailable = /未配置/.test(option.textContent || '');
        setText(option, `认知引擎 ${String(externalIndex).padStart(2, '0')}${unavailable ? ' · 未配置' : ''}`);
      });
    }

    const grid = document.getElementById('providerGrid');
    if (grid) {
      let externalIndex = 0;
      [...grid.querySelectorAll('.provider-card')].forEach(card => {
        const logo = card.querySelector('.provider-logo');
        const title = card.querySelector('b');
        if (!title || !logo) return;
        const titleText = (title.textContent || '').trim();
        if (/本地|演示/.test(titleText)) {
          setText(logo, '本');
          setText(title, '本地受控');
          return;
        }
        externalIndex += 1;
        setText(logo, String(externalIndex).padStart(2, '0'));
        setText(title, `认知引擎 ${String(externalIndex).padStart(2, '0')}`);
        const note = card.querySelector('p');
        setText(note, '按当前任务所需能力参与自动编排');
      });
    }
  }

  function genericizeAnswerFooters(root = document) {
    root.querySelectorAll?.('.answer-provider').forEach(node => {
      setText(node, '受控运行时 · 已完成');
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

  function promptCorrection(text) {
    const input = document.getElementById('messageInput');
    if (!input) return;
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus({ preventScroll: false });
    input.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }

  function installCorrectionActions(root = document) {
    root.querySelectorAll?.('.msg.assistant .answer-foot').forEach(foot => {
      if (foot.querySelector('.answer-correction')) return;
      const group = document.createElement('div');
      group.className = 'answer-correction';
      group.setAttribute('aria-label', '继续核对当前结论');
      const verify = document.createElement('button');
      verify.type = 'button';
      verify.textContent = '继续追证';
      verify.title = '继续核对当前结论中最薄弱的证据';
      verify.addEventListener('click', () => promptCorrection('请继续核对当前结论中最薄弱的证据，优先补充能够真正改变判断的信息，并明确还缺什么。'));
      const counter = document.createElement('button');
      counter.type = 'button';
      counter.textContent = '检查反证';
      counter.title = '主动寻找可能推翻当前结论的证据';
      counter.addEventListener('click', () => promptCorrection('请主动寻找可能推翻当前结论的反证、冲突证据或错误假设。只有证据足够时再维持原结论。'));
      group.append(verify, counter);
      foot.appendChild(group);
    });
  }

  function markCurrentProgress() {
    const rows = [...document.querySelectorAll('#progressList .progress-item')];
    rows.forEach(row => {
      row.classList.remove('current');
      row.removeAttribute('aria-current');
    });
    const current = [...rows].reverse().find(row => !row.classList.contains('done'));
    if (current) {
      current.classList.add('current');
      current.setAttribute('aria-current', 'step');
    }
  }

  function animateNewMessages(root) {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    root.querySelectorAll?.('.msg:not([data-motion-seen])').forEach(node => {
      node.dataset.motionSeen = '1';
      node.classList.add('ui-enter');
      node.addEventListener('animationend', () => node.classList.remove('ui-enter'), { once: true });
    });
  }

  function installMotion() {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    if (!reduced.matches) requestAnimationFrame(() => document.documentElement.classList.add('ui-motion-ready'));

    const messages = document.getElementById('messageList');
    if (messages) {
      animateNewMessages(messages);
      new MutationObserver(() => animateNewMessages(messages)).observe(messages, { childList: true, subtree: true });
    }

    const progress = document.getElementById('progressList');
    if (progress) {
      markCurrentProgress();
      new MutationObserver(markCurrentProgress).observe(progress, { childList: true, subtree: true });
    }

    reduced.addEventListener?.('change', event => {
      document.documentElement.classList.toggle('ui-motion-ready', !event.matches);
    });
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
      new MutationObserver(() => {
        genericizeAnswerFooters(messages);
        installCorrectionActions(messages);
      }).observe(messages, { childList: true, subtree: true });
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
    document.querySelector('.task-head')?.style.setProperty('position', 'relative');
    installObservers();
    installMotion();
    genericizeProviders();
    genericizeAnswerFooters();
    installCorrectionActions();
    markCurrentProgress();
  });
})();
