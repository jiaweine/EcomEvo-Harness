(() => {
  'use strict';

  const STATUS = {
    sufficient: {
      label: '证据充分',
      tone: 'ok',
      detail: '用户问题已被当前证据覆盖，事实性结论均通过逐条核验。',
    },
    insufficient: {
      label: '证据不足',
      tone: 'warn',
      detail: '仍有问题、事实或规则没有被当前证据直接覆盖。',
    },
    conflicted: {
      label: '存在冲突',
      tone: 'danger',
      detail: '当前证据中存在会推翻部分事实性结论的直接反证。',
    },
  };

  function escapeHtml(value = '') {
    return String(value).replace(/[&<>"']/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[character]));
  }

  function inferStatus(grounding) {
    if (!grounding) return null;
    if (STATUS[grounding.evidence_sufficiency]) return grounding.evidence_sufficiency;
    if (Number(grounding.contradicted_factual_claim_count || 0) > 0) return 'conflicted';
    const subqueries = grounding.subqueries || [];
    const claims = grounding.claims || [];
    const uncovered = subqueries.some(row => !row.covered);
    const unsupported = claims.some(row => ['fact', 'rule'].includes(row.kind) && row.verdict !== 'supported');
    return !subqueries.length || uncovered || unsupported ? 'insufficient' : 'sufficient';
  }

  function percent(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
    return `${Math.round(Math.max(0, Math.min(1, Number(value))) * 100)}%`;
  }

  function evidenceName(ids, byId) {
    const names = [];
    for (const id of (ids || []).slice(0, 3)) {
      const row = byId.get(String(id));
      const name = row?.title || row?.source || id;
      if (name && !names.includes(name)) names.push(name);
    }
    return names.join(' · ');
  }

  function claimGroup(title, rows, tone, byId) {
    if (!rows.length) return '';
    return `<section class="trust-claim-group ${tone}">
      <header><b>${escapeHtml(title)}</b><span>${rows.length}</span></header>
      <div class="trust-claim-list">${rows.slice(0, 10).map(row => {
        const source = evidenceName(row.evidence_ids, byId);
        const reason = String(row.reason || '').trim();
        return `<div class="trust-claim-row">
          <i aria-hidden="true"></i>
          <div><b>${escapeHtml(row.text || '未命名声明')}</b>${source ? `<small>依据：${escapeHtml(source)}</small>` : ''}${reason ? `<small>${escapeHtml(reason)}</small>` : ''}</div>
        </div>`;
      }).join('')}</div>
    </section>`;
  }

  function renderSnapshot(snapshot) {
    const audit = snapshot?.grounding;
    const rows = Array.isArray(snapshot?.rows) ? snapshot.rows : [];
    const box = document.getElementById('evidenceList');
    if (!box) return;
    box.querySelector(':scope > .trust-surface')?.remove();
    if (!audit) return;

    const status = inferStatus(audit);
    const meta = STATUS[status] || STATUS.insufficient;
    const claims = Array.isArray(audit.claims) ? audit.claims : [];
    const supported = claims.filter(row => ['fact', 'rule'].includes(row.kind) && row.verdict === 'supported');
    const contradicted = claims.filter(row => ['fact', 'rule'].includes(row.kind) && row.verdict === 'contradicted');
    const unsupported = claims.filter(row => ['fact', 'rule'].includes(row.kind) && row.verdict === 'unsupported');
    const uncovered = (audit.subqueries || []).filter(row => !row.covered);
    const byId = new Map(rows.map(row => [String(row.evidence_id || ''), row]));
    const deterministicOnly = audit.mode === 'deterministic_evidence_fallback';

    const surface = document.createElement('div');
    surface.className = `trust-surface ${meta.tone}`;
    surface.innerHTML = `<section class="trust-overview">
      <div class="trust-overview-head">
        <div><span class="trust-state-dot" aria-hidden="true"></span><b>${escapeHtml(meta.label)}</b></div>
        <small>不是模型置信度</small>
      </div>
      <p>${escapeHtml(deterministicOnly ? '本轮只完成确定性业务证据门禁，逐声明核验未运行，因此不会把当前状态显示成“证据充分”。' : meta.detail)}</p>
      <div class="trust-metrics" aria-label="证据核验指标">
        <div><span>问题覆盖</span><b>${percent(audit.query_coverage)}</b></div>
        <div><span>事实已支持</span><b>${Number(audit.supported_factual_claim_count || 0)}/${Number(audit.factual_claim_count || 0)}</b></div>
        <div><span>直接反证</span><b>${Number(audit.contradicted_factual_claim_count || 0)}</b></div>
      </div>
    </section>
    ${claimGroup('已支持', supported, 'supported', byId)}
    ${claimGroup('存在反证', contradicted, 'contradicted', byId)}
    ${claimGroup('缺少直接支持', unsupported, 'unsupported', byId)}
    ${uncovered.length ? `<section class="trust-gap"><header><b>尚未覆盖的问题</b><span>${uncovered.length}</span></header>${uncovered.slice(0, 6).map(row => `<p>${escapeHtml(row.text || '')}${row.reason ? `<small>${escapeHtml(row.reason)}</small>` : ''}</p>`).join('')}</section>` : ''}`;
    box.prepend(surface);
  }

  window.addEventListener('ecomevo:evidence-rendered', event => renderSnapshot(event.detail || {}));
  if (window.ecomevoTrustSnapshot) renderSnapshot(window.ecomevoTrustSnapshot);
})();
