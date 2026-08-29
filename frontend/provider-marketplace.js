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
  let voiceRecognition = null;
  let voiceRecorder = null;
  let voiceStream = null;
  let voiceChunks = [];
  let voiceMode = '';
  let preferRecorder = false;

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

  function filesFromClipboard(event) {
    const items = [...(event.clipboardData?.items || [])];
    return items
      .filter(item => item.kind === 'file')
      .map(item => item.getAsFile())
      .filter(Boolean);
  }

  function dispatchFiles(files) {
    const input = byId('fileInput');
    if (!input || !files?.length || typeof DataTransfer === 'undefined') return false;
    const transfer = new DataTransfer();
    files.forEach(file => transfer.items.add(file));
    input.files = transfer.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  function pendingAssetCount() {
    return byId('pendingAssets')?.querySelectorAll('.pending-asset').length || 0;
  }

  function prepareAttachmentOnlySend() {
    const input = byId('messageInput');
    if (!input || input.value.trim() || !pendingAssetCount()) return;
    input.value = '请分析我刚刚添加的资料，提取关键信息，并告诉我下一步建议。';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function closeAttachmentMenu({ restoreFocus = false } = {}) {
    const menu = byId('attachmentMenu');
    const trigger = byId('attachMenuBtn');
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    trigger?.setAttribute('aria-expanded', 'false');
    if (restoreFocus) trigger?.focus();
  }

  function openAttachmentMenu() {
    const menu = byId('attachmentMenu');
    const trigger = byId('attachMenuBtn');
    if (!menu || !trigger) return;
    const nextOpen = menu.hidden;
    menu.hidden = !nextOpen;
    trigger.setAttribute('aria-expanded', String(nextOpen));
    if (nextOpen) requestAnimationFrame(() => menu.querySelector('button')?.focus());
  }

  function setVoiceState(active, mode = '', message = '') {
    const button = byId('voiceInputBtn');
    const live = byId('voiceLiveStatus');
    if (!button) return;
    button.classList.toggle('listening', active);
    button.classList.toggle('recording-audio', active && mode === 'recording');
    button.setAttribute('aria-pressed', String(active));
    button.setAttribute('aria-label', active ? '停止语音输入' : '开始语音输入');
    if (live) {
      live.hidden = !active;
      live.textContent = message || (mode === 'recording' ? '正在录音…' : '正在听写…');
    }
  }

  function stopVoiceCapture() {
    if (voiceRecognition && voiceMode === 'dictation') {
      try { voiceRecognition.stop(); } catch {}
      return;
    }
    if (voiceRecorder && voiceMode === 'recording' && voiceRecorder.state !== 'inactive') {
      try { voiceRecorder.stop(); } catch {}
    }
  }

  async function startAudioRecording() {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      showToast('当前浏览器不支持语音输入，请直接添加音频文件');
      return;
    }
    try {
      voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
      const mimeType = candidates.find(type => MediaRecorder.isTypeSupported?.(type)) || '';
      voiceChunks = [];
      voiceRecorder = mimeType ? new MediaRecorder(voiceStream, { mimeType }) : new MediaRecorder(voiceStream);
      voiceMode = 'recording';
      voiceRecorder.ondataavailable = event => {
        if (event.data?.size) voiceChunks.push(event.data);
      };
      voiceRecorder.onerror = () => {
        showToast('录音没有完成，请检查麦克风权限');
      };
      voiceRecorder.onstop = () => {
        const type = voiceRecorder?.mimeType || mimeType || 'audio/webm';
        const extension = type.includes('ogg') ? 'ogg' : 'webm';
        const blob = new Blob(voiceChunks, { type });
        voiceStream?.getTracks().forEach(track => track.stop());
        voiceStream = null;
        voiceRecorder = null;
        voiceChunks = [];
        voiceMode = '';
        setVoiceState(false);
        if (!blob.size) return;
        const stamp = new Date().toISOString().replace(/[:.]/g, '-');
        const file = new File([blob], `语音-${stamp}.${extension}`, { type });
        if (dispatchFiles([file])) showToast('语音已作为音频资料加入，可补充文字后发送');
        else showToast('录音已完成，但当前浏览器无法加入文件');
      };
      voiceRecorder.start(250);
      setVoiceState(true, 'recording', '正在录音… 再点一次结束');
    } catch (error) {
      voiceStream?.getTracks().forEach(track => track.stop());
      voiceStream = null;
      voiceMode = '';
      setVoiceState(false);
      showToast(error?.name === 'NotAllowedError' ? '需要允许麦克风权限才能语音输入' : '无法打开麦克风');
    }
  }

  function startDictation() {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition || preferRecorder) {
      startAudioRecording();
      return;
    }
    const input = byId('messageInput');
    if (!input) return;
    const baseText = input.value.trimEnd();
    try {
      const recognition = new Recognition();
      voiceRecognition = recognition;
      voiceMode = 'dictation';
      recognition.lang = navigator.language || 'zh-CN';
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.maxAlternatives = 1;
      recognition.onstart = () => setVoiceState(true, 'dictation', '正在听写… 再点一次结束');
      recognition.onresult = event => {
        let heard = '';
        for (let index = 0; index < event.results.length; index += 1) {
          heard += event.results[index][0]?.transcript || '';
        }
        input.value = `${baseText}${baseText && heard ? ' ' : ''}${heard}`;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.focus();
      };
      recognition.onerror = event => {
        if (event.error === 'network' || event.error === 'service-not-allowed') preferRecorder = true;
        const message = event.error === 'not-allowed'
          ? '需要允许麦克风权限才能语音输入'
          : event.error === 'no-speech'
            ? '没有听到语音，可以再试一次'
            : '语音转文字暂不可用，再点一次可改为录音上传';
        showToast(message);
      };
      recognition.onend = () => {
        voiceRecognition = null;
        voiceMode = '';
        setVoiceState(false);
      };
      recognition.start();
    } catch {
      voiceRecognition = null;
      voiceMode = '';
      setVoiceState(false);
      preferRecorder = true;
      startAudioRecording();
    }
  }

  function installComposerExperience() {
    const composer = byId('composer');
    const input = byId('messageInput');
    const attachRow = composer?.querySelector('.attach-row');
    const sendRow = composer?.querySelector('.send-row');
    const sendButton = byId('sendBtn');
    const pending = byId('pendingAssets');
    if (!composer || !input || !attachRow || !sendRow || !sendButton) return;

    input.placeholder = '输入问题，或拖入 / 粘贴图片、视频、音频、文档…';
    if (pending && pending.parentElement !== composer) composer.prepend(pending);

    attachRow.innerHTML = `<div class="composer-attach-wrap">
      <button type="button" class="composer-tool-btn attach-menu-trigger" id="attachMenuBtn" aria-label="添加图片、视频、音频或文件" aria-haspopup="menu" aria-expanded="false"><span aria-hidden="true">＋</span></button>
      <div class="attachment-menu" id="attachmentMenu" role="menu" aria-label="添加资料" hidden>
        <button type="button" class="attach attachment-option" data-accept="image/*" role="menuitem"><span aria-hidden="true">▧</span><b>图片 / 截图</b><small>PNG、JPG、WebP 等</small></button>
        <button type="button" class="attach attachment-option" data-accept="video/*" role="menuitem"><span aria-hidden="true">▶</span><b>视频</b><small>直接拖入也可以</small></button>
        <button type="button" class="attach attachment-option" data-accept="audio/*" role="menuitem"><span aria-hidden="true">♪</span><b>音频</b><small>录音或已有音频文件</small></button>
        <button type="button" class="attach attachment-option" data-accept=".pdf,.docx,.xlsx,.xlsm,.csv,.json,.txt,.log,.yaml,.yml,.xml" role="menuitem"><span aria-hidden="true">＋</span><b>文档 / 表格</b><small>PDF、Word、Excel、CSV 等</small></button>
        <button type="button" class="attachment-option attachment-library" id="attachmentLibraryBtn" role="menuitem"><span aria-hidden="true">▤</span><b>本次资料</b><small>查看已经上传的内容</small></button>
        <div class="attachment-menu-hint">也可以直接把文件拖进输入框，或粘贴截图</div>
      </div>
    </div>`;

    const voiceButton = document.createElement('button');
    voiceButton.type = 'button';
    voiceButton.id = 'voiceInputBtn';
    voiceButton.className = 'composer-tool-btn voice-input-btn';
    voiceButton.setAttribute('aria-label', '开始语音输入');
    voiceButton.setAttribute('aria-pressed', 'false');
    voiceButton.innerHTML = '<span class="voice-glyph" aria-hidden="true"><i></i><i></i><i></i></span>';
    const live = document.createElement('span');
    live.id = 'voiceLiveStatus';
    live.className = 'voice-live-status';
    live.setAttribute('aria-live', 'polite');
    live.hidden = true;
    sendRow.insertBefore(live, sendButton);
    sendRow.insertBefore(voiceButton, sendButton);

    const note = document.querySelector('.composer-note');
    if (note) note.textContent = '支持拖入或粘贴图片、视频、音频、文档和表格；涉及真实业务变更时仍会先请您确认。';

    byId('attachMenuBtn')?.addEventListener('click', event => {
      event.stopPropagation();
      openAttachmentMenu();
    });
    byId('attachmentMenu')?.addEventListener('click', event => {
      const option = event.target.closest('button');
      if (option?.classList.contains('attach')) closeAttachmentMenu();
    });
    byId('attachmentMenu')?.addEventListener('keydown', event => {
      const buttons = [...event.currentTarget.querySelectorAll('button')];
      const index = buttons.indexOf(document.activeElement);
      if (event.key === 'Escape') {
        event.preventDefault();
        closeAttachmentMenu({ restoreFocus: true });
      } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const direction = event.key === 'ArrowDown' ? 1 : -1;
        buttons[(index + direction + buttons.length) % buttons.length]?.focus();
      }
    });
    byId('attachmentLibraryBtn')?.addEventListener('click', () => {
      closeAttachmentMenu();
      byId('assetLibraryBtn')?.click();
    });
    document.addEventListener('click', event => {
      if (!event.target.closest('.composer-attach-wrap')) closeAttachmentMenu();
    });

    input.addEventListener('paste', event => {
      const files = filesFromClipboard(event);
      if (!files.length) return;
      event.preventDefault();
      if (dispatchFiles(files)) showToast(files.length === 1 ? '已粘贴 1 个文件' : `已粘贴 ${files.length} 个文件`);
    });

    let composerDragDepth = 0;
    composer.addEventListener('dragenter', event => {
      if (!event.dataTransfer?.types?.includes('Files')) return;
      composerDragDepth += 1;
      composer.classList.add('is-drop-target');
    });
    composer.addEventListener('dragleave', event => {
      if (!event.dataTransfer?.types?.includes('Files')) return;
      composerDragDepth = Math.max(0, composerDragDepth - 1);
      if (!composerDragDepth) composer.classList.remove('is-drop-target');
    });
    window.addEventListener('drop', event => {
      if (event.dataTransfer?.files?.length) showToast(event.dataTransfer.files.length === 1 ? '正在添加文件…' : `正在添加 ${event.dataTransfer.files.length} 个文件…`);
      composerDragDepth = 0;
      composer.classList.remove('is-drop-target');
    });
    window.addEventListener('dragend', () => {
      composerDragDepth = 0;
      composer.classList.remove('is-drop-target');
    });

    sendButton.addEventListener('click', prepareAttachmentOnlySend, true);
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) prepareAttachmentOnlySend();
    }, true);

    voiceButton.addEventListener('click', () => {
      closeAttachmentMenu();
      if (voiceMode) stopVoiceCapture();
      else startDictation();
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

  installComposerExperience();
  bootProviderMarketplace();
})();