(() => {
  'use strict';

  const CATEGORY_LABELS = {
    factual_error: '事实有误',
    missing_support: '缺少直接支持',
    wrong_rule: '规则引用有误',
    stale_source: '来源已过期',
    evidence_conflict: '证据存在冲突',
    other: '其他问题',
  };
  const IMPACT_LABELS = {
    answer_only: '仅影响表述',
    decision_relevant: '可能影响判断',
    action_blocking: '涉及高影响操作',
  };
  const state = { canSubmit: false, messageId: '', targets: [] };

  async function api(url, opts = {}) {
    const options = { ...opts };
    if (typeof options.body === 'string') {
      options.headers = { 'content-type': 'application/json', ...(options.headers || {}) };
    }
    const response = await fetch(url, options);
    if (!response.ok) {
      let detail = `请求失败 ${response.status}`;
      try {
        const payload = await response.json();
        if (typeof payload.detail === 'string') detail = payload.detail;
      } catch {}
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }

  function conversationId() {
    return new URL(location.href).searchParams.get('conversation') || '';
  }

  function ensureDialog() {
    let root = document.getElementById('feedbackDisputeModal');
    if (root) return root;
    root = document.createElement('div');
    root.id = 'feedbackDisputeModal';
    root.className = 'feedback-dispute-backdrop';
    root.hidden = true;
    root.innerHTML = `
      <section class="feedback-dispute-modal" role="dialog" aria-modal="true" aria-labelledby="feedbackDisputeTitle">
        <header>
          <div><small>结果纠错</small><h3 id="feedbackDisputeTitle">指出需要复核的地方</h3><p>反馈只会进入质量复核，不会自动修改业务结论、规则或执行权限。</p></div>
          <button type="button" class="feedback-dispute-close" aria-label="关闭">×</button>
        </header>
        <form id="feedbackDisputeForm">
          <label><span>问题类型</span><select id="feedbackCategory"></select></label>
          <label><span>影响范围</span><select id="feedbackImpact"></select></label>
          <label><span>具体位置</span><select id="feedbackTarget"><option value="answer">整个回答</option></select></label>
          <label class="feedback-dispute-wide"><span>哪里有问题</span><textarea id="feedbackExplanation" rows="4" maxlength="6000" required placeholder="请说明哪项事实、证据或规则需要重新核对。"></textarea></label>
          <label class="feedback-dispute-wide"><span>建议修正（可选）</span><textarea id="feedbackCorrection" rows="3" maxlength="6000" placeholder="例如：应以 2026-09-01 生效的规则版本为准。"></textarea></label>
          <div class="feedback-dispute-note">提交异议不会直接改变原回复，也不会触发退款、下架、审核通过等业务操作。</div>
          <div class="feedback-dispute-status" id="feedbackDisputeStatus" role="status" aria-live="polite"></div>
          <footer><button type="button" class="feedback-dispute-cancel">取消</button><button type="submit" class="feedback-dispute-submit">提交复核</button></footer>
        </form>
      </section>`;
    document.body.appendChild(root);
    const category = root.querySelector('#feedbackCategory');
    category.innerHTML = Object.entries(CATEGORY_LABELS).map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
    const impact = root.querySelector('#feedbackImpact');
    impact.innerHTML = Object.entries(IMPACT_LABELS).map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
    root.querySelector('.feedback-dispute-close').onclick = closeDialog;
    root.querySelector('.feedback-dispute-cancel').onclick = closeDialog;
    root.addEventListener('click', event => { if (event.target === root) closeDialog(); });
    root.querySelector('#feedbackDisputeForm').addEventListener('submit', submitFeedback);
    return root;
  }

  function closeDialog() {
    const root = document.getElementById('feedbackDisputeModal');
    if (!root) return;
    root.hidden = true;
    state.messageId = '';
    state.targets = [];
  }

  function setStatus(text, error = false) {
    const node = document.getElementById('feedbackDisputeStatus');
    if (!node) return;
    node.textContent = text || '';
    node.classList.toggle('error', Boolean(error));
  }

  async function openDialog(messageId) {
    const cid = conversationId();
    if (!cid || !messageId) return;
    const root = ensureDialog();
    state.messageId = messageId;
    state.targets = [{ type: 'answer', ref: '', label: '整个回答' }];
    root.querySelector('#feedbackExplanation').value = '';
    root.querySelector('#feedbackCorrection').value = '';
    root.querySelector('#feedbackImpact').value = 'decision_relevant';
    root.querySelector('#feedbackCategory').value = 'missing_support';
    root.querySelector('#feedbackTarget').innerHTML = '<option value="0">整个回答</option>';
    setStatus('正在读取可核对的声明和证据…');
    root.hidden = false;
    root.querySelector('.feedback-dispute-close').focus();
    try {
      const data = await api(`/api/conversations/${encodeURIComponent(cid)}/feedback/targets?message_id=${encodeURIComponent(messageId)}`);
      if (!data.can_submit) {
        setStatus('当前角色只能查看，不能提交纠错。', true);
        return;
      }
      for (const claim of data.claims || []) {
        state.targets.push({ type: 'claim', ref: String(claim.ref || ''), label: `声明 · ${String(claim.text || '').slice(0, 120)}` });
      }
      for (const evidence of data.evidence || []) {
        state.targets.push({ type: 'evidence', ref: String(evidence.ref || ''), label: `证据 · ${String(evidence.title || evidence.ref || '').slice(0, 120)}` });
      }
      const select = root.querySelector('#feedbackTarget');
      select.innerHTML = state.targets.map((target, index) => {
        const option = document.createElement('option');
        option.value = String(index);
        option.textContent = target.label;
        return option.outerHTML;
      }).join('');
      setStatus(data.claims?.length || data.evidence?.length ? '可以精确选择具体声明或证据。' : '当前回复没有细粒度 grounding，可对整个回答提交异议。');
    } catch (error) {
      setStatus(error.message || '无法读取复核目标', true);
    }
  }

  async function submitFeedback(event) {
    event.preventDefault();
    const cid = conversationId();
    const root = ensureDialog();
    const submit = root.querySelector('.feedback-dispute-submit');
    const targetIndex = Number(root.querySelector('#feedbackTarget').value || 0);
    const target = state.targets[targetIndex] || state.targets[0] || { type: 'answer', ref: '' };
    const explanation = root.querySelector('#feedbackExplanation').value.trim();
    if (!cid || !state.messageId || explanation.length < 3) {
      setStatus('请至少说明 3 个字符的问题描述。', true);
      return;
    }
    submit.disabled = true;
    setStatus('正在提交…');
    try {
      await api(`/api/conversations/${encodeURIComponent(cid)}/feedback`, {
        method: 'POST',
        body: JSON.stringify({
          assistant_message_id: state.messageId,
          category: root.querySelector('#feedbackCategory').value,
          impact: root.querySelector('#feedbackImpact').value,
          target_type: target.type,
          target_ref: target.ref,
          explanation,
          proposed_correction: root.querySelector('#feedbackCorrection').value.trim(),
        }),
      });
      setStatus('已提交质量复核。原回复与业务权限均未被自动修改。');
      setTimeout(closeDialog, 900);
    } catch (error) {
      setStatus(error.message || '提交失败', true);
    } finally {
      submit.disabled = false;
    }
  }

  function decorateMessages() {
    if (!state.canSubmit) return;
    document.querySelectorAll('.msg.assistant .answer-foot').forEach(foot => {
      const copy = foot.querySelector('[data-copy]');
      const messageId = copy?.dataset?.copy || '';
      if (!messageId || foot.querySelector(`.feedback-dispute-trigger[data-message-id="${CSS.escape(messageId)}"]`)) return;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'answer-action feedback-dispute-trigger';
      button.dataset.messageId = messageId;
      button.textContent = '纠错';
      button.setAttribute('aria-label', '对这条回复提出事实或证据异议');
      button.onclick = () => openDialog(messageId);
      foot.appendChild(button);
    });
  }

  async function init() {
    try {
      const capabilities = await api('/api/feedback/capabilities');
      state.canSubmit = Boolean(capabilities.can_submit);
    } catch {
      state.canSubmit = false;
    }
    if (!state.canSubmit) return;
    decorateMessages();
    const list = document.getElementById('messageList');
    if (list) {
      let scheduled = false;
      const observer = new MutationObserver(() => {
        if (scheduled) return;
        scheduled = true;
        queueMicrotask(() => { scheduled = false; decorateMessages(); });
      });
      observer.observe(list, { childList: true, subtree: true });
    }
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !document.getElementById('feedbackDisputeModal')?.hidden) closeDialog();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
