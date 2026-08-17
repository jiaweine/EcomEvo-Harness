(() => {
  'use strict';

  const UpstreamFetch = window.fetch.bind(window);

  function requestUrl(input) {
    return typeof input === 'string' ? input : (input && input.url) || '';
  }

  function requestMethod(options) {
    return String(options?.method || 'GET').toUpperCase();
  }

  function isActionDecision(input, options) {
    return requestMethod(options) === 'POST' && /\/api\/actions\/[^/]+\/decision(?:\?|$)/.test(requestUrl(input));
  }

  function taskBusy() {
    const chip = document.getElementById('taskReadyChip');
    const send = document.getElementById('sendBtn');
    return Boolean(chip?.classList.contains('busy') || send?.dataset.remoteLocked === '1');
  }

  function toast(message) {
    const node = document.getElementById('toast');
    if (!node) return;
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => node.classList.remove('show'), 3200);
  }

  window.fetch = async (...args) => {
    const actionDecision = isActionDecision(args[0], args[1]);
    try {
      return await UpstreamFetch(...args);
    } catch (error) {
      if (!actionDecision) throw error;
      const uncertain = new Error('业务操作响应中断，实际状态待核对，请勿重复确认');
      uncertain.status = 502;
      uncertain.cause = error;
      throw uncertain;
    }
  };

  document.addEventListener('click', event => {
    if (!taskBusy()) return;
    const trigger = event.target?.closest?.('.attach');
    if (!trigger) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    toast('当前任务正在处理中，本轮结束后再追加资料');
  }, true);

  document.addEventListener('change', event => {
    if (event.target?.id !== 'fileInput' || !taskBusy()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    event.target.value = '';
    toast('当前任务正在处理中，本轮结束后再追加资料');
  }, true);

  window.addEventListener('drop', event => {
    if (!taskBusy() || !(event.dataTransfer?.files?.length > 0)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const mask = document.getElementById('dropMask');
    if (mask) mask.hidden = true;
    toast('当前任务正在处理中，本轮结束后再追加资料');
  }, true);
})();