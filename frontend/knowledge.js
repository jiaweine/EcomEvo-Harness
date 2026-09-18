(() => {
  'use strict';
  const API = '/api/runtime/knowledge';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>\"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[ch]));
  const state = {catalog: null, selected: null, mode: 'source'};
  const domainLabel = {
    product_governance:'商品治理', merchant_review:'商家审核', aftersales:'售后判责',
    risk_review:'风险核查', content_audit:'内容审核', general:'通用',
  };
  const stateLabel = {draft:'Draft', reviewed:'Reviewed', published:'Published', superseded:'Superseded', retired:'Retired'};
  const freshLabel = {current:'当前有效', review_due:'待复审', expired:'已过期', future:'尚未生效'};
  let toastTimer;

  async function api(url, options={}) {
    const response = await fetch(url, {
      credentials:'same-origin',
      headers:{'content-type':'application/json', 'accept':'application/json', ...(options.headers || {})},
      ...options,
    });
    let body = null;
    try { body = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(body?.detail || `请求失败 ${response.status}`);
    return body;
  }

  function toast(message) {
    const node = $('toast');
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('show'), 2600);
  }

  function timeText(value) {
    if (value == null) return '—';
    const d = new Date(Number(value) * 1000);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN');
  }

  function unixValue(id) {
    const value = $(id).value;
    if (!value) return null;
    const ms = new Date(value).getTime();
    return Number.isFinite(ms) ? ms / 1000 : null;
  }

  function renderHierarchy() {
    const rows = state.catalog?.source_hierarchy || [];
    $('kgHierarchy').innerHTML = rows.map(row => `
      <article class="kg-tier ${row.authorable_here ? '' : 'locked'}">
        <b>${esc(row.tier)} · ${esc(row.label)}</b><em>${row.authorable_here ? '可治理' : '锁定来源'}</em>
        <p>${esc(row.note)}</p>
      </article>`).join('');
  }

  function renderSources() {
    const items = state.catalog?.items || [];
    $('sourceList').innerHTML = items.length ? items.map(item => `
      <button type="button" class="kg-source ${state.selected?.source_id === item.source_id ? 'active' : ''}" data-source="${item.source_id}">
        <div class="kg-source-top"><b>${esc(item.name)}</b><em>${esc(item.source_tier)}</em></div>
        <p>${esc(domainLabel[item.domain] || item.domain)} · ${item.current_published_version_id ? '有 Published 版本' : '未发布'}</p>
      </button>`).join('') : '<div class="kg-empty"><b>还没有知识源</b><p>创建第一个 S2 或 S4 来源。</p></div>';
    document.querySelectorAll('[data-source]').forEach(button => {
      button.onclick = () => selectSource(button.dataset.source);
    });
  }

  async function loadCatalog() {
    state.catalog = await api(API);
    renderHierarchy();
    renderSources();
    if (state.selected?.source_id) {
      const still = (state.catalog.items || []).some(item => item.source_id === state.selected.source_id);
      if (still) await selectSource(state.selected.source_id, false);
    }
  }

  async function selectSource(sourceId, rerenderList=true) {
    state.selected = await api(`${API}/sources/${encodeURIComponent(sourceId)}`);
    $('emptyState').hidden = true;
    $('sourceDetail').hidden = false;
    $('detailTier').textContent = state.selected.source_tier;
    $('detailDomain').textContent = domainLabel[state.selected.domain] || state.selected.domain;
    $('detailName').textContent = state.selected.name;
    $('detailMeta').textContent = `Owner: ${state.selected.owner} · ${state.selected.jurisdiction || '未限定地区'} · ${(state.selected.tags || []).join(' / ') || '无标签'}`;
    $('detailDescription').textContent = state.selected.description || '无来源说明';
    renderVersions();
    if (rerenderList) renderSources();
  }

  function versionActions(version) {
    const buttons = [];
    if (version.state === 'draft') buttons.push(`<button data-action="review" data-version="${version.version_id}">标记 Reviewed</button>`);
    if (version.state === 'reviewed') {
      buttons.push(`<button class="primary-action" data-action="publish" data-version="${version.version_id}">发布到目录</button>`);
      buttons.push(`<button data-action="retire" data-version="${version.version_id}">Retire</button>`);
    }
    if (version.state === 'published') buttons.push(`<button data-action="retire" data-version="${version.version_id}">Retire</button>`);
    buttons.push(`<button data-action="projection" data-version="${version.version_id}">查看 Runtime projection</button>`);
    return buttons.join('');
  }

  function renderVersions() {
    const versions = state.selected?.versions || [];
    $('versionList').innerHTML = versions.map(version => `
      <article class="kg-version">
        <div class="kg-version-head"><h3>v${Number(version.version || 0)} · ${esc(version.title)}</h3><span>${esc(stateLabel[version.state] || version.state)}</span></div>
        <div class="kg-version-meta">
          <span>${esc(freshLabel[version.freshness] || version.freshness)}</span>
          <span>复审：${timeText(version.review_due_at)}</span>
          <span>hash ${esc(String(version.content_hash || '').slice(0,12))}…</span>
        </div>
        <p>${esc(version.content_text || '')}</p>
        <div class="kg-version-actions">${versionActions(version)}</div>
        <div class="kg-projection" id="projection-${version.version_id.replace(/[^A-Za-z0-9_-]/g,'_')}" hidden></div>
      </article>`).join('');
    document.querySelectorAll('[data-action]').forEach(button => {
      button.onclick = () => handleVersionAction(button.dataset.action, button.dataset.version);
    });
  }

  async function handleVersionAction(action, versionId) {
    try {
      if (action === 'projection') {
        const projection = await api(`${API}/versions/${encodeURIComponent(versionId)}/retrieval-projection`);
        const id = `projection-${versionId.replace(/[^A-Za-z0-9_-]/g,'_')}`;
        const node = $(id);
        node.hidden = false;
        node.textContent = `${projection.runtime_projection_status} · eligible_for_runtime_evidence=${projection.authority.eligible_for_runtime_evidence} · ${projection.content_hash}`;
        return;
      }
      const endpoint = action === 'review' ? 'review' : action === 'publish' ? 'publish' : 'retire';
      const result = await api(`${API}/versions/${encodeURIComponent(versionId)}/${endpoint}`, {
        method:'POST',
        body:JSON.stringify({note: action === 'publish' ? 'Admin catalog publication' : ''}),
      });
      toast(`${result.version_id} → ${result.state}`);
      await loadCatalog();
    } catch (error) { toast(error.message); }
  }

  function resetEditor() {
    $('editorForm').reset();
    $('sourceFields').hidden = state.mode === 'version';
    $('editorKicker').textContent = state.mode === 'version' ? 'Immutable version' : 'New source';
    $('editorTitle').textContent = state.mode === 'version' ? `为 ${state.selected?.name || ''} 创建新版本` : '新建知识源';
    $('editorSubmit').textContent = state.mode === 'version' ? '创建新版本' : '创建知识源';
  }

  function openEditor(mode) {
    state.mode = mode;
    resetEditor();
    $('editorModal').hidden = false;
    requestAnimationFrame(() => (mode === 'source' ? $('sourceName') : $('versionTitle')).focus());
  }

  function closeEditor() { $('editorModal').hidden = true; }

  async function saveEditor(event) {
    event.preventDefault();
    const version = {
      title:$('versionTitle').value.trim(),
      content_text:$('contentText').value.trim(),
      provenance:$('versionProvenance').value.trim(),
      effective_from:unixValue('effectiveFrom'),
      effective_until:unixValue('effectiveUntil'),
      review_due_at:unixValue('reviewDue'),
    };
    try {
      let result;
      if (state.mode === 'version') {
        result = await api(`${API}/sources/${encodeURIComponent(state.selected.source_id)}/versions`, {
          method:'POST', body:JSON.stringify(version),
        });
      } else {
        const payload = {
          name:$('sourceName').value.trim(),
          source_tier:$('sourceTier').value,
          domain:$('sourceDomain').value,
          description:$('sourceDescription').value.trim(),
          owner:$('sourceOwner').value.trim(),
          jurisdiction:$('sourceJurisdiction').value.trim(),
          tags:$('sourceTags').value.split(',').map(x => x.trim()).filter(Boolean),
          version,
        };
        result = await api(`${API}/sources`, {method:'POST', body:JSON.stringify(payload)});
      }
      closeEditor();
      toast(`${result.source_id || result.version_id} 已创建`);
      await loadCatalog();
      await selectSource(result.source_id || state.selected.source_id);
    } catch (error) { toast(error.message); }
  }

  async function search(event) {
    event.preventDefault();
    const q = $('searchInput').value.trim();
    if (!q) return;
    try {
      const result = await api(`${API}/search?q=${encodeURIComponent(q)}`);
      $('searchResults').innerHTML = (result.items || []).length ? result.items.map(item => `
        <article class="kg-hit"><b>${esc(item.source_tier)} · ${esc(item.name)}</b><small>${esc(item.title)} · ${esc(freshLabel[item.freshness] || item.freshness)}</small><p>${esc(item.excerpt)}</p></article>`).join('') : '<div class="kg-empty"><b>没有命中</b><p>只有 Published 且当前有效的目录版本参与预览。</p></div>';
    } catch (error) { toast(error.message); }
  }

  $('newSourceBtn').onclick = () => openEditor('source');
  $('newVersionBtn').onclick = () => openEditor('version');
  $('editorClose').onclick = closeEditor;
  $('editorCancel').onclick = closeEditor;
  $('editorForm').addEventListener('submit', saveEditor);
  $('searchForm').addEventListener('submit', search);
  $('editorModal').addEventListener('click', event => { if (event.target === $('editorModal')) closeEditor(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !$('editorModal').hidden) closeEditor(); });

  loadCatalog().catch(error => toast(error.message));
})();
