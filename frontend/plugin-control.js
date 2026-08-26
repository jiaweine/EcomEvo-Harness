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
    $('runtimePluginGrid').innerHTML = visible.length ? visible.map(plugin => {
      const state = pluginState(plugin);
      const kind = String(plugin.kind || 'plugin').toUpperCase().slice(0, 2);
      const missing = Array.isArray(plugin.contract_missing) && plugin.contract_missing.length ? '部分能力暂不可用' : plugin.description || plugin.key;
      return `<article class="runtime-plugin" data-plane="${esc(planeFor(plugin))}">
        <span class="runtime-plugin-icon">${esc(kind)}</span>
        <div class="runtime-plugin-main"><div class="runtime-plugin-title"><b>${esc(plugin.name || '服务功能')}</b><em>${esc(sourceLabel(plugin.source))}</em></div><p title="${esc(missing)}">${esc(missing)}</p></div>
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