(() => {
  'use strict';

  const CATEGORY = {
    factual_error: '事实有误',
    missing_support: '缺少直接支持',
    wrong_rule: '规则引用有误',
    stale_source: '来源已过期',
    evidence_conflict: '证据存在冲突',
    other: '其他问题',
  };
  const STATUS = {
    open: '待复核',
    acknowledged: '已确认收到',
    accepted_for_eval: '进入评估候选',
    needs_followup: '需要补充',
    dismissed: '已驳回',
  };
  const IMPACT = {
    answer_only: '仅影响表述',
    decision_relevant: '可能影响判断',
    action_blocking: '涉及高影响操作',
  };
  const state = { items: [], selectedId: '', sample: null };
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

  async function api(url, opts = {}) {
    const options = { ...opts };
    if (typeof options.body === 'string') options.headers = { 'content-type': 'application/json', ...(options.headers || {}) };
    const response = await fetch(url, options);
    if (!response.ok) {
      let detail = `请求失败 ${response.status}`;
      try {
        const payload = await response.json();
        if (typeof payload.detail === 'string') detail = payload.detail;
      } catch {}
      throw new Error(detail);
    }
    return response.status === 204 ? null : response.json();
  }

  let toastTimer;
  function toast(message) {
    const node = $('feedbackAdminToast');
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('show'), 2600);
  }

  function fmtTime(value) {
    if (!value) return '—';
    try { return new Date(Number(value) * 1000).toLocaleString('zh-CN'); } catch { return '—'; }
  }

  function targetSummary(item) {
    const target = item?.target_snapshot?.target || {};
    if (target.type === 'claim') return `声明 · ${target.text || target.ref || '未命名'}`;
    if (target.type === 'evidence') return `证据 · ${target.title || target.ref || '未命名'}`;
    return '整个回答';
  }

  function renderMetrics() {
    const rows = state.items;
    const open = rows.filter(row => row.status === 'open').length;
    const blocking = rows.filter(row => row.impact === 'action_blocking').length;
    const evalReady = rows.filter(row => row.status === 'accepted_for_eval').length;
    const conflicts = rows.filter(row => row.category === 'evidence_conflict').length;
    $('feedbackMetrics').innerHTML = [
      ['待复核', open],
      ['涉及高影响操作', blocking],
      ['评估候选', evalReady],
      ['证据冲突', conflicts],
    ].map(([label, value]) => `<div class="feedback-admin-metric"><small>${esc(label)}</small><b>${Number(value)}</b></div>`).join('');
  }

  function renderList() {
    $('feedbackCount').textContent = `${state.items.length} 条`;
    const box = $('feedbackAdminList');
    if (!state.items.length) {
      box.innerHTML = '<div class="feedback-admin-empty">当前筛选条件下没有异议。</div>';
      $('feedbackAdminDetail').innerHTML = '<div class="feedback-admin-empty">没有可显示的异议。</div>';
      state.selectedId = '';
      return;
    }
    box.innerHTML = state.items.map(item => `
      <button type="button" class="feedback-admin-item ${item.id === state.selectedId ? 'active' : ''}" data-feedback-id="${esc(item.id)}">
        <div class="feedback-admin-item-top"><b>${esc(CATEGORY[item.category] || item.category)}</b><span>${esc(STATUS[item.status] || item.status)}</span></div>
        <p>${esc(item.explanation || '').slice(0, 180)}</p>
        <small>${esc(IMPACT[item.impact] || item.impact)} · ${esc(fmtTime(item.created_at))}</small>
      </button>`).join('');
    box.querySelectorAll('[data-feedback-id]').forEach(button => {
      button.onclick = () => selectItem(button.dataset.feedbackId);
    });
  }

  function detailMarkup(item) {
    const target = item.target_snapshot?.target || {};
    const correction = item.proposed_correction || '未提供建议修正';
    const reviewNote = item.review_payload?.note || '尚无复核备注';
    return `
      <div class="feedback-admin-detail-head">
        <div><small>${esc(item.id)}</small><h2>${esc(CATEGORY[item.category] || item.category)}</h2><p>${esc(targetSummary(item))}</p></div>
        <span class="feedback-admin-status">${esc(STATUS[item.status] || item.status)}</span>
      </div>
      <div class="feedback-admin-detail-grid">
        <article class="feedback-admin-card"><small>影响范围</small><b>${esc(IMPACT[item.impact] || item.impact)}</b></article>
        <article class="feedback-admin-card"><small>提交人</small><b>${esc(item.submitted_by)}</b></article>
        <article class="feedback-admin-card"><small>任务 / 回复</small><p><a href="/?conversation=${encodeURIComponent(item.conversation_id)}">${esc(item.conversation_id)}</a><br>${esc(item.assistant_message_id)}</p></article>
        <article class="feedback-admin-card"><small>当前 grounding 状态</small><b>${esc(item.target_snapshot?.evidence_sufficiency || '未提供')}</b></article>
        <article class="feedback-admin-card wide"><small>operator 观察</small><p>${esc(item.explanation)}</p></article>
        <article class="feedback-admin-card wide"><small>建议修正</small><p>${esc(correction)}</p></article>
        <article class="feedback-admin-card wide"><small>目标快照</small><pre>${esc(JSON.stringify(target, null, 2))}</pre></article>
        <article class="feedback-admin-card wide"><small>最近复核备注</small><p>${esc(reviewNote)}</p></article>
      </div>
      <section class="feedback-admin-actions">
        <textarea id="feedbackReviewNote" maxlength="4000" placeholder="复核备注（可选）"></textarea>
        <div class="feedback-admin-action-row">
          <button type="button" data-review="acknowledged">确认收到</button>
          <button type="button" data-review="needs_followup">需要补充</button>
          <button type="button" class="primary" data-review="accepted_for_eval">进入评估候选</button>
          <button type="button" data-review="dismissed">驳回</button>
          <button type="button" id="feedbackSampleBtn">查看评估样本</button>
        </div>
      </section>
      <section class="feedback-admin-sample" id="feedbackSamplePanel"></section>`;
  }

  async function selectItem(id) {
    state.selectedId = id;
    state.sample = null;
    renderList();
    try {
      const item = await api(`/api/runtime/feedback/${encodeURIComponent(id)}`);
      $('feedbackAdminDetail').innerHTML = detailMarkup(item);
      $('feedbackAdminDetail').querySelectorAll('[data-review]').forEach(button => {
        button.onclick = () => review(id, button.dataset.review);
      });
      $('feedbackSampleBtn').onclick = () => loadSample(id);
    } catch (error) {
      $('feedbackAdminDetail').innerHTML = `<div class="feedback-admin-empty">${esc(error.message)}</div>`;
    }
  }

  async function review(id, decision) {
    const note = $('feedbackReviewNote')?.value?.trim() || '';
    try {
      await api(`/api/runtime/feedback/${encodeURIComponent(id)}/review`, {
        method: 'POST',
        body: JSON.stringify({ decision, note }),
      });
      toast('复核事件已追加；生产 authority 未被修改。');
      await loadItems(id);
    } catch (error) {
      toast(error.message || '复核失败');
    }
  }

  async function loadSample(id) {
    const panel = $('feedbackSamplePanel');
    panel.innerHTML = '<div class="feedback-admin-empty">正在生成只读评估样本…</div>';
    try {
      state.sample = await api(`/api/runtime/feedback/${encodeURIComponent(id)}/evaluation-sample`);
      panel.innerHTML = `<article class="feedback-admin-card wide"><small>Evaluation sample · read only</small><pre>${esc(JSON.stringify(state.sample, null, 2))}</pre></article>`;
    } catch (error) {
      panel.innerHTML = `<div class="feedback-admin-empty">${esc(error.message)}</div>`;
    }
  }

  async function loadItems(keepSelected = '') {
    const status = $('feedbackStatusFilter').value;
    const category = $('feedbackCategoryFilter').value;
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    if (category) params.set('category', category);
    params.set('limit', '200');
    try {
      const data = await api(`/api/runtime/feedback?${params.toString()}`);
      state.items = Array.isArray(data.items) ? data.items : [];
      state.selectedId = keepSelected && state.items.some(row => row.id === keepSelected)
        ? keepSelected
        : (state.items[0]?.id || '');
      renderMetrics();
      renderList();
      if (state.selectedId) await selectItem(state.selectedId);
    } catch (error) {
      $('feedbackAdminList').innerHTML = `<div class="feedback-admin-empty">${esc(error.message)}</div>`;
      toast(error.message || '加载失败');
    }
  }

  $('feedbackRefreshBtn').onclick = () => loadItems(state.selectedId);
  $('feedbackStatusFilter').onchange = () => loadItems('');
  $('feedbackCategoryFilter').onchange = () => loadItems('');
  loadItems('');
})();
