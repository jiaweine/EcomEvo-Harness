(() => {
  'use strict';

  if (!document.querySelector('link[data-ecomevo-polish]')) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/product-polish.css';
    link.dataset.ecomevoPolish = '1';
    document.head.appendChild(link);
  }

  const nativeFetch = window.fetch.bind(window);
  const NativeWebSocket = window.WebSocket;
  let lastActionResult = null;
  let uiScheduled = false;
  let pulseSignature = '';
  let uploadBatchPending = 0;
  let sendDisabledBeforeUpload = false;
  const telemetry = {
    apiEwmaMs: null,
    apiLastMs: null,
    runtime: null,
    routing: null,
    counterfactualMs: null,
    connected: false,
  };

  function requestUrl(input) {
    return typeof input === 'string' ? input : (input && input.url) || '';
  }

  function requestMethod(options) {
    return String(options?.method || 'GET').toUpperCase();
  }

  function actionUrl(input) {
    return /\/api\/actions\/[^/]+\/decision(?:\?|$)/.test(requestUrl(input));
  }

  function providerUrl(input) {
    return /\/api\/providers(?:\?|$)/.test(requestUrl(input));
  }

  function assetUploadUrl(input, options) {
    return /\/api\/assets(?:\?|$)/.test(requestUrl(input)) && requestMethod(options) === 'POST';
  }

  function conversationCreateUrl(input, options) {
    return /\/api\/conversations(?:\?|$)/.test(requestUrl(input)) && requestMethod(options) === 'POST';
  }

  function conversationDetailUrl(input, options) {
    return /\/api\/conversations\/[^/?]+(?:\?|$)/.test(requestUrl(input)) && requestMethod(options) === 'GET';
  }

  function showInteractionToast(message) {
    const node = document.getElementById('toast');
    if (!node) return;
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(showInteractionToast.timer);
    showInteractionToast.timer = setTimeout(() => node.classList.remove('show'), 2600);
  }

  function renderUploadGuard() {
    const active = uploadBatchPending > 0;
    const composer = document.getElementById('composer');
    const send = document.getElementById('sendBtn');
    composer?.classList.toggle('upload-busy', active);
    if (composer) composer.setAttribute('aria-busy', String(active));
    if (!send) return;
    if (active) {
      if (send.dataset.uploadLocked !== '1') {
        sendDisabledBeforeUpload = Boolean(send.disabled);
        send.dataset.uploadLabel = send.textContent || '发送';
      }
      send.dataset.uploadLocked = '1';
      send.disabled = true;
      send.textContent = uploadBatchPending > 1 ? `上传中 ${uploadBatchPending}` : '资料上传中…';
      return;
    }
    if (send.dataset.uploadLocked === '1') {
      send.disabled = sendDisabledBeforeUpload;
      send.textContent = send.dataset.uploadLabel || '发送';
      delete send.dataset.uploadLocked;
      delete send.dataset.uploadLabel;
    }
  }

  function beginUploadBatch(count) {
    const amount = Math.max(0, Number(count) || 0);
    if (!amount) return;
    uploadBatchPending += amount;
    renderUploadGuard();
  }

  function finishUploadItem() {
    if (uploadBatchPending > 0) uploadBatchPending -= 1;
    renderUploadGuard();
  }

  function resetUploadGuard() {
    uploadBatchPending = 0;
    renderUploadGuard();
  }

  function installUploadGuard() {
    document.addEventListener('change', event => {
      if (event.target?.id !== 'fileInput') return;
      beginUploadBatch(event.target.files?.length || 0);
    }, true);
    window.addEventListener('drop', event => {
      const count = event.dataTransfer?.files?.length || 0;
      if (count) beginUploadBatch(count);
    }, true);
    document.addEventListener('click', event => {
      if (!uploadBatchPending) return;
      const target = event.target?.closest?.('#sendBtn,.scene[data-scene],.conv-item,#newTaskBtn,.command-result');
      if (!target) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      showInteractionToast(target.id === 'sendBtn' ? '资料还在上传，完成后再发送' : '资料还在上传，完成后再切换任务');
    }, true);
    document.addEventListener('keydown', event => {
      if (!uploadBatchPending) return;
      const sendByEnter = event.target?.id === 'messageInput' && event.key === 'Enter' && !event.shiftKey && !event.isComposing;
      const newTaskShortcut = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'n';
      if (!sendByEnter && !newTaskShortcut) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      showInteractionToast(sendByEnter ? '资料还在上传，完成后再发送' : '资料还在上传，完成后再新建任务');
    }, true);
  }

  installUploadGuard();

  function genericProviderRows(rows) {
    if (!Array.isArray(rows)) return rows;
    let externalIndex = 0;
    return rows.map(row => {
      if (!row || typeof row !== 'object') return row;
      if (row.key === 'auto') return { ...row, name: '自动编排', vendor: '', note: '按任务能力与可用状态自动选择' };
      if (row.key === 'demo') return { ...row, name: '本地受控', vendor: '', note: '本地受控模式，不自动出站' };
      externalIndex += 1;
      return {
        ...row,
        name: `认知引擎 ${String(externalIndex).padStart(2, '0')}`,
        vendor: '',
        note: '按当前任务所需能力参与自动编排',
      };
    });
  }

  function clearTurnTelemetry({ keepRouting = false } = {}) {
    telemetry.runtime = null;
    telemetry.counterfactualMs = null;
    if (!keepRouting) telemetry.routing = null;
    pulseSignature = '';
    scheduleUiPass();
  }

  function latestRuntimeFromConversation(payload) {
    const messages = Array.isArray(payload?.messages) ? payload.messages : [];
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message?.role !== 'assistant') continue;
      const runtime = message?.payload?.runtime;
      if (runtime && typeof runtime === 'object') return runtime;
    }
    return null;
  }

  function restoreConversationTelemetry(payload) {
    telemetry.runtime = latestRuntimeFromConversation(payload);
    telemetry.routing = telemetry.runtime?.belief?.facts?.routing_policy || null;
    telemetry.counterfactualMs = null;
    pulseSignature = '';
    scheduleUiPass();
  }

  function updateApiLatency(ms) {
    telemetry.apiLastMs = ms;
    telemetry.apiEwmaMs = telemetry.apiEwmaMs == null ? ms : (0.82 * telemetry.apiEwmaMs + 0.18 * ms);
    scheduleUiPass();
  }

  window.fetch = async (...args) => {
    const started = performance.now();
    const input = args[0];
    const options = args[1];
    const isAssetUpload = assetUploadUrl(input, options);
    try {
      const response = await nativeFetch(...args);
      updateApiLatency(performance.now() - started);
      if (providerUrl(input) && response.ok) {
        try {
          const payload = genericProviderRows(await response.clone().json());
          const headers = new Headers(response.headers);
          headers.delete('content-length');
          return new Response(JSON.stringify(payload), {
            status: response.status,
            statusText: response.statusText,
            headers,
          });
        } catch (_) {}
      }
      if (conversationCreateUrl(input, options) && response.ok) {
        clearTurnTelemetry();
      } else if (conversationDetailUrl(input, options) && response.ok) {
        try { restoreConversationTelemetry(await response.clone().json()); } catch (_) {}
      }
      if (actionUrl(input) && response.ok) {
        try {
          const payload = await response.clone().json();
          if (payload && typeof payload === 'object') lastActionResult = payload;
        } catch (_) {}
      }
      return response;
    } catch (error) {
      updateApiLatency(performance.now() - started);
      throw error;
    } finally {
      if (isAssetUpload) finishUploadItem();
    }
  };

  function captureSocketEvent(raw) {
    let event;
    try { event = JSON.parse(raw); } catch (_) { return; }
    if (!event || typeof event !== 'object') return;
    if (event.type === 'message.accepted') {
      clearTurnTelemetry();
      return;
    }
    if (event.type === 'routing.policy.updated') {
      telemetry.routing = event.payload?.policy || event.payload || null;
      telemetry.counterfactualMs = Number(event.payload?.counterfactual_ms ?? null);
      scheduleUiPass();
      return;
    }
    if (event.type === 'answer.ready') {
      telemetry.runtime = event.payload?.result?.runtime || event.payload?.message?.payload?.runtime || null;
      telemetry.routing = telemetry.runtime?.belief?.facts?.routing_policy || telemetry.routing;
      scheduleUiPass();
      return;
    }
    if (event.type === 'answer.error') {
      clearTurnTelemetry({ keepRouting: false });
    }
  }

  if (NativeWebSocket) {
    function ObservedWebSocket(...args) {
      const socket = new NativeWebSocket(...args);
      socket.addEventListener('open', () => { telemetry.connected = true; scheduleUiPass(); });
      socket.addEventListener('close', () => { telemetry.connected = false; scheduleUiPass(); });
      socket.addEventListener('message', event => captureSocketEvent(event.data));
      return socket;
    }
    ObservedWebSocket.prototype = NativeWebSocket.prototype;
    Object.setPrototypeOf(ObservedWebSocket, NativeWebSocket);
    window.WebSocket = ObservedWebSocket;
  }

  function installSceneBridge() {
    const grid = document.querySelector('.quick-grid');
    if (grid && !grid.querySelector('.quick-card[data-scene="content_audit"]')) {
      const bridge = document.createElement('button');
      bridge.type = 'button';
      bridge.hidden = true;
      bridge.className = 'quick-card';
      bridge.dataset.scene = 'content_audit';
      bridge.dataset.prompt = '帮我核对当前图片、视频和文案与商品事实是否一致，找出需要补证或修改的高风险内容。';
      bridge.setAttribute('aria-hidden', 'true');
      bridge.tabIndex = -1;
      grid.appendChild(bridge);
    }

    document.addEventListener('click', event => {
      const scene = event.target?.closest?.('.scene[data-scene]');
      if (!scene) return;
      const sceneKey = scene.dataset.scene;
      const bridge = [...document.querySelectorAll('.quick-card[data-scene]')].find(node => node.dataset.scene === sceneKey);
      if (!bridge || typeof bridge.onclick !== 'function') return;
      event.preventDefault();
      event.stopImmediatePropagation();
      bridge.click();
      if (matchMedia('(max-width:820px)').matches) document.getElementById('drawerScrim')?.click();
    }, true);
  }

  function installNarrowAssetDrawerFix() {
    document.addEventListener('click', event => {
      const trigger = event.target?.closest?.('#assetLibraryBtn');
      if (!trigger || !matchMedia('(max-width:1080px)').matches) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      document.getElementById('tab-assets')?.click();
      const rightbar = document.getElementById('rightbar');
      if (!rightbar?.classList.contains('open')) document.getElementById('detailToggle')?.click();
    }, true);
  }

  installSceneBridge();
  installNarrowAssetDrawerFix();

  function setText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function genericizeProviders() {
    const select = document.getElementById('providerSelect');
    if (select) {
      let externalIndex = 0;
      [...select.options].forEach(option => {
        if (option.value === 'auto') return setText(option, '自动编排');
        if (option.value === 'demo') return setText(option, '本地受控');
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
        setText(card.querySelector('p'), '按当前任务所需能力参与自动编排');
      });
    }
  }

  function genericizeAnswerFooters(root = document) {
    root.querySelectorAll?.('.answer-provider').forEach(node => setText(node, '受控运行时 · 已完成'));
  }

  function rewriteActionToast(node) {
    if (!node || node.textContent !== '操作已完成并留痕' || !lastActionResult) return;
    const messages = {
      executed: '操作已执行并留痕',
      approved: '已确认，正在等待业务系统返回结果',
      uncertain: '执行结果待核对，请先确认真实业务状态',
      failed: '执行失败，业务状态未确认变更',
      proposed: '操作仍在等待确认',
      rejected: '本次操作已取消',
    };
    node.textContent = messages[lastActionResult.status] || '操作状态已更新';
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
    let current = null;
    for (const row of rows) {
      const active = !row.classList.contains('done');
      row.classList.toggle('current', active && current == null);
      if (active && current == null) {
        current = row;
        row.setAttribute('aria-current', 'step');
      } else {
        row.removeAttribute('aria-current');
      }
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

  function fmtLatency(ms) {
    if (!Number.isFinite(ms)) return '—';
    return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
  }

  function fmtCost(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(number < 10 ? 1 : 0) : '—';
  }

  function stopLabel(reason) {
    return ({
      verified: '验证完成',
      budget_exhausted: '预算用尽',
      controller_stop: '本轮主动停止',
      no_high_value_action: '没有更高价值的下一步',
      stagnated: '补证没有改变状态',
      step_limit: '达到处理步数上限',
      evidence_incomplete: '证据仍不完整',
    })[reason] || '等待本轮结果';
  }

  function runtimeFacts() {
    return telemetry.runtime?.belief?.facts || telemetry.runtime?.runtime?.belief?.facts || {};
  }

  function ensureRuntimePulse() {
    let host = document.getElementById('runtimePulse');
    if (host) return host;
    const panel = document.getElementById('panel-progress');
    if (!panel) return null;
    host = document.createElement('section');
    host.id = 'runtimePulse';
    host.className = 'runtime-pulse';
    host.setAttribute('aria-label', '运行质量与决策透明度');
    const first = panel.firstElementChild;
    if (first) first.after(host); else panel.prepend(host);
    return host;
  }

  function renderRuntimePulse() {
    const host = ensureRuntimePulse();
    if (!host) return;
    const runtime = telemetry.runtime || {};
    const facts = runtimeFacts();
    const routing = facts.routing_policy || telemetry.routing || {};
    const runtimeMs = Number(facts.runtime_elapsed_ms ?? NaN);
    const samples = Number(routing.samples ?? routing.policy?.samples ?? 0);
    const drift = Number(routing.residual_ewma ?? routing.policy?.residual_ewma ?? NaN);
    const steps = Number(runtime.autonomy_steps ?? facts.autonomy_steps ?? 0);
    const evidenceComplete = runtime.evidence_complete ?? facts.evidence_complete;
    const missing = Array.isArray(runtime.missing_evidence) ? runtime.missing_evidence : [];
    const used = runtime.tool_cost_used ?? facts.tool_cost_used;
    const budget = runtime.tool_cost_budget ?? facts.tool_cost_budget;
    const remaining = runtime.tool_cost_remaining ?? facts.tool_cost_remaining;
    const reason = runtime.stop_reason || facts.stop_reason || '';
    const mode = runtime.autonomy_mode || facts.autonomy_mode || '';
    const sync = telemetry.connected || /已同步/.test(document.getElementById('taskConnection')?.textContent || '');
    const evidenceLabel = evidenceComplete === true ? '证据完整' : evidenceComplete === false ? `缺证 ${missing.length || '待核'}` : '等待本轮';
    const modeLabel = mode === 'model_controller' ? '自适应认知' : mode ? '受控本地' : '等待任务';
    const gap = missing[0] ? String(missing[0]).slice(0, 48) : '';
    const html = `
      <div class="runtime-pulse-head">
        <div><span class="runtime-live ${sync ? 'ok' : ''}"></span><b>运行质量</b></div>
        <small>证据与权限 · 可审计</small>
      </div>
      <div class="runtime-pulse-grid">
        <div><small>证据状态</small><strong>${evidenceLabel}</strong></div>
        <div><small>工具预算</small><strong>${fmtCost(used)} / ${fmtCost(budget)}</strong></div>
        <div><small>自主步骤</small><strong>${steps || '—'}</strong></div>
        <div><small>停止原因</small><strong>${stopLabel(reason)}</strong></div>
        <div><small>API RTT</small><strong>${fmtLatency(telemetry.apiEwmaMs)}</strong></div>
        <div><small>运行模式</small><strong>${modeLabel}</strong></div>
      </div>
      <div class="runtime-pulse-foot">
        <span>${gap ? `最先缺口：${gap}` : Number.isFinite(Number(remaining)) ? `剩余预算 ${fmtCost(remaining)}` : `任务用时 ${fmtLatency(runtimeMs)}`}</span>
        <span>${samples > 0 ? `策略样本 ${samples}${Number.isFinite(drift) ? ` · 漂移 ${drift.toFixed(3)}` : ''}` : Number.isFinite(telemetry.counterfactualMs) ? `反事实复核 ${fmtLatency(telemetry.counterfactualMs)}` : '证据优先'}</span>
      </div>`;
    if (html === pulseSignature) return;
    pulseSignature = html;
    host.innerHTML = html;
  }

  function runUiPass() {
    uiScheduled = false;
    genericizeProviders();
    genericizeAnswerFooters();
    installCorrectionActions();
    markCurrentProgress();
    const messages = document.getElementById('messageList');
    if (messages) animateNewMessages(messages);
    rewriteActionToast(document.getElementById('toast'));
    renderUploadGuard();
    renderRuntimePulse();
  }

  function scheduleUiPass() {
    if (uiScheduled) return;
    uiScheduled = true;
    requestAnimationFrame(runUiPass);
  }

  function installObserver() {
    const root = document.querySelector('.app-shell') || document.body;
    new MutationObserver(scheduleUiPass).observe(root, {
      childList: true,
      subtree: true,
    });
  }

  function installMotion() {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    if (!reduced.matches) requestAnimationFrame(() => document.documentElement.classList.add('ui-motion-ready'));
    reduced.addEventListener?.('change', event => {
      document.documentElement.classList.toggle('ui-motion-ready', !event.matches);
    });
  }

  window.addEventListener('unhandledrejection', event => {
    const message = String(event.reason?.message || event.reason || '');
    if (!/请求失败|Failed to fetch|NetworkError|任务不存在|服务连接/.test(message)) return;
    event.preventDefault();
    resetUploadGuard();
    const toast = document.getElementById('toast');
    if (toast) {
      toast.textContent = '操作未完成，请检查连接后重试';
      toast.classList.add('show');
      setTimeout(() => toast.classList.remove('show'), 3200);
    }
  });

  document.addEventListener('DOMContentLoaded', () => {
    document.documentElement.dataset.ui = 'ecomevo';
    document.querySelector('.task-head')?.style.setProperty('position', 'relative');
    installObserver();
    installMotion();
    renderUploadGuard();
    scheduleUiPass();
  });
})();