(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const SCENES = {
    product_governance: '商品治理',
    merchant_review: '商家审核',
    aftersales: '售后判责',
    risk_review: '风险核查',
    content_audit: '内容审核',
  };

  function installStylesheet() {
    if (document.querySelector('link[data-ecomevo-ops-intelligence]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/ops-intelligence.css';
    link.dataset.ecomevoOpsIntelligence = '1';
    document.head.appendChild(link);
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function fmtTime(value) {
    const n = Number(value || 0);
    if (!n) return '暂无时间';
    const ms = n > 10_000_000_000 ? n : n * 1000;
    try {
      return new Date(ms).toLocaleString('zh-CN', {
        month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
      });
    } catch (_) {
      return '暂无时间';
    }
  }

  async function readJson(url) {
    const response = await fetch(url, { headers: { accept: 'application/json' } });
    if (!response.ok) throw new Error(`${url} ${response.status}`);
    return response.json();
  }

  function runtimeSnapshot(runtime) {
    const plugins = Array.isArray(runtime?.plugins) ? runtime.plugins : [];
    const active = plugins.filter(plugin => plugin?.enabled && plugin?.loaded);
    const healthy = active.filter(plugin => plugin?.contract_valid);
    const blocked = active.length - healthy.length;
    return {
      status: active.length > 0 && blocked === 0 ? '契约通过' : active.length ? '需要检查' : '等待实例',
      statusClass: active.length > 0 && blocked === 0 ? 'ok' : blocked ? 'warn' : 'neutral',
      active: active.length,
      healthy: healthy.length,
      blocked,
    };
  }

  function metric(label, value, detail, tone = '') {
    const item = el('div', `ops-metric ${tone}`.trim());
    item.append(el('small', '', label), el('strong', '', value), el('span', '', detail));
    return item;
  }

  function navigateConversation(id) {
    if (!id) return;
    const url = new URL(location.href);
    url.searchParams.set('conversation', id);
    url.searchParams.delete('tour');
    location.assign(url.toString());
  }

  function renderOverview(conversations = [], runtime = null) {
    const copy = document.querySelector('.welcome-copy');
    const kicker = copy?.querySelector('.welcome-kicker');
    if (!copy || !kicker) return;

    const existing = copy.querySelector('.ops-overview');
    if (existing) existing.remove();

    const snapshot = runtimeSnapshot(runtime);
    const rows = Array.isArray(conversations) ? conversations.slice(0, 4) : [];
    const overview = el('section', 'ops-overview');
    overview.setAttribute('aria-label', '运行概览');

    const head = el('div', 'ops-overview-head');
    const titleWrap = el('div');
    titleWrap.append(el('small', '', 'OPERATIONS SNAPSHOT'), el('b', '', '运行概览'));
    head.append(titleWrap, el('span', 'ops-live-label', '真实运行数据'));

    const metrics = el('div', 'ops-metrics');
    metrics.append(
      metric('RUNTIME', snapshot.status, snapshot.blocked ? `${snapshot.blocked} 个实例需检查` : `${snapshot.healthy}/${snapshot.active || 0} 契约有效`, snapshot.statusClass),
      metric('ACTIVE PLUGINS', snapshot.active, '当前进入执行图的实例'),
      metric('RECENT', rows.length, rows.length ? '首页展示的可恢复任务' : '暂无历史任务'),
      metric('AUTHORITY', '人工确认', '真实业务动作不会静默执行', 'authority'),
    );

    overview.append(head, metrics);

    if (rows.length) {
      const recent = el('div', 'ops-recent');
      const recentHead = el('div', 'ops-recent-head');
      recentHead.append(el('b', '', '最近运行'), el('small', '', '继续一个耐久任务'));
      const list = el('div', 'ops-recent-list');
      rows.forEach((row, index) => {
        const button = el('button', 'ops-recent-item');
        button.type = 'button';
        const indexNode = el('span', 'ops-recent-index', String(index + 1).padStart(2, '0'));
        const body = el('div', 'ops-recent-copy');
        body.append(el('b', '', row?.title || '业务任务'));
        body.append(el('small', '', `${SCENES[row?.scene] || '业务任务'} · ${fmtTime(row?.updated_at)}`));
        button.append(indexNode, body, el('span', 'ops-recent-open', '打开'));
        button.addEventListener('click', () => navigateConversation(row?.id));
        list.appendChild(button);
      });
      recent.append(recentHead, list);
      overview.appendChild(recent);
    }

    kicker.before(overview);
  }

  async function refreshOverview() {
    const [conversationsResult, runtimeResult] = await Promise.allSettled([
      readJson('/api/conversations?limit=4'),
      readJson('/api/runtime'),
    ]);
    const conversations = conversationsResult.status === 'fulfilled' && Array.isArray(conversationsResult.value)
      ? conversationsResult.value : [];
    const runtime = runtimeResult.status === 'fulfilled' ? runtimeResult.value : null;
    renderOverview(conversations, runtime);
  }

  function traceIdFromCard(card) {
    const image = card.querySelector('.evidence-media img');
    const src = image?.getAttribute('src') || '';
    const match = src.match(/\/api\/assets\/([^/]+)/);
    if (!match) return null;
    try { return decodeURIComponent(match[1]); } catch (_) { return match[1]; }
  }

  function auditCell(label, value, cls = '') {
    const cell = el('span', `evidence-audit-cell ${cls}`.trim());
    cell.append(el('small', '', label), el('b', '', value));
    return cell;
  }

  function decorateEvidence() {
    const list = byId('evidenceList');
    if (!list) return;
    list.querySelectorAll('.evidence-card').forEach(card => {
      if (card.dataset.auditEnhanced === '1') return;
      card.dataset.auditEnhanced = '1';
      const source = card.querySelector('.evidence-top em')?.textContent?.trim() || '核对结果';
      const traceId = traceIdFromCard(card);
      const original = source.includes('业务资料');
      card.classList.toggle('evidence-original', original);
      card.classList.toggle('evidence-derived', !original);

      const audit = el('div', 'evidence-audit-row');
      audit.append(
        auditCell('SOURCE', source, original ? 'original' : 'derived'),
        auditCell('PROVENANCE', original ? '原始资料' : '运行派生', original ? 'original' : 'derived'),
        auditCell('TRACE', traceId ? `…${traceId.slice(-8)}` : (original ? '已关联资料' : '当前结论')),
      );
      card.appendChild(audit);
    });
  }

  function decorateActions() {
    const list = byId('actionList');
    if (!list) return;
    list.querySelectorAll('.action-card').forEach(card => {
      if (card.dataset.authorityEnhanced === '1') return;
      card.dataset.authorityEnhanced = '1';
      const chips = [...card.querySelectorAll('.risk-chip')].map(node => node.textContent.trim()).filter(Boolean);
      const status = card.querySelector('.action-status')?.textContent?.trim() || '待处理';
      const requiresConfirm = Boolean(card.querySelector('[data-decision="approve"]'));
      const changesState = chips.some(text => text.includes('改变业务状态'));

      const boundary = el('div', 'action-authority-row');
      boundary.append(
        auditCell('STATUS', status),
        auditCell('AUTHORITY', requiresConfirm ? '人工确认' : '已决策', requiresConfirm ? 'authority' : ''),
        auditCell('IMPACT', changesState ? '业务状态变更' : '记录级影响', changesState ? 'impact' : ''),
      );
      const buttons = card.querySelector('.action-buttons');
      if (buttons) card.insertBefore(boundary, buttons);
      else card.appendChild(boundary);
    });
  }

  function decorateProgress() {
    const list = byId('progressList');
    if (!list) return;
    const panel = byId('panel-progress');
    if (!panel) return;
    let trace = panel.querySelector('.trace-ledger-head');
    if (!trace) {
      trace = el('div', 'trace-ledger-head');
      list.before(trace);
    }
    const count = list.querySelectorAll('.progress-item').length;
    const done = list.querySelectorAll('.progress-item.done').length;
    const sync = byId('taskConnection')?.textContent?.trim() || '同步状态未知';
    const signature = `${done}/${count || 0}|${sync}`;
    if (trace.dataset.signature === signature) return;
    trace.dataset.signature = signature;
    trace.replaceChildren(
      el('div', 'trace-ledger-title'),
      el('span', 'trace-ledger-meta', `${done}/${count || 0} steps · ${sync}`),
    );
    const title = trace.querySelector('.trace-ledger-title');
    title.append(el('small', '', 'EVENT TRACE'), el('b', '', '执行轨迹'));
  }

  function observeRightPlane() {
    const rightbar = byId('rightbar');
    if (!rightbar) return;
    const run = () => {
      decorateEvidence();
      decorateActions();
      decorateProgress();
    };
    run();
    let queued = false;
    const observer = new MutationObserver(() => {
      if (queued) return;
      queued = true;
      queueMicrotask(() => {
        queued = false;
        run();
      });
    });
    observer.observe(rightbar, { childList: true, subtree: true, characterData: true });
  }

  function boot() {
    installStylesheet();
    observeRightPlane();
    refreshOverview().catch(() => renderOverview([], null));

    const conversations = byId('conversationList');
    if (conversations) {
      let timer = null;
      new MutationObserver(() => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          if (!byId('welcomePanel')?.hidden) refreshOverview().catch(() => {});
        }, 120);
      }).observe(conversations, { childList: true, subtree: true });
    }
  }

  installStylesheet();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
