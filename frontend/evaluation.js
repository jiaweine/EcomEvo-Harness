(() => {
  const API = '/api/runtime/evaluations';
  const domainNames = {
    product_governance: '商品治理',
    merchant_review: '商家审核',
    aftersales: '售后判责',
    risk_review: '风险核查',
    content_audit: '内容审核',
    general: '一般核对',
  };
  const $ = (id) => document.getElementById(id);
  const node = (tag, className, text) => {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = String(text);
    return item;
  };
  const state = { catalog: null, runs: [], selectedRun: null };

  async function fetchJson(url, options) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json', ...(options && options.headers ? options.headers : {}) },
      ...options,
    });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) {
      const detail = payload && payload.detail ? payload.detail : `请求失败 (${response.status})`;
      throw new Error(detail);
    }
    return payload;
  }

  function shortHash(value) {
    const text = String(value || '');
    return text ? `${text.slice(0, 10)}…${text.slice(-6)}` : '—';
  }

  function timeText(value) {
    const date = new Date(Number(value || 0) * 1000);
    if (Number.isNaN(date.getTime())) return '—';
    return new Intl.DateTimeFormat('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }).format(date);
  }

  function statusLabel(value) {
    if (value === 'completed') return '完成';
    if (value === 'needs_evidence') return '需要补证';
    return String(value || '—');
  }

  function renderCatalog() {
    const catalog = state.catalog;
    if (!catalog) return;
    $('caseCount').textContent = String(catalog.case_count || 0);
    const domains = new Set((catalog.cases || []).map((item) => item.domain));
    $('domainCount').textContent = String(domains.size);
    $('sourceHash').textContent = shortHash(catalog.source_hash);
    $('sourceHash').title = String(catalog.source_hash || '');

    const list = $('caseList');
    list.textContent = '';
    (catalog.cases || []).forEach((item) => {
      const row = node('article', 'case-row');
      const main = node('div', 'case-main');
      main.append(node('b', '', item.id));
      main.append(node('span', '', item.text));
      row.append(main);
      row.append(node('div', 'domain', domainNames[item.domain] || item.domain));
      const expected = node('div', `expect ${item.expected_status === 'needs_evidence' ? 'needs' : ''}`);
      expected.append(node('i'));
      const expectedText = item.expected_status === 'needs_evidence'
        ? `需补证 · ${(item.missing_contains || []).join(' / ') || '证据缺口'}`
        : '应完成';
      expected.append(node('span', '', expectedText));
      row.append(expected);
      list.append(row);
    });
  }

  function latestRunSummary() {
    const latest = state.runs[0];
    if (!latest) {
      $('latestStatus').textContent = '—';
      $('latestStatusDetail').textContent = '尚未运行';
      $('latestDrift').textContent = '—';
      return;
    }
    $('latestStatus').textContent = latest.ok ? 'PASS' : 'FAIL';
    $('latestStatusDetail').textContent = `${latest.failed_case_count || 0} 个失败场景`;
    $('latestDrift').textContent = String(latest.drift_case_count || 0);
  }

  function renderRuns() {
    latestRunSummary();
    const list = $('runList');
    list.textContent = '';
    if (!state.runs.length) {
      list.append(node('div', 'empty-list', '还没有评估快照。'));
      return;
    }
    state.runs.forEach((run) => {
      const button = node('button', `run-row ${state.selectedRun && state.selectedRun.id === run.id ? 'active' : ''}`);
      button.type = 'button';
      const top = node('div', 'run-row-top');
      top.append(node('span', 'run-id', run.id));
      top.append(node('span', 'run-time', timeText(run.created_at)));
      button.append(top);
      const kind = run.ok ? (run.drift_case_count ? 'warn' : '') : 'fail';
      const status = node('div', `run-status ${kind}`);
      status.append(node('i'));
      status.append(node('span', '', run.ok ? 'Gate passed' : `${run.failed_case_count} case failed`));
      button.append(status);
      button.addEventListener('click', () => loadRun(run.id));
      list.append(button);
    });
  }

  function addBadge(text, className) {
    $('detailBadges').append(node('span', `badge ${className || ''}`, text));
  }

  function renderDetail(run) {
    state.selectedRun = run;
    renderRuns();
    $('emptyDetail').hidden = true;
    $('detailContent').hidden = false;
    $('detailTitle').textContent = `评估快照 ${run.id}`;
    $('detailBadges').textContent = '';
    addBadge(run.ok ? 'Release gate passed' : 'Release gate failed', run.ok ? '' : 'fail');
    const drift = Number((run.summary || {}).drift_case_count || 0);
    addBadge(drift ? `${drift} replay drift` : 'Replay stable', drift ? 'warn' : '');
    addBadge(shortHash(run.source_hash));

    const summary = $('runSummary');
    summary.textContent = '';
    const values = [
      ['场景通过', `${(run.summary || {}).passed_case_count || 0}/${run.case_count || 0}`],
      ['失败场景', (run.summary || {}).failed_case_count || 0],
      ['Replay 漂移', drift],
      ['执行阶段', run.phase_count || 0],
    ];
    values.forEach(([label, value]) => {
      const card = node('article');
      card.append(node('small', '', label));
      card.append(node('b', '', value));
      summary.append(card);
    });

    const body = $('comparisonBody');
    body.textContent = '';
    (run.comparisons || []).forEach((item) => {
      const tr = node('tr');
      tr.append(node('td', '', item.id));
      tr.append(node('td', 'status-text', statusLabel(item.fresh_status)));
      tr.append(node('td', 'status-text', statusLabel(item.replay_status)));
      const missing = node('td');
      const missingList = node('div', 'missing-list');
      const values = Array.from(new Set([...(item.fresh_missing_evidence || []), ...(item.replay_missing_evidence || [])]));
      if (!values.length) missingList.append(node('span', '', '无'));
      values.forEach((value) => missingList.append(node('span', '', value)));
      missing.append(missingList);
      tr.append(missing);
      const stability = node('td');
      const status = node('span', `stability ${item.stable ? '' : 'drift'}`);
      status.append(node('i'));
      status.append(node('span', '', item.stable ? '稳定' : '有漂移'));
      stability.append(status);
      tr.append(stability);
      body.append(tr);
    });

    const failures = run.failures || [];
    $('failureBox').hidden = !failures.length;
    const failureList = $('failureList');
    failureList.textContent = '';
    failures.forEach((failure) => failureList.append(node('p', '', failure)));
  }

  async function loadRun(runId) {
    try {
      const run = await fetchJson(`${API}/runs/${encodeURIComponent(runId)}`);
      renderDetail(run);
    } catch (error) {
      $('runHint').textContent = error.message;
    }
  }

  async function loadRuns(selectLatest) {
    const payload = await fetchJson(`${API}/runs?limit=30`);
    state.runs = payload.items || [];
    renderRuns();
    if (selectLatest && state.runs.length) await loadRun(state.runs[0].id);
  }

  async function loadCatalog() {
    state.catalog = await fetchJson(`${API}/cases`);
    renderCatalog();
  }

  async function runEvaluation() {
    const button = $('runBtn');
    button.disabled = true;
    button.classList.add('loading');
    button.textContent = '正在运行…';
    $('runHint').textContent = '正在隔离 Runtime 中执行 fresh + persisted replay。';
    try {
      const run = await fetchJson(`${API}/runs`, { method: 'POST' });
      $('runHint').textContent = run.ok ? '评估通过，快照已保存。' : '评估完成，但 release gate 未通过。';
      await loadRuns(false);
      renderDetail(run);
    } catch (error) {
      $('runHint').textContent = error.message;
    } finally {
      button.disabled = false;
      button.classList.remove('loading');
      button.textContent = '运行 Gold Set';
    }
  }

  $('runBtn').addEventListener('click', runEvaluation);
  $('refreshBtn').addEventListener('click', () => loadRuns(false).catch((error) => { $('runHint').textContent = error.message; }));

  Promise.all([loadCatalog(), loadRuns(true)]).catch((error) => {
    $('runHint').textContent = error.message;
    $('caseList').textContent = '';
    $('caseList').append(node('div', 'empty-list', '无法读取评估控制面。请确认当前账号具有 admin 权限。'));
  });
})();
