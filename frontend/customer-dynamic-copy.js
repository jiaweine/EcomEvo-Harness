(() => {
  'use strict';

  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];

  function friendlyProvider(value = '') {
    return String(value)
      .replace(/^认知引擎\s*[·:-]?\s*/u, '智能处理 · ')
      .replace(/^自动编排/u, '自动选择')
      .replace(/^本地受控/u, '本地处理');
  }

  function simplifyOperational(value = '') {
    return String(value)
      .replace(/目标与证据/g, '问题与资料')
      .replace(/自适应规划/g, '安排下一步')
      .replace(/自主规划/g, '安排下一步')
      .replace(/并行查证/g, '核对相关信息')
      .replace(/验证门禁/g, '检查处理结果')
      .replace(/恢复演进/g, '重新调整处理')
      .replace(/任务状态与证据路径/g, '当前信息和相关资料')
      .replace(/Runtime/gi, '服务')
      .replace(/Agent/gi, '助手')
      .replace(/Planner/gi, '处理计划')
      .replace(/Verifier/gi, '结果核对')
      .replace(/Evidence/gi, '资料')
      .replace(/Authority/gi, '确认')
      .replace(/Plugin/gi, '功能')
      .replace(/Contract/gi, '检查')
      .replace(/Provenance/gi, '来源');
  }

  function rewriteTextNodes(root) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const before = node.nodeValue || '';
      const after = simplifyOperational(before);
      if (after !== before) node.nodeValue = after;
    }
  }

  function apply() {
    $$('#providerSelect option').forEach(option => {
      const next = friendlyProvider(option.textContent || '');
      if (option.textContent !== next) option.textContent = next;
    });
    $$('#providerGrid .provider-card b').forEach(title => {
      const next = friendlyProvider(title.textContent || '');
      if (title.textContent !== next) title.textContent = next;
    });

    $$('.answer-provider').forEach(node => {
      if (node.textContent !== '由 EcomEvo 完成') node.textContent = '由 EcomEvo 完成';
    });

    rewriteTextNodes($('.progress-list'));
    rewriteTextNodes($('.work-card'));
    rewriteTextNodes($('.status-card'));

    const runtimeCatalog = $('.runtime-catalog');
    if (runtimeCatalog) runtimeCatalog.setAttribute('aria-hidden', 'true');
  }

  function boot() {
    apply();
    let queued = false;
    const observer = new MutationObserver(() => {
      if (queued) return;
      queued = true;
      queueMicrotask(() => {
        queued = false;
        apply();
      });
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
