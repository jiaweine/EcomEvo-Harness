(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const state = {
    view: 'all',
    scene: '',
    query: '',
    items: [],
    selectedId: null,
    currentUser: '',
    loading: false,
  };

  const SCENES = {
    product_governance: '商品治理',
    merchant_review: '商家审核',
    aftersales: '售后判责',
    risk_review: '风险核查',
    content_audit: '内容审核',
  };

  const STATES = {
    processing: '处理中',
    waiting_approval: '待人工确认',
    waiting_evidence: '待补证据',
    needs_verification: '结果待核对',
    needs_attention: '需要关注',
    ready: '可继续',
    new: '新任务',
  };

  const PRIORITIES = {
    urgent: '紧急',
    high: '高',
    normal: '普通',
    low: '低',
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]));

  function formatTime(ts) {
    if (!ts) return '—';
    try {
      return new Date(Number(ts) * 1000).toLocaleString('zh-CN', {
        month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
      });
    } catch {
      return '—';
    }
  }

  async function api(url, options = {}) {
    const opts = { ...options };
    if (typeof opts.body === 'string') {
      opts.headers = { 'content-type': 'application/json', ...(opts.headers || {}) };
    }
    const response = await fetch(url, opts);
    if (!response.ok) {
      let message = `请求失败 ${response.status}`;
      try {
        const payload = await response.json();
        if (typeof payload.detail === 'string') message = payload.detail;
      } catch {}
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }

  let toastTimer;
  function toast(message) {
    const node = $('toast');
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('show'), 2400);
  }

  function filteredItems() {
    const q = state.query.trim().toLowerCase();
    if (!q) return state.items;
    return state.items.filter(item => [
      item.title,
      item.latest_user_content,
      item.owner_user_id,
      SCENES[item.scene],
      STATES[item.queue_state],
    ].some(value => String(value || '').toLowerCase().includes(q)));
  }

  function ownerLabel(item) {
    if (!item.owner_user_id) return '未认领';
    if (item.owner_user_id === state.currentUser) return '我';
    return String(item.owner_user_id);
  }

  function renderMetrics() {
    const summary = state.items.reduce((acc, item) => {
      acc[item.queue_state] = (acc[item.queue_state] || 0) + 1;
      return acc;
    }, {});
    $('metricTotal').textContent = String(state.items.length);
    $('metricEvidence').textContent = String(summary.waiting_evidence || 0);
    $('metricApproval').textContent = String(summary.waiting_approval || 0);
    $('metricVerify').textContent = String(summary.needs_verification || 0);
  }

  function renderList() {
    const rows = filteredItems();
    $('queueCount').textContent = `${rows.length} 条`;
    const box = $('queueList');
    if (!rows.length) {
      box.innerHTML = '<div class="queue-empty">当前筛选条件下没有任务。</div>';
      renderDetail();
      return;
    }
    box.innerHTML = rows.map(item => `
      <button class="queue-item ${item.id === state.selectedId ? 'active' : ''}" type="button" data-id="${esc(item.id)}" data-priority="${esc(item.queue_priority)}">
        <span class="priority-stripe" aria-hidden="true"></span>
        <span class="queue-copy">
          <span class="queue-kicker">
            <span class="scene-label">${esc(SCENES[item.scene] || '业务任务')}</span>
            <span class="priority-label">${esc(PRIORITIES[item.queue_priority] || '普通')}</span>
            <span class="owner-label">负责人：${esc(ownerLabel(item))}</span>
          </span>
          <h3>${esc(item.title || '未命名任务')}</h3>
          <p>${esc(item.latest_user_content || item.queue_state_reason || '尚无用户消息')}</p>
          <span class="queue-meta">
            <span>${Number(item.message_count || 0)} 条消息</span>
            <span>${Number(item.asset_count || 0)} 份资料</span>
            <span>${esc(formatTime(item.updated_at))}</span>
          </span>
        </span>
        <span class="state-pill" data-state="${esc(item.queue_state)}">${esc(STATES[item.queue_state] || '可继续')}</span>
      </button>
    `).join('');
    box.querySelectorAll('.queue-item').forEach(button => {
      button.addEventListener('click', () => {
        state.selectedId = button.dataset.id;
        renderList();
        renderDetail();
      });
    });
  }

  function selectedItem() {
    return state.items.find(item => item.id === state.selectedId) || null;
  }

  function renderDetail() {
    const item = selectedItem();
    $('detailEmpty').hidden = Boolean(item);
    $('detailContent').hidden = !item;
    if (!item) return;

    const stateNode = $('detailState');
    stateNode.textContent = STATES[item.queue_state] || '可继续';
    stateNode.dataset.state = item.queue_state || 'ready';
    $('detailScene').textContent = SCENES[item.scene] || '业务任务';
    $('detailTitle').textContent = item.title || '未命名任务';
    $('detailPriority').textContent = PRIORITIES[item.queue_priority] || '普通';
    $('detailReason').textContent = item.queue_state_reason || '当前没有需要人工处理的阻塞项。';
    $('detailOwner').textContent = ownerLabel(item);
    $('detailUpdated').textContent = formatTime(item.updated_at);
    $('detailCounts').textContent = `${Number(item.message_count || 0)} 条 / ${Number(item.asset_count || 0)} 份`;
    $('detailQuestion').textContent = item.latest_user_content || '暂无用户消息';
    $('prioritySelect').value = item.queue_priority || 'normal';
    $('openTaskLink').href = `/?conversation=${encodeURIComponent(item.id)}`;

    const claim = $('claimBtn');
    const owner = item.owner_user_id || '';
    if (!owner) {
      claim.hidden = false;
      claim.disabled = false;
      claim.dataset.action = 'claim';
      claim.textContent = '认领任务';
    } else if (owner === state.currentUser) {
      claim.hidden = false;
      claim.disabled = false;
      claim.dataset.action = 'release';
      claim.textContent = '释放认领';
    } else {
      claim.hidden = false;
      claim.disabled = true;
      claim.dataset.action = 'other';
      claim.textContent = '已被其他同事认领';
    }
  }

  function setLoading(value) {
    state.loading = Boolean(value);
    $('refreshBtn').disabled = state.loading;
    $('syncState').textContent = state.loading ? '正在同步' : '已同步';
  }

  async function loadInbox({ preserveSelection = true } = {}) {
    setLoading(true);
    try {
      const params = new URLSearchParams({ view: state.view, limit: '200' });
      if (state.scene) params.set('scene', state.scene);
      const payload = await api(`/api/inbox?${params.toString()}`);
      state.currentUser = payload.current_user || '';
      state.items = Array.isArray(payload.items) ? payload.items : [];
      if (!preserveSelection || !state.items.some(item => item.id === state.selectedId)) {
        state.selectedId = state.items[0]?.id || null;
      }
      renderMetrics();
      renderList();
      renderDetail();
      $('syncState').textContent = '已同步';
    } catch (error) {
      $('syncState').textContent = '同步失败';
      toast(error.message || '队列加载失败');
      if (!state.items.length) {
        $('queueList').innerHTML = '<div class="queue-empty">暂时无法加载任务队列，请稍后刷新。</div>';
      }
    } finally {
      setLoading(false);
    }
  }

  async function claimOrRelease() {
    const item = selectedItem();
    if (!item || state.loading) return;
    const action = $('claimBtn').dataset.action;
    if (!['claim', 'release'].includes(action)) return;
    setLoading(true);
    try {
      await api(`/api/inbox/${encodeURIComponent(item.id)}/claim`, {
        method: action === 'claim' ? 'POST' : 'DELETE',
      });
      toast(action === 'claim' ? '任务已认领' : '已释放认领');
      await loadInbox();
    } catch (error) {
      toast(error.message || '认领状态更新失败');
      await loadInbox();
    } finally {
      setLoading(false);
    }
  }

  async function updatePriority() {
    const item = selectedItem();
    if (!item || state.loading) return;
    const priority = $('prioritySelect').value;
    if (priority === item.queue_priority) return;
    setLoading(true);
    try {
      await api(`/api/inbox/${encodeURIComponent(item.id)}/priority`, {
        method: 'PATCH',
        body: JSON.stringify({ priority }),
      });
      toast('优先级已更新');
      await loadInbox();
    } catch (error) {
      toast(error.message || '优先级更新失败');
      $('prioritySelect').value = item.queue_priority || 'normal';
    } finally {
      setLoading(false);
    }
  }

  function bind() {
    document.querySelectorAll('[data-view]').forEach(button => {
      button.addEventListener('click', () => {
        if (state.view === button.dataset.view) return;
        state.view = button.dataset.view;
        document.querySelectorAll('[data-view]').forEach(node => node.classList.toggle('active', node === button));
        loadInbox({ preserveSelection: false });
      });
    });
    $('sceneFilter').addEventListener('change', event => {
      state.scene = event.target.value || '';
      loadInbox({ preserveSelection: false });
    });
    $('searchInput').addEventListener('input', event => {
      state.query = event.target.value || '';
      renderList();
      renderDetail();
    });
    $('refreshBtn').addEventListener('click', () => loadInbox());
    $('claimBtn').addEventListener('click', claimOrRelease);
    $('prioritySelect').addEventListener('change', updatePriority);
  }

  document.addEventListener('DOMContentLoaded', () => {
    bind();
    loadInbox({ preserveSelection: false });
    window.__ECOMEVO_INBOX_READY__ = true;
  });
})();
