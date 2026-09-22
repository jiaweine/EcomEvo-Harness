(() => {
  const API = '/api/runtime/readiness';
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  let current = null;

  async function fetchJson(url, options) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json', ...(options?.headers || {}) },
      ...options,
    });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) throw new Error(payload?.detail || `请求失败 (${response.status})`);
    return payload;
  }

  function timeText(value) {
    const d = new Date(Number(value || 0) * 1000);
    if (Number.isNaN(d.getTime())) return '—';
    return new Intl.DateTimeFormat('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }).format(d);
  }

  function statusText(value) {
    if (value === 'ready_for_human_release_review') return '可进入人工发布评审';
    if (value === 'blocked') return '存在发布前阻断';
    return String(value || '—');
  }

  function checkLabel(value) {
    return { pass: '通过', blocker: '阻断', warning: '提醒', info: '信息' }[value] || value;
  }

  function toast(message) {
    $('toast').textContent = message;
    $('toast').classList.add('show');
    window.setTimeout(() => $('toast').classList.remove('show'), 2600);
  }

  function renderPreview(data) {
    current = data;
    $('statusValue').textContent = statusText(data.status);
    $('statusValue').className = data.status === 'blocked' ? 'blocked' : 'ready';
    $('statusDetail').textContent = data.status === 'blocked'
      ? '先处理确定性 blocker，再进入人工发布评审。'
      : '这不是发布批准，也不会触发任何生产变更。';
    $('blockerCount').textContent = String(data.blocker_count ?? 0);
    $('warningCount').textContent = String(data.warning_count ?? 0);
    $('tenantValue').textContent = String(data.tenant_scope || '—');
    $('windowValue').textContent = `观察窗口 ${data.window || '—'}`;
    $('generatedAt').textContent = timeText(data.generated_at);

    $('checkList').innerHTML = (data.checks || []).map((row) => `
      <article class="rr-check ${esc(row.status)}">
        <div class="rr-check-head"><b>${esc(row.title)}</b><span>${esc(checkLabel(row.status))}</span></div>
        <p>${esc(row.detail)}</p>
        <small>${esc(row.source)} · ${esc(row.id)}</small>
      </article>
    `).join('');

    const src = data.sources || {};
    const evaluation = src.evaluation || {};
    const feedback = src.feedback || {};
    const obs = src.observability || {};
    const policy = src.policy || {};
    const connection = src.connections || {};
    const topology = src.deployment_topology || {};
    const migration = src.multi_node_migration || {};
    const openFeedbackCount = Number(feedback.open_count ?? 0);
    const openFeedbackCountLabel = feedback.exact_count === true
      ? `${openFeedbackCount} open · exact`
      : `${openFeedbackCount} open · count completeness unknown`;
    $('sourceSummary').innerHTML = [
      ['Gold Set', evaluation.available ? `${evaluation.latest?.ok ? 'PASS' : 'FAIL'} · ${evaluation.latest?.case_count || 0} cases` : '无快照'],
      ['Open feedback', `${openFeedbackCountLabel} · action-blocking ${feedback.action_blocking || 0}`],
      ['Uncertain actions', obs.authority_workload?.current_uncertain_actions ?? 0],
      ['Evidence gaps', `${obs.quality?.evidence_gap_results || 0}/${obs.quality?.assistant_results || 0}`],
      ['Connections', `${connection.count || 0} · observational catalog`],
      ['Policy inventory', `${policy.visible_versions || 0} visible versions · scope-dependent`],
      [
        'Deployment topology',
        topology.declaration_valid
          ? `${topology.storage_backend || 'unknown'} · declared ${topology.declared_nodes} node(s) · certified max ${topology.certified_max_nodes ?? '—'}`
          : 'node count 未有效声明 · fail closed',
      ],
      [
        'Multi-node migration',
        migration.ready === true
          ? 'ready'
          : `not ready · ${migration.blocker_count ?? '—'} prerequisites · self-attestation disabled`,
      ],
    ].map(([label, value]) => `
      <article><small>${esc(label)}</small><b>${esc(value)}</b></article>
    `).join('');
  }

  async function loadPreview() {
    const windowKey = $('windowSelect').value;
    const data = await fetchJson(`${API}/preview?window=${encodeURIComponent(windowKey)}`);
    renderPreview(data);
  }

  async function createSnapshot() {
    const button = $('snapshotBtn');
    button.disabled = true;
    try {
      const windowKey = $('windowSelect').value;
      const data = await fetchJson(`${API}/snapshots?window=${encodeURIComponent(windowKey)}`, { method: 'POST' });
      renderPreview(data);
      await loadSnapshots();
      toast(`已保存不可变证据快照 ${data.id}`);
    } finally {
      button.disabled = false;
    }
  }

  async function loadSnapshots() {
    const payload = await fetchJson(`${API}/snapshots?limit=30`);
    const list = $('snapshotList');
    const items = payload.items || [];
    if (!items.length) {
      list.innerHTML = '<div class="rr-empty">还没有准备度快照。</div>';
      return;
    }
    list.innerHTML = items.map((row) => `
      <article>
        <div><b>${esc(row.id)}</b><span>${esc(statusText(row.status))}</span></div>
        <p>${esc(timeText(row.created_at))} · ${esc(row.window_key)} · blockers ${esc(row.blocker_count)} · warnings ${esc(row.warning_count)}</p>
        <small>sha256 ${esc(String(row.content_hash || '').slice(0, 18))}…</small>
      </article>
    `).join('');
  }

  $('refreshBtn').addEventListener('click', () => loadPreview().catch((e) => toast(e.message)));
  $('snapshotBtn').addEventListener('click', () => createSnapshot().catch((e) => toast(e.message)));
  $('historyRefreshBtn').addEventListener('click', () => loadSnapshots().catch((e) => toast(e.message)));
  $('windowSelect').addEventListener('change', () => loadPreview().catch((e) => toast(e.message)));

  Promise.all([loadPreview(), loadSnapshots()]).catch((error) => {
    toast(error.message);
    $('checkList').innerHTML = '<div class="rr-empty">无法读取发布准备度控制面。请确认当前账号具有 admin 权限。</div>';
  });
})();
