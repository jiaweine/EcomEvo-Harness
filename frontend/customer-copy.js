(() => {
  'use strict';

  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];

  function setNodeText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function text(selector, value) {
    setNodeText($(selector), value);
  }

  function replaceExact(root, from, to) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (node.nodeValue?.trim() === from) node.nodeValue = node.nodeValue.replace(from, to);
    }
  }

  function applyDynamicCopy() {
    const replacements = new Map([
      ['契约通过', '服务正常'],
      ['契约全部通过', '服务正常'],
      ['需要检查', '部分服务需检查'],
      ['等待实例', '正在准备'],
      ['人工确认', '需要确认'],
      ['运行派生', '系统整理'],
      ['原始资料', '你提供的'],
      ['当前结论', '当前结果'],
      ['已关联资料', '已关联'],
      ['业务状态变更', '会影响业务状态'],
      ['记录级影响', '仅影响当前记录'],
      ['已决策', '已处理'],
      ['执行轨迹', '办理进度'],
      ['最近运行', '最近任务'],
      ['继续一个耐久任务', '继续上次处理'],
      ['真实运行数据', '实时更新'],
    ]);
    replacements.forEach((to, from) => replaceExact(document.body, from, to));

    const overview = $('.ops-overview');
    if (overview) {
      if (overview.getAttribute('aria-label') !== '当前服务概况') overview.setAttribute('aria-label', '当前服务概况');
      text('.ops-overview-head small', '当前服务概况');
      text('.ops-overview-head b', '处理服务');
      const labels = ['服务状态', '可用功能', '最近任务', '重要操作'];
      $$('.ops-metric > small').forEach((node, index) => { if (labels[index]) setNodeText(node, labels[index]); });
      const details = $$('.ops-metric > span');
      if (details[1]) setNodeText(details[1], '当前可用于处理任务的功能');
      if (details[3]) setNodeText(details[3], '重要变更会先请你确认');
    }

    $$('.evidence-summary-stat small').forEach(node => {
      const map = { TOTAL: '全部', ORIGINAL: '你提供的', DERIVED: '系统整理的' };
      if (map[node.textContent.trim()]) setNodeText(node, map[node.textContent.trim()]);
    });
    $$('.evidence-meta-item').forEach(node => {
      const map = { SOURCE: '来源', PROVENANCE: '类型', TRACE: '编号' };
      if (map[node.dataset.label] && node.dataset.label !== map[node.dataset.label]) node.dataset.label = map[node.dataset.label];
    });
    $$('.action-authority-row .evidence-audit-cell small').forEach(node => {
      const map = { STATUS: '状态', AUTHORITY: '确认', IMPACT: '影响' };
      if (map[node.textContent.trim()]) setNodeText(node, map[node.textContent.trim()]);
    });

    text('.trace-ledger-title small', '办理情况');
    text('.trace-ledger-title b', '办理进度');
    const traceMeta = $('.trace-ledger-meta');
    if (traceMeta) {
      const next = traceMeta.textContent.replace(/steps/g, '项');
      if (next !== traceMeta.textContent) traceMeta.textContent = next;
    }

    const laneCopy = {
      state: ['任务信息', '保存当前任务和已提交的内容'],
      cognition: ['智能处理', '整理信息并给出处理建议'],
      execution: ['资料连接', '读取已接入的业务资料与工具'],
      safety: ['安全保护', '重要操作前进行检查和确认'],
    };
    $$('.runtime-lane').forEach(node => {
      const key = ['state', 'cognition', 'execution', 'safety'].find(name => node.classList.contains(name));
      const copy = laneCopy[key];
      if (!copy) return;
      setNodeText(node.querySelector(':scope > b'), copy[0]);
      setNodeText(node.querySelector(':scope > p'), copy[1]);
      const count = node.querySelector('.runtime-lane-count');
      if (count) {
        const next = count.textContent.replace('LIVE', '项可用');
        if (next !== count.textContent) count.textContent = next;
      }
    });

    const summaryLabels = ['服务状态', '可用功能', '检查结果', '备用功能'];
    $$('.runtime-health-grid > div small').forEach((node, index) => { if (summaryLabels[index]) setNodeText(node, summaryLabels[index]); });
    const summarySpans = $$('.runtime-health-grid > div span');
    if (summarySpans[0]) setNodeText(summarySpans[0], '当前可用服务已经准备好');
    if (summarySpans[1]) setNodeText(summarySpans[1], '当前可用于处理任务');
    if (summarySpans[2]) setNodeText(summarySpans[2], '基础检查已完成');
    if (summarySpans[3]) setNodeText(summarySpans[3], '需要时可启用');

    const filterLabels = { all: '全部', state: '任务信息', cognition: '智能处理', execution: '资料连接', safety: '安全保护' };
    $$('.runtime-filter').forEach(node => { if (filterLabels[node.dataset.runtimeFilter]) setNodeText(node, filterLabels[node.dataset.runtimeFilter]); });

    const taskState = $('#taskState');
    if (taskState?.textContent === '等待目标') setNodeText(taskState, '等待开始');
    const taskDetail = $('#taskDetail');
    if (taskDetail?.textContent.includes('交代目标')) setNodeText(taskDetail, '描述问题或先上传资料，我会帮你继续处理。');
    const workStep = $('#workStep');
    if (workStep?.textContent === '正在自主处理') setNodeText(workStep, '正在处理');
    const workDetail = $('#workDetail');
    if (workDetail?.textContent.includes('任务状态') || workDetail?.textContent.includes('证据路径')) setNodeText(workDetail, '正在核对你提供的信息和相关资料');
  }

  function boot() {
    applyDynamicCopy();
    let queued = false;
    const observer = new MutationObserver(() => {
      if (queued) return;
      queued = true;
      queueMicrotask(() => {
        queued = false;
        applyDynamicCopy();
      });
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
