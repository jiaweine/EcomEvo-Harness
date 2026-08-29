(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const STORAGE_KEY = 'ecomevo.ai-provider';
  const GROUPS = [
    { key: 'recommended', label: '推荐', note: '让 EcomEvo 根据任务自动选择' },
    { key: 'global', label: '全球 AI', note: 'OpenAI · Anthropic · Google' },
    { key: 'china', label: '国内 AI', note: '主流中文与企业模型服务' },
    { key: 'private', label: '本地与私有化', note: '数据边界由您控制' },
  ];
  const META = {
    auto: { group: 'recommended', mark: '✦', order: 0 },
    openai: { group: 'global', mark: 'OA', order: 10 },
    anthropic: { group: 'global', mark: 'CL', order: 20 },
    gemini: { group: 'global', mark: 'G', order: 30 },
    deepseek: { group: 'china', mark: 'DS', order: 40 },
    qwen: { group: 'china', mark: 'QW', order: 50 },
    doubao: { group: 'china', mark: '豆', order: 60 },
    kimi: { group: 'china', mark: 'K', order: 70 },
    zhipu: { group: 'china', mark: 'GL', order: 80 },
    hunyuan: { group: 'china', mark: 'HY', order: 90 },
    qianfan: { group: 'china', mark: '千', order: 100 },
    open_model: { group: 'private', mark: 'OS', order: 110 },
    custom: { group: 'private', mark: '私', order: 120 },
    demo: { group: 'private', mark: '本', order: 130 },
  };

  let providers = [];
  let selectedKey = localStorage.getItem(STORAGE_KEY) || 'auto';
  let searchQuery = '';

  function escapeHtml(value = '') {
    return String(value).replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);
  }

  function providerMeta(provider) {
    return META[provider?.key] || { group: 'private', mark: (provider?.name || 'AI').slice(0, 2), order: 999 };
  }

  function isSelectable(provider) {
    return Boolean(provider && (provider.key === 'auto' || provider.key === 'demo' || provider.configured));
  }

  function providerByKey(key) {
    return providers.find(provider => provider.key === key) || null;
  }

  function fallbackProvider() {
    return providerByKey('auto') || providers.find(isSelectable) || null;
  }

  function activeProvider() {
    const requested = providerByKey(selectedKey);
    if (requested && isSelectable(requested)) return requested;
    const fallback = fallbackProvider();
    if (fallback) selectedKey = fallback.key;
    return fallback;
  }

  function caps(provider) {
    if (!provider) return [];
    const values = [];
    if (provider.multimodal) values.push('图片');
    if (provider.supports_audio) values.push('音频');
    if (provider.supports_document) values.push('文档');
    if (!values.length) values.push('文本');
    return values;
  }

  function modelLabel(provider) {
    if (!provider) return '';
    if (provider.key === 'auto') return '智能路由';
    if (provider.key === 'demo') return '本地受控';
    if (provider.model) return provider.model;
    return provider.configured ? provider.vendor || '已配置' : '未配置模型';
  }

  function showToast(message) {
    const toast = byId('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => toast.classList.remove('show'), 2600);
  }

  function loadProvidersViaXHR() {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('GET', '/api/providers', true);
      xhr.setRequestHeader('accept', 'application/json');
      xhr.onload = () => {
        if (xhr.status < 200 || xhr.status >= 300) return reject(new Error(`providers ${xhr.status}`));
        try {
          const rows = JSON.parse(xhr.responseText);
          resolve(Array.isArray(rows) ? rows : []);
        } catch (error) {
          reject(error);
        }
      };
      xhr.onerror = () => reject(new Error('provider network error'));
      xhr.send();
    });
  }

  async function waitForNativeSelect(timeoutMs = 5000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const select = byId('providerSelect');
      if (select && select.options.length > 1) return select;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    return byId('providerSelect');
  }

  function updateTrigger() {
    const provider = activeProvider();
    const trigger = byId('providerBtn');
    if (!provider || !trigger) return;
    const meta = providerMeta(provider);
    const mark = byId('providerTriggerMark');
    const name = byId('providerTriggerName');
    const model = byId('providerTriggerModel');
    if (mark) mark.textContent = meta.mark;
    if (name) name.textContent = provider.name;
    if (model) model.textContent = modelLabel(provider);
    trigger.setAttribute('aria-label', `选择 AI 服务，当前 ${provider.name}${provider.model ? ` ${provider.model}` : ''}`);
    trigger.title = `${provider.name}${provider.model ? ` · ${provider.model}` : ''}`;
  }

  function syncNativeSelect() {
    const select = byId('providerSelect');
    const provider = activeProvider();
    if (!select || !provider) return;
    if ([...select.options].some(option => option.value === provider.key)) {
      select.value = provider.key;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  function chooseProvider(provider) {
    if (!provider) return;
    if (!isSelectable(provider)) {
      showToast(`${provider.name} 尚未配置 API Key 和模型`);
      return;
    }
    selectedKey = provider.key;
    localStorage.setItem(STORAGE_KEY, selectedKey);
    syncNativeSelect();
    updateTrigger();
    renderPicker();
    byId('providerModal')?.querySelector('.modal-close')?.click();
  }

  function matchesSearch(provider) {
    if (!searchQuery) return true;
    const haystack = [provider.key, provider.name, provider.vendor, provider.model, provider.note, ...caps(provider)]
      .filter(Boolean).join(' ').toLowerCase();
    return haystack.includes(searchQuery.toLowerCase());
  }

  function cardMarkup(provider) {
    const meta = providerMeta(provider);
    const selected = provider.key === activeProvider()?.key;
    const selectable = isSelectable(provider);
    const statusText = selected ? '当前使用' : provider.configured || provider.key === 'auto' || provider.key === 'demo' ? '可用' : '未配置';
    const statusClass = selected ? 'selected' : selectable ? 'ready' : '';
    const capabilityMarkup = caps(provider).map(cap => `<span>${escapeHtml(cap)}</span>`).join('');
    return `<button type="button" class="ai-provider-card" data-provider-key="${escapeHtml(provider.key)}" data-selected="${selected}" data-selectable="${selectable}" aria-pressed="${selected}"${!selectable ? ` aria-label="${escapeHtml(provider.name)}，未配置"` : ''}>
      <span class="ai-provider-card-mark" aria-hidden="true">${escapeHtml(meta.mark)}</span>
      <span class="ai-provider-card-copy">
        <span class="ai-provider-card-title"><b>${escapeHtml(provider.name)}</b><small>${escapeHtml(provider.vendor || '')}</small></span>
        <span class="ai-provider-card-model">${escapeHtml(modelLabel(provider))}</span>
        <span class="ai-provider-card-caps">${capabilityMarkup}</span>
      </span>
      <span class="ai-provider-state ${statusClass}">${statusText}</span>
    </button>`;
  }

  function renderPicker() {
    const grid = byId('providerGrid');
    if (!grid || !providers.length) return;
    const rows = providers.filter(matchesSearch).sort((a, b) => providerMeta(a).order - providerMeta(b).order);
    const sections = GROUPS.map(group => {
      const groupRows = rows.filter(provider => providerMeta(provider).group === group.key);
      if (!groupRows.length) return '';
      return `<section class="ai-provider-section" data-provider-group="${group.key}">
        <div class="ai-provider-section-head"><b>${group.label}</b><span>${group.note}</span></div>
        <div class="ai-provider-cards">${groupRows.map(cardMarkup).join('')}</div>
      </section>`;
    }).join('');
    grid.innerHTML = `<div class="ai-provider-picker">
      <div class="ai-provider-toolbar">
        <input class="ai-provider-search" id="providerSearch" type="search" autocomplete="off" placeholder="搜索 OpenAI、Claude、Kimi、豆包…" value="${escapeHtml(searchQuery)}" aria-label="搜索 AI 服务" />
        <span class="ai-provider-toolbar-note">已配置的服务可直接切换</span>
      </div>
      <div class="ai-provider-sections">${sections || '<div class="ai-provider-empty">没有匹配的 AI 服务</div>'}</div>
    </div>`;

    grid.querySelectorAll('.ai-provider-card').forEach(card => {
      card.addEventListener('click', () => chooseProvider(providerByKey(card.dataset.providerKey)));
    });
    const search = byId('providerSearch');
    search?.addEventListener('input', event => {
      searchQuery = event.target.value.trim();
      renderPicker();
      requestAnimationFrame(() => {
        const next = byId('providerSearch');
        next?.focus();
        if (next) next.setSelectionRange(next.value.length, next.value.length);
      });
    });
  }

  function installModalFocus() {
    const modal = byId('providerModal');
    if (!modal) return;
    // app.js has a deliberately minimal single-button trap. Replace it with a
    // real trap now that the provider dialog contains interactive model cards.
    modal.onkeydown = event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        modal.querySelector('.modal-close')?.click();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusables = [...modal.querySelectorAll('button:not([disabled]),input:not([disabled])')]
        .filter(node => node.offsetParent !== null);
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
  }

  function installOpenRefresh() {
    const trigger = byId('providerBtn');
    if (!trigger) return;
    trigger.addEventListener('click', () => {
      searchQuery = '';
      renderPicker();
      requestAnimationFrame(() => byId('providerSearch')?.focus());
    });
  }

  async function bootProviderMarketplace() {
    try {
      const [rows, select] = await Promise.all([loadProvidersViaXHR(), waitForNativeSelect()]);
      providers = rows;
      if (!providers.length) return;
      activeProvider();
      if (select) {
        syncNativeSelect();
        select.addEventListener('change', () => {
          const candidate = providerByKey(select.value);
          if (!candidate || !isSelectable(candidate)) return;
          selectedKey = candidate.key;
          localStorage.setItem(STORAGE_KEY, selectedKey);
          updateTrigger();
        });
      }
      updateTrigger();
      renderPicker();
      installModalFocus();
      installOpenRefresh();
    } catch (error) {
      console.error('provider marketplace failed to initialize', error);
      updateTrigger();
    }
  }

  bootProviderMarketplace();
})();
