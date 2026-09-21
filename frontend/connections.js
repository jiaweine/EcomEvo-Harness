(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  let rows = [];
  let toastTimer = null;

  const TEXT = {
    authority: {
      authoritative_internal: '权威内部系统',
      controlled_knowledge: '受控知识源',
      submitted_evidence: '提交证据',
      external_public: '外部公开源',
      unknown: '未声明',
    },
    freshness: { live: '实时', cached: '缓存', unknown: '未声明' },
    health: {
      healthy: '连接正常', unhealthy: '检查失败', disabled: '已停用', not_checked: '未检查', checking: '检查中',
    },
    capability: { read: 'Read', governed_action: 'Governed Action', unknown: 'Unknown' },
    scope: { read_only: 'Read only', governed_write: 'Governed write', mixed: 'Mixed', unknown: '未声明' },
    idempotency: { required: 'Required', supported: 'Supported', not_applicable: 'N/A', unknown: '未声明' },
    schema: { first_observation: '首次基线', unchanged: '未变化', changed: 'Schema changed', unknown: '未知' },
    warning: {
      access_scope_not_declared: '未声明 access scope',
      declared_read_only_but_action_binding_exists: '声明只读但存在 governed action',
      declared_write_scope_omits_read_bindings: '声明写范围但同时存在 read binding',
      credential_owner_not_declared: '已配置凭据但未声明 credential owner',
      governed_action_idempotency_not_declared: '存在 governed action 但未声明幂等语义',
    },
  };

  function esc(value = '') {
    return String(value).replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
  }

  function toast(message) {
    const node = $('toast');
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('show'), 2600);
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json', ...(options.headers || {}) },
      ...options,
    });
    if (!response.ok) {
      let detail = `请求失败 ${response.status}`;
      try { detail = (await response.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return response.json();
  }

  function toolRow(tool) {
    const capability = tool.capability || 'unknown';
    const bindings = Array.isArray(tool.bindings) ? tool.bindings.join(' · ') : '';
    const tags = Array.isArray(tool.evidence_tags) && tool.evidence_tags.length
      ? ` · evidence: ${tool.evidence_tags.join(', ')}`
      : '';
    const idem = tool.idempotency && tool.idempotency !== 'not_applicable'
      ? ` · idempotency: ${TEXT.idempotency[tool.idempotency] || tool.idempotency}`
      : '';
    const desc = tool.description || bindings || (tool.declared ? '已在 EcomEvo 显式声明' : 'MCP discovery 返回，尚未声明用途');
    return `<div class="tool">
      <span class="tool-name">${esc(tool.name || 'unnamed')}</span>
      <span class="badge ${esc(capability)}">${esc(TEXT.capability[capability] || 'Unknown')}</span>
      <span class="tool-desc">${esc(desc + tags + idem)}</span>
    </div>`;
  }

  function card(row) {
    const health = row.health?.state || (row.enabled ? 'not_checked' : 'disabled');
    const protocol = row.health?.protocol || '—';
    const latency = row.health?.latency_ms !== undefined ? `${row.health.latency_ms} ms` : '—';
    const tools = Array.isArray(row.tools) ? row.tools : [];
    const governance = row.governance || {};
    const reliability = row.reliability || {};
    const latencyStats = reliability.latency_ms || {};
    const warnings = Array.isArray(governance.warnings) ? governance.warnings : [];
    const evidenceTags = Array.isArray(governance.evidence_tags) ? governance.evidence_tags : [];
    const schemaFingerprint = row.schema?.fingerprint || reliability.latest_schema_fingerprint || '';
    const schemaChange = row.schema?.change || reliability.latest_schema_change || 'unknown';
    const successRate = typeof reliability.success_rate === 'number'
      ? `${Math.round(reliability.success_rate * 1000) / 10}%`
      : '—';
    const failureRate = typeof reliability.failure_rate === 'number'
      ? `${Math.round(reliability.failure_rate * 1000) / 10}%`
      : '—';
    return `<article class="connection" data-key="${esc(row.key)}" data-health="${esc(health)}">
      <header class="connection-head">
        <div class="identity"><span class="state-dot" aria-hidden="true"></span><div>
          <h3>${esc(row.name || row.key)}</h3>
          <p>${esc(row.key)} · ${row.enabled ? 'enabled' : 'disabled'} · streamable HTTP</p>
        </div></div>
        <div class="head-actions">
          <span class="health-label">${esc(TEXT.health[health] || health)}</span>
          <button class="probe" type="button" data-probe="${esc(row.key)}" ${row.enabled ? '' : 'disabled'}>检查连接</button>
        </div>
      </header>
      <div class="connection-body">
        <div class="meta">
          <div><span>Authority</span><b>${esc(TEXT.authority[row.authority] || '未声明')}</b></div>
          <div><span>Freshness</span><b>${esc(TEXT.freshness[row.freshness] || '未声明')}</b></div>
          <div><span>凭据</span><b>${row.auth_configured ? '已配置' : '未配置 / 不需要'}</b></div>
          <div><span>Protocol</span><b>${esc(protocol)}</b></div>
          <div><span>Latency</span><b>${esc(latency)}</b></div>
        </div>
        <section class="governance-block">
          <div class="tool-head"><b>治理契约</b><span>声明不会扩大运行时权限</span></div>
          <div class="governance-grid">
            <div><span>Data source</span><b>${esc(governance.data_source || row.name || row.key)}</b></div>
            <div><span>Declared scope</span><b>${esc(TEXT.scope[governance.declared_access_scope] || governance.declared_access_scope || '未声明')}</b></div>
            <div><span>Effective bindings</span><b>${esc(TEXT.scope[governance.effective_tool_scope] || governance.effective_tool_scope || 'Unknown')}</b></div>
            <div><span>Credential owner</span><b>${esc(governance.credential_owner || '未声明')}</b></div>
            <div><span>Idempotency</span><b>${esc(TEXT.idempotency[governance.idempotency] || governance.idempotency || '未声明')}</b></div>
            <div><span>Evidence tags</span><b>${esc(evidenceTags.length ? evidenceTags.join(' · ') : '未声明')}</b></div>
          </div>
          ${warnings.length ? `<div class="warnings">${warnings.map(code => `<span>${esc(TEXT.warning[code] || code)}</span>`).join('')}</div>` : '<div class="warnings ok"><span>当前声明未发现静态治理冲突</span></div>'}
        </section>
        <section class="reliability-block">
          <div class="tool-head"><b>可靠性历史</b><span>${Number(reliability.observations || 0)} 次只读 probe</span></div>
          <div class="reliability-grid">
            <div><span>Success rate</span><b>${esc(successRate)}</b></div>
            <div><span>Failure rate</span><b>${esc(failureRate)}</b></div>
            <div><span>P95 latency</span><b>${latencyStats.p95 !== null && latencyStats.p95 !== undefined ? `${esc(latencyStats.p95)} ms` : '—'}</b></div>
            <div><span>Schema</span><b>${esc(TEXT.schema[schemaChange] || schemaChange)}</b></div>
            <div class="schema-cell"><span>Schema fingerprint</span><code>${esc(schemaFingerprint ? schemaFingerprint.slice(0, 16) : '—')}</code></div>
          </div>
        </section>
        <div class="tool-head"><b>工具边界</b><span>${tools.length} 个可见工具</span></div>
        <div class="tools">${tools.length ? tools.map(toolRow).join('') : '<div class="tool-desc">尚无显式工具绑定；运行只读检查后可查看 MCP discovery 结果。</div>'}</div>
        ${row.health?.error ? `<p class="tool-desc">检查结果：${esc(row.health.error)}</p>` : ''}
      </div>
    </article>`;
  }

  function updateMetrics() {
    const tools = rows.flatMap(row => Array.isArray(row.tools) ? row.tools : []);
    $('metricConnections').textContent = String(rows.length);
    $('metricReads').textContent = String(tools.filter(tool => tool.capability === 'read').length);
    $('metricActions').textContent = String(tools.filter(tool => tool.capability === 'governed_action').length);
    $('metricUnknown').textContent = String(tools.filter(tool => tool.capability === 'unknown').length);
  }

  function render() {
    $('connectionGrid').innerHTML = rows.map(card).join('');
    $('emptyState').hidden = rows.length !== 0;
    updateMetrics();
  }

  async function load() {
    $('refreshBtn').disabled = true;
    try {
      const data = await api('/api/runtime/connections');
      rows = Array.isArray(data.connections) ? data.connections : [];
      $('scopeChip').textContent = `scope: ${data.scope || 'deployment'}`;
      render();
    } catch (error) {
      toast(error.message);
    } finally {
      $('refreshBtn').disabled = false;
    }
  }

  async function probe(key, button) {
    const index = rows.findIndex(row => row.key === key);
    if (index < 0) return;
    button.disabled = true;
    rows[index] = { ...rows[index], health: { state: 'checking' } };
    render();
    const active = document.querySelector(`[data-probe="${CSS.escape(key)}"]`);
    if (active) active.disabled = true;
    try {
      rows[index] = await api(`/api/runtime/connections/${encodeURIComponent(key)}/probe`, { method: 'POST' });
      render();
      toast(rows[index].health?.state === 'healthy' ? '只读连接检查完成' : '连接检查未通过');
    } catch (error) {
      toast(error.message);
      await load();
    }
  }

  document.addEventListener('click', event => {
    const button = event.target.closest('[data-probe]');
    if (button) probe(button.dataset.probe, button);
  });
  $('refreshBtn').addEventListener('click', load);
  load();
})();
