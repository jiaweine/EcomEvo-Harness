(() => {
  'use strict';

  const UpstreamFetch = window.fetch.bind(window);
  const blockedActionIds = new Set();
  let actionSyncScheduled = false;

  function requestUrl(input) {
    return typeof input === 'string' ? input : (input && input.url) || '';
  }

  function requestMethod(options) {
    return String(options?.method || 'GET').toUpperCase();
  }

  function actionDecisionId(input, options) {
    if (requestMethod(options) !== 'POST') return '';
    try {
      const parsed = new URL(requestUrl(input), location.href);
      const match = parsed.pathname.match(/\/api\/actions\/([^/]+)\/decision$/);
      return match ? decodeURIComponent(match[1]) : '';
    } catch (_) {
      return '';
    }
  }

  function taskBusy() {
    const chip = document.getElementById('taskReadyChip');
    const send = document.getElementById('sendBtn');
    return Boolean(chip?.classList.contains('busy') || send?.dataset.remoteLocked === '1');
  }

  function isFileDrag(event) {
    return Boolean(event.dataTransfer?.types?.includes?.('Files') || event.dataTransfer?.files?.length > 0);
  }

  function hideDropMask() {
    const mask = document.getElementById('dropMask');
    if (mask) mask.hidden = true;
  }

  function toast(message) {
    const node = document.getElementById('toast');
    if (!node) return;
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => node.classList.remove('show'), 3200);
  }

  function setTaskAttention(kind) {
    const chip = document.getElementById('taskReadyChip');
    const badge = document.getElementById('actionBadge');
    if (!chip) return;
    chip.classList.remove('busy', 'ready', 'attention', 'complete');
    if (kind === 'uncertain') {
      chip.classList.add('attention');
      chip.innerHTML = '<i></i><b>执行待核对</b>';
      if (badge) { badge.hidden = false; badge.textContent = '!'; badge.title = '有执行结果待核对'; }
    } else if (kind === 'approved') {
      chip.classList.add('busy');
      chip.innerHTML = '<i></i><b>执行中</b>';
      if (badge) { badge.hidden = false; badge.textContent = '…'; badge.title = '业务操作正在执行'; }
    }
  }

  function syncActionSafetyState() {
    actionSyncScheduled = false;
    const list = document.getElementById('actionList');
    if (!list) return;

    const uncertain = list.querySelector('.action-status.uncertain');
    const approved = list.querySelector('.action-status.approved');
    if (uncertain) setTaskAttention('uncertain');
    else if (approved) setTaskAttention('approved');

    const visibleActionIds = new Set([...list.querySelectorAll('[data-action]')].map(node => node.dataset.action));
    for (const id of [...blockedActionIds]) {
      if (!visibleActionIds.has(id)) blockedActionIds.delete(id);
    }
  }

  function scheduleActionSafetySync() {
    if (actionSyncScheduled) return;
    actionSyncScheduled = true;
    requestAnimationFrame(syncActionSafetyState);
  }

  window.fetch = async (...args) => {
    const actionId = actionDecisionId(args[0], args[1]);
    try {
      return await UpstreamFetch(...args);
    } catch (error) {
      if (!actionId) throw error;
      blockedActionIds.add(actionId);
      scheduleActionSafetySync();
      const uncertain = new Error('业务操作响应中断，实际状态待核对，请勿重复确认');
      uncertain.status = 502;
      uncertain.cause = error;
      throw uncertain;
    }
  };

  document.addEventListener('click', event => {
    const action = event.target?.closest?.('[data-action]');
    if (action && blockedActionIds.has(action.dataset.action)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      toast('该操作刚发生响应中断，请先核对业务系统状态，不要重复确认');
      return;
    }

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

  for (const type of ['dragenter', 'dragover']) {
    window.addEventListener(type, event => {
      if (!taskBusy() || !isFileDrag(event)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      hideDropMask();
    }, true);
  }

  window.addEventListener('drop', event => {
    if (!taskBusy() || !isFileDrag(event)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    hideDropMask();
    toast('当前任务正在处理中，本轮结束后再追加资料');
  }, true);

  document.addEventListener('DOMContentLoaded', () => {
    const list = document.getElementById('actionList');
    if (list) new MutationObserver(scheduleActionSafetySync).observe(list, { childList: true, subtree: true });
    scheduleActionSafetySync();
  });
})();