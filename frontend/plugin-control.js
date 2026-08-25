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
    all: '全部', state: '状态层', cognition: '认知层', execution: '连接层', safety: '安全层',
  };
  const lanes = [
    { key: 'state', index: '01', title: '任务状态', copy: '目标、事件与可恢复事实' },
    { key: 'cognition', index: '02', title: '自主决策', copy: '规划、记忆、委派与演进' },
    { key: 'execution', index: '03', title: '工具连接', copy: '目录、并行执行与企业连接' },
    { key: 'safety', index: '04', title: '安全边界', copy: '操作沙箱与独立结果复核' },
  ];
  let returnFocus = null;
  let plugins = [];
  let currentFilter = 'all';

  function pluginState(plugin) {
    if (!plugin.enabled || !plugin.loaded) return { key: 'standby', label: 'STANDBY' };
    if (!plugin.contract_valid) return { key: 'blocked', label: 'BLOCKED' };
    return { key: 'healthy', label: 'HEALTHY' };
  }

  function sourceLabel(source) {
    const value = String(source || 'builtin');
    if (value === 'builtin') return 'BUILT-IN';
    if (value === 'configured') return 'CONFIG';
    if (value === 'injected') return 'INJECTED';
    if (value.startsWith('entry-point:')) return 'PACKAGE';
    return 'RUNTIME';
  }

  function renderSummary(runtime) {
    const loaded = plugins.filter(plugin => plugin.loaded && plugin.enabled);
    const healthy = loaded.filter(plugin => plugin.contract_valid);
    const standby = plugins.filter(plugin => !plugin.loaded || !plugin.enabled);
    const generation = Math.max(0, ...plugins.map(plugin => Number(plugin.generation || 0)));
    const state = loaded.length > 0 && healthy.length === loaded.length ? '契约全部通过' : '需要检查';
    $('runtimeSummary').innerHTML = `<div class="runtime-health-grid">
      <div><small>RUNTIME HEALTH</small><strong>${esc(state)}</strong><span>${healthy.length} 个实例已验证并进入执行图</span></div>
      <div><small>ACTIVE</small><strong>${loaded.length}</strong><span>当前启用实例</span></div>
      <div><small>CONTRACT</small><strong>${healthy.length}/${loaded.length}</strong><span>能力契约有效</span></div>
      <div><small>GENERATION</small><strong>G${generation}</strong><span>${standby.length} 个可选槽位待命</span></div>
    </div>`;
  }

  function renderLanes() {
    $('runtimeLanes').innerHTML = lanes.map(lane => {
      const count = plugins.filter(plugin => planeFor(plugin) === lane.key && plugin.enabled && plugin.loaded).length;
      return `<article class="runtime-lane ${lane.key}"><div class="runtime-lane-head"><span class="runtime-lane-index">${lane.index}</span><span class="runtime-lane-count">${count} LIVE</span></div><b>${lane.title}</b><p>${lane.copy}</p></article>`;
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
      const kind = String(plugin.kind || 'plugin').toUpperCase().slice(0, 4);
      const missing = Array.isArray(plugin.contract_missing) && plugin.contract_missing.length ? `缺少 ${plugin.contract_missing.join('、')}` : plugin.description || plugin.key;
      return `<article class="runtime-plugin" data-plane="${esc(planeFor(plugin))}">
        <span class="runtime-plugin-icon">${esc(kind)}</span>
        <div class="runtime-plugin-main"><div class="runtime-plugin-title"><b>${esc(plugin.name || plugin.key)}</b><em>${esc(sourceLabel(plugin.source))}</em></div><p title="${esc(missing)}">${esc(missing)}</p></div>
        <div class="runtime-plugin-meta"><span class="runtime-plugin-state ${state.key}">${state.label}</span><small>G${Number(plugin.generation || 0)} · API ${esc(plugin.api_version || '1')}</small></div>
      </article>`;
    }).join('') : '<div class="runtime-empty">当前层级没有已注册插件。</div>';
  }

  function renderError() {
    $('runtimeSummary').innerHTML = '<div class="runtime-summary-loading"><span>运行时状态暂时不可用，请稍后重试。</span></div>';
    $('runtimeLanes').innerHTML = '';
    $('runtimeFilters').innerHTML = '';
    $('runtimePluginGrid').innerHTML = '<div class="runtime-empty">未能读取插件目录。</div>';
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
    $('runtimeSummary').innerHTML = '<div class="runtime-summary-loading"><i></i><span>正在读取运行时状态</span></div>';
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
    trigger.onclick = openRuntime;
    $('runtimeCloseBtn').onclick = closeRuntime;
    modal.onclick = event => { if (event.target === modal) closeRuntime(); };
    modal.onkeydown = trapFocus;
    window.addEventListener('keydown', event => { if (event.key === 'Escape' && !modal.hidden) closeRuntime(); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();
})();
