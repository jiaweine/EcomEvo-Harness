(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
  const planeFor = plugin => ({
    state: 'state', model: 'cognition', planner: 'cognition', agent: 'cognition',
    memory: 'cognition', skill: 'cognition', tool: 'execution', sandbox: 'safety',
    verifier: 'safety',
  })[plugin?.kind] || 'cognition';
  const labels = {
    all: '全部', state: '基础服务', cognition: '智能处理', execution: '外部连接', safety: '安全保护',
  };
  const lanes = [
    { key: 'state', index: '01', title: '基础服务', copy: '保存当前办理状态和重要记录' },
    { key: 'cognition', index: '02', title: '智能处理', copy: '帮助整理信息并生成处理建议' },
    { key: 'execution', index: '03', title: '外部连接', copy: '连接业务资料和已配置服务' },
    { key: 'safety', index: '04', title: '安全保护', copy: '保护重要操作并核对处理结果' },
  ];
  const serviceNames = {
    state: '办理状态服务',
    model: '信息处理服务',
    planner: '处理流程服务',
    agent: '业务协助服务',
    memory: '历史信息服务',
    skill: '业务能力服务',
    tool: '外部信息服务',
    sandbox: '安全保护服务',
    verifier: '结果核对服务',
  };
  const serviceDescriptions = {
    state: '保存本次办理的进度和重要记录',
    model: '理解提交内容并整理关键信息',
    planner: '根据当前情况安排后续处理步骤',
    agent: '协助完成当前业务处理',
    memory: '保留本次办理中已经确认的信息',
    skill: '提供当前业务需要的处理能力',
    tool: '读取已连接的业务资料和服务',
    sandbox: '限制高风险操作并保护业务状态',
    verifier: '在给出结果前再次核对关键信息',
  };
  let returnFocus = null;
  let plugins = [];
  let currentFilter = 'all';

  function pluginState(plugin) {
    if (!plugin.enabled || !plugin.loaded) return { key: 'standby', label: '未启用' };
    if (!plugin.contract_valid) return { key: 'blocked', label: '需检查' };
    return { key: 'healthy', label: '正常' };
  }

  function sourceLabel(source) {
    const value = String(source || 'builtin');
    if (value === 'builtin') return '内置';
    if (value === 'configured') return '已设置';
    if (value === 'injected') return '已连接';
    if (value.startsWith('entry-point:')) return '扩展';
    return '服务';
  }

  function renderSummary(runtime) {
    const loaded = plugins.filter(plugin => plugin.loaded && plugin.enabled);
    const healthy = loaded.filter(plugin => plugin.contract_valid);
    const standby = plugins.filter(plugin => !plugin.loaded || !plugin.enabled);
    const state = loaded.length > 0 && healthy.length === loaded.length ? '服务正常' : '部分服务需检查';
    $('runtimeSummary').innerHTML = `<div class="runtime-health-grid">
      <div><small>整体状态</small><strong>${esc(state)}</strong><span>${healthy.length} 项服务当前可以正常使用</span></div>
      <div><small>已启用</small><strong>${loaded.length}</strong><span>当前使用中的服务</span></div>
      <div><small>可用</small><strong>${healthy.length}/${loaded.length}</strong><span>状态正常的服务</span></div>
      <div><small>可选功能</small><strong>${standby.length}</strong><span>当前未启用的功能</span></div>
    </div>`;
  }

  function renderLanes() {
    $('runtimeLanes').innerHTML = lanes.map(lane => {
      const count = plugins.filter(plugin => planeFor(plugin) === lane.key && plugin.enabled && plugin.loaded).length;
      return `<article class="runtime-lane ${lane.key}"><div class="runtime-lane-head"><span class="runtime-lane-index">${lane.index}</span><span class="runtime-lane-count">${count} 项</span></div><b>${lane.title}</b><p>${lane.copy}</p></article>`;
    }).join('');
  }

  function renderFilters() {
    $('runtimeFilters').innerHTML = ['all', ...lanes.map(lane => lane.key)].map(key => `<button class="runtime-filter ${currentFilter === key ? 'active' : ''}" data-runtime-filter="${key}" aria-pressed="${currentFilter === key}">${labels[key]}</button>`).join('');
    $('runtimeFilters').querySelectorAll('[data-runtime-filter]').forEach(button => {
      button.onclick = () => {
        currentFilter = button.dataset.runtimeFilter;
        renderFilters();
        renderPlugins();
      };
    });
  }

  function renderPlugins() {
    const visible = currentFilter === 'all' ? plugins : plugins.filter(plugin => planeFor(plugin) === currentFilter);
    const seen = new Map();
    $('runtimePluginGrid').innerHTML = visible.length ? visible.map(plugin => {
      const state = pluginState(plugin);
      const kind = String(plugin.kind || 'service');
      const baseName = serviceNames[kind] || '服务功能';
      const occurrence = (seen.get(baseName) || 0) + 1;
      seen.set(baseName, occurrence);
      const totalOfKind = visible.filter(item => String(item.kind || 'service') === kind).length;
      const name = totalOfKind > 1 ? `${baseName} ${occurrence}` : baseName;
      const description = Array.isArray(plugin.contract_missing) && plugin.contract_missing.length
        ? '部分能力暂不可用，请稍后再试'
        : serviceDescriptions[kind] || '支持当前业务办理';

      // Retain escaped internal diagnostics for support tooling and regression
      // checks without rendering engineering vocabulary into the customer UI.
      const internalName = esc(plugin.name || plugin.key);
      const generation = Number(plugin.generation || 0);
      const apiVersion = esc(plugin.api_version || '1');
      const internalSource = esc(plugin.source || 'builtin');

      return `<article class="runtime-plugin" data-plane="${esc(planeFor(plugin))}" data-service-ref="${internalName}" data-generation="${generation}" data-api-version="${apiVersion}" data-service-source="${internalSource}">
        <span class="runtime-plugin-icon">${String(occurrence).padStart(2, '0')}</span>
        <div class="runtime-plugin-main"><div class="runtime-plugin-title"><b>${esc(name)}</b><em>${esc(sourceLabel(plugin.source))}</em></div><p>${esc(description)}</p></div>
        <div class="runtime-plugin-meta"><span class="runtime-plugin-state ${state.key}">${state.label}</span></div>
      </article>`;
    }).join('') : '<div class="runtime-empty">当前分类没有已启用功能。</div>';
  }

  function renderError() {
    $('runtimeSummary').innerHTML = '<div class="runtime-summary-loading"><span>暂时无法读取服务状态，请稍后重试。</span></div>';
    $('runtimeLanes').innerHTML = '';
    $('runtimeFilters').innerHTML = '';
    $('runtimePluginGrid').innerHTML = '<div class="runtime-empty">服务信息暂时不可用。</div>';
  }

  async function loadRuntime() {
    try {
      const response = await fetch('/api/runtime', { headers: { accept: 'application/json' } });
      if (!response.ok) throw new Error(`runtime ${response.status}`);
      const runtime = await response.json();
      plugins = Array.isArray(runtime?.plugins) ? runtime.plugins : [];
      plugins.sort((a, b) => lanes.findIndex(lane => lane.key === planeFor(a)) - lanes.findIndex(lane => lane.key === planeFor(b)) || String(a.key).localeCompare(String(b.key)));
      renderSummary(runtime);
      renderLanes();
      renderFilters();
      renderPlugins();
    } catch (_) {
      renderError();
    }
  }

  function openRuntime() {
    const modal = $('runtimeModal');
    if (!modal || $('productTour')?.hidden === false || $('providerModal')?.hidden === false) return;
    returnFocus = document.activeElement;
    currentFilter = 'all';
    modal.hidden = false;
    document.body.classList.add('runtime-open');
    $('runtimeSummary').innerHTML = '<div class="runtime-summary-loading"><i></i><span>正在读取服务状态</span></div>';
    requestAnimationFrame(() => $('runtimeCloseBtn')?.focus());
    loadRuntime();
  }

  function closeRuntime() {
    const modal = $('runtimeModal');
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    document.body.classList.remove('runtime-open');
    const target = returnFocus;
    returnFocus = null;
    if (target && document.contains(target)) requestAnimationFrame(() => target.focus());
  }

  function trapFocus(event) {
    if (event.key !== 'Tab') return;
    const focusable = [...$('runtimeModal').querySelectorAll('button:not(:disabled)')].filter(node => node.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  function install() {
    const trigger = $('settingsBtn');
    const modal = $('runtimeModal');
    if (!trigger || !modal) return;
    trigger.addEventListener('click', event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      openRuntime();
    }, true);
    $('runtimeCloseBtn').onclick = closeRuntime;
    modal.onclick = event => { if (event.target === modal) closeRuntime(); };
    modal.onkeydown = trapFocus;
    window.addEventListener('keydown', event => { if (event.key === 'Escape' && !modal.hidden) closeRuntime(); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();
})();