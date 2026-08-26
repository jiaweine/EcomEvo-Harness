(() => {
  'use strict';

  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];

  function setText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function friendlyEvidenceState(value = '') {
    const text = String(value).trim();
    if (text === '本轮查证中') return '正在核对';
    if (text === '证据完整') return '资料已齐';
    if (text === '等待本轮') return '等待开始';
    const missing = text.match(/^缺证\s*(.+)$/u);
    if (missing) return `还缺 ${missing[1]} 项`;
    return text.replace(/证据/g, '资料').replace(/查证/g, '核对');
  }

  function friendlyStop(value = '') {
    const text = String(value).trim();
    const map = new Map([
      ['验证完成', '本轮处理完成'],
      ['预算用尽', '本轮处理已暂停'],
      ['本轮主动停止', '本轮处理已暂停'],
      ['没有更高价值的下一步', '当前没有更多需要处理的内容'],
      ['补证没有改变状态', '等待补充资料'],
      ['本轮处理中', '正在处理'],
    ]);
    if (map.has(text)) return map.get(text);
    return text
      .replace(/补证/g, '补充资料')
      .replace(/验证/g, '核对')
      .replace(/预算/g, '处理额度')
      .replace(/控制器/g, '系统');
  }

  function polishRuntimePulse() {
    const host = $('#runtimePulse');
    if (!host) return;

    setText(host.querySelector('.runtime-pulse-head b'), '处理情况');
    setText(host.querySelector('.runtime-pulse-head small'), '根据当前任务实时更新');
    host.setAttribute('aria-label', '当前处理情况');

    const cells = $$('#runtimePulse .runtime-pulse-grid > div');
    if (cells[0]) {
      setText(cells[0].querySelector('small'), '资料情况');
      const strong = cells[0].querySelector('strong');
      if (strong) setText(strong, friendlyEvidenceState(strong.textContent));
      cells[0].hidden = false;
    }
    if (cells[3]) {
      setText(cells[3].querySelector('small'), '当前状态');
      const strong = cells[3].querySelector('strong');
      if (strong) setText(strong, friendlyStop(strong.textContent));
      cells[3].hidden = false;
    }
    [1, 2, 4, 5].forEach(index => { if (cells[index]) cells[index].hidden = true; });

    const foot = $$('#runtimePulse .runtime-pulse-foot > span');
    if (foot[0]) {
      const raw = foot[0].textContent.trim();
      if (raw.startsWith('最先缺口：')) {
        setText(foot[0], raw.replace('最先缺口：', '还缺：').replace(/证据/g, '资料'));
        foot[0].hidden = false;
      } else {
        foot[0].hidden = true;
      }
    }
    if (foot[1]) foot[1].hidden = true;
  }

  function polishOverview() {
    const metrics = $$('.ops-metric');
    if (metrics[0]) {
      const status = metrics[0].querySelector('strong')?.textContent.trim() || '';
      const detail = metrics[0].querySelector('span');
      setText(detail, status === '服务正常' ? '当前处理服务可用' : '部分处理服务需要检查');
    }
    if (metrics[2]) setText(metrics[2].querySelector('span'), '可以继续的最近任务');
  }

  function polishEvidence() {
    $$('.evidence-card').forEach(card => {
      const source = card.querySelector('.evidence-top em');
      if (source?.textContent.trim() === '核对结果') setText(source, '系统整理');
      if (source?.textContent.trim() === '业务资料') setText(source, '你提供的');

      const items = [...card.querySelectorAll('.evidence-meta-item')];
      if (!items.length) return;
      const firstValue = items[0]?.querySelector('b')?.textContent.trim();
      const secondValue = items[1]?.querySelector('b')?.textContent.trim();
      if (items[1]) items[1].hidden = Boolean(firstValue && secondValue && firstValue === secondValue);
      if (items[2]) {
        const value = items[2].querySelector('b')?.textContent.trim() || '';
        if (value === '当前结果' || value === '已关联') items[2].dataset.label = '关联';
      }
    });
  }

  function polishServiceModal() {
    const summary = $$('.runtime-health-grid > div');
    if (summary[3]) summary[3].hidden = true;
    setText($('.runtime-modal-foot span'), '服务能力按当前配置提供');
  }

  function polishProgressLanguage() {
    $$('#progressList .progress-item').forEach(item => {
      const nodes = [...item.querySelectorAll('b,small,p,span')];
      nodes.forEach(node => {
        const before = node.textContent;
        const after = before
          .replace(/业务上下文/g, '任务信息')
          .replace(/处理约束/g, '处理要求')
          .replace(/证据/g, '资料')
          .replace(/查证/g, '核对');
        if (after !== before) setText(node, after);
      });
    });
  }

  function apply() {
    polishRuntimePulse();
    polishOverview();
    polishEvidence();
    polishServiceModal();
    polishProgressLanguage();
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
