(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const SCENES = {
    product_governance: '商品问题',
    merchant_review: '商家认证',
    aftersales: '售后处理',
    risk_review: '风险问题',
    content_audit: '内容问题',
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
      status: active.length > 0 && blocked === 0 ? '服务正常' : active.length ? '部分服务需检查' : '正在准备',
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
    const anchor = copy?.querySelector('.quick-grid');
    if (!copy) return;

    const existing = copy.querySelector('.ops-overview');
    if (existing) existing.remove();

    const snapshot = runtimeSnapshot(runtime);
    const rows = Array.isArray(conversations) ? conversations.slice(0, 4) : [];
    const overview = el('section', 'ops-overview');
    overview.setAttribute('aria-label', '服务概况');

    const head = el('div', 'ops-overview-head');
    const titleWrap = el('div');
    titleWrap.append(el('small', '', '当前服务'), el('b', '', '服务概况'));
    head.append(titleWrap, el('span', 'ops-live-label', '信息会自动保存'));

    const metrics = el('div', 'ops-metrics');
    metrics.append(
      metric('服务状态', snapshot.status, snapshot.blocked ? `${snapshot.blocked} 项服务需要检查` : '当前可以正常使用', snapshot.statusClass),
      metric('最近办理', rows.length, rows.length ? '可随时继续之前的办理' : '还没有办理记录'),
      metric('资料支持', '图片 / 文档 / 表格', '可以继续补充相关资料'),
      metric('重要操作', '先确认再继续', '不会自动改变真实业务状态', 'authority'),
    );

    overview.append(head, metrics);

    if (rows.length) {
      const recent = el('div', 'ops-recent');
      const recentHead = el('div', 'ops-recent-head');
      recentHead.append(el('b', '', '最近办理'), el('small', '', '从上次的位置继续'));
      const list = el('div', 'ops-recent-list');
      rows.forEach((row, index) => {
        const button = el('button', 'ops-recent-item');
        button.type = 'button';
        const indexNode = el('span', 'ops-recent-index', String(index + 1).padStart(2, '0'));
        const body = el('div', 'ops-recent-copy');
        body.append(el('b', '', row?.title || '业务办理'));
        body.append(el('small', '', `${SCENES[row?.scene] || '业务办理'} · ${fmtTime(row?.updated_at)}`));
        button.append(indexNode, body, el('span', 'ops-recent-open', '继续'));
        button.addEventListener('click', () => navigateConversation(row?.id));
        list.appendChild(button);
      });
      recent.append(recentHead, list);
      overview.appendChild(recent);
    }

    if (anchor) anchor.before(overview);
    else copy.appendChild(overview);
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

  function evidenceMeta(label, value, cls = '') {
    const item = el('span', `evidence-meta-item ${cls}`.trim());
    item.dataset.label = label;
    item.append(el('b', '', value));
    return item;
  }

  function summaryStat(label, value, tone = '') {
    const item = el('span', `evidence-summary-stat ${tone}`.trim());
    item.append(el('small', '', label), el('b', '', value));
    return item;
  }

  function decorateEvidence() {
    const list = byId('evidenceList');
    if (!list) return;
    list.querySelectorAll('.evidence-card').forEach(card => {
      if (card.dataset.auditEnhanced === '3') return;
      card.dataset.auditEnhanced = '3';
      card.querySelector('.evidence-audit-row')?.remove();
      card.querySelector('.evidence-provenance-line')?.remove();

      const rawSource = card.querySelector('.evidence-top em')?.textContent?.trim() || '系统整理';
      const traceId = traceIdFromCard(card);
      const original = rawSource.includes('业务资料') || rawSource.includes('您提交');
      const source = original ? '您提交的' : '系统整理';
      card.classList.toggle('evidence-original', original);
      card.classList.toggle('evidence-derived', !original);

      const meta = el('div', 'evidence-provenance-line');
      meta.append(
        evidenceMeta('来源', source, original ? 'original' : 'derived'),
        evidenceMeta('类型', original ? '原始资料' : '整理结果', original ? 'original' : 'derived'),
        evidenceMeta('参考', traceId ? `编号 …${traceId.slice(-8)}` : '本次办理'),
      );
      card.appendChild(meta);
    });
  }

  function decorateEvidenceSummary() {
    const panel = byId('panel-evidence');
    const list = byId('evidenceList');
    const copy = panel?.querySelector('.panel-copy');
    if (!panel || !list || !copy) return;

    const cards = [...list.querySelectorAll('.evidence-card')];
    let summary = panel.querySelector('.evidence-summary');
    if (!cards.length) {
      summary?.remove();
      return;
    }

    const original = cards.filter(card => card.classList.contains('evidence-original')).length;
    const derived = cards.length - original;
    const signature = `${cards.length}|${original}|${derived}`;
    if (summary?.dataset.signature === signature) return;
    if (!summary) {
      summary = el('div', 'evidence-summary');
      copy.after(summary);
    }
    summary.dataset.signature = signature;
    summary.replaceChildren(
      summaryStat('资料总数', cards.length),
      summaryStat('您提交的', original, 'original'),
      summaryStat('系统整理的', derived, 'derived'),
    );
  }

  function setTabCount(tabId, count) {
    const tab = byId(tabId);
    if (!tab) return;
    let badge = tab.querySelector('.ops-tab-count');
    if (!badge) {
      badge = el('i', 'ops-tab-count');
      badge.setAttribute('aria-hidden', 'true');
      tab.appendChild(badge);
    }
    const next = String(Math.max(0, Number(count || 0)));
    if (badge.textContent !== next) badge.textContent = next;
    badge.hidden = Number(count || 0) === 0;
  }

  function updateTabCounts() {
    setTabCount('tab-evidence', byId('evidenceList')?.querySelectorAll('.evidence-card').length || 0);
    setTabCount('tab-assets', byId('assetList')?.querySelectorAll('.asset-card').length || 0);
  }

  function decorateActions() {
    const list = byId('actionList');
    if (!list) return;
    list.querySelectorAll('.action-card').forEach(card => {
      if (card.dataset.authorityEnhanced === '2') return;
      card.dataset.authorityEnhanced = '2';
      card.querySelector('.action-authority-row')?.remove();
      const chips = [...card.querySelectorAll('.risk-chip')].map(node => node.textContent.trim()).filter(Boolean);
      const status = card.querySelector('.action-status')?.textContent?.trim() || '等待确认';
      const requiresConfirm = Boolean(card.querySelector('[data-decision="approve"]'));
      const changesState = chips.some(text => text.includes('改变业务状态'));

      const boundary = el('div', 'action-authority-row');
      boundary.append(
        auditCell('当前状态', status),
        auditCell('需要确认', requiresConfirm ? '请您确认' : '已处理', requiresConfirm ? 'authority' : ''),
        auditCell('可能影响', changesState ? '会改变业务状态' : '仅更新当前记录', changesState ? 'impact' : ''),
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
      el('span', 'trace-ledger-meta', `${done}/${count || 0} 已完成 · ${sync}`),
    );
    const title = trace.querySelector('.trace-ledger-title');
    title.append(el('small', '', '处理记录'), el('b', '', '办理进度'));
  }

  function observeRightPlane() {
    const rightbar = byId('rightbar');
    if (!rightbar) return;
    const run = () => {
      decorateEvidence();
      decorateEvidenceSummary();
      decorateActions();
      decorateProgress();
      updateTabCounts();
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