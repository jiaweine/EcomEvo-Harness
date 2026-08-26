(() => {
  'use strict';

  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];

  const text = (selector, value) => {
    const node = $(selector);
    if (node && node.textContent !== value) node.textContent = value;
  };

  const attr = (selector, name, value) => {
    const node = $(selector);
    if (node && node.getAttribute(name) !== value) node.setAttribute(name, value);
  };

  function installTheme() {
    if (document.querySelector('link[data-ecomevo-customer-theme]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/customer-theme.css';
    link.dataset.ecomevoCustomerTheme = '1';
    document.head.appendChild(link);
  }

  function replaceExact(root, from, to) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (node.nodeValue?.trim() === from) node.nodeValue = node.nodeValue.replace(from, to);
    }
  }

  function applyPageCopy() {
    document.title = 'EcomEvo 业务服务助手';
    attr('meta[name="description"]', 'content', 'EcomEvo 业务服务助手：提交资料、查看处理进度、补充信息，并在重要操作前由你确认。');
    attr('meta[name="theme-color"]', 'content', '#f5f1ec');

    text('.brand-copy small', '业务服务助手');
    text('#productGuideBtn em', '帮助');
    text('#providerBtn em', '服务');
    text('.left-footer .privacy-mini b', '安心处理');
    text('.left-footer .privacy-mini small', '重要操作会先请你确认');
    text('#settingsBtn', '服务状态');

    text('#conversationMeta', '告诉我们要处理的问题，也可以先上传相关资料。');
    text('.provider-control small', '处理方式');
    attr('#providerSelect', 'aria-label', '选择处理方式');

    text('#welcomePanel h2', '今天想处理什么问题？');
    text('#welcomePanel .welcome-copy > p', '把情况和相关资料告诉我，我会帮你核对重点、提示缺少的信息，并给出下一步建议。');
    text('.input-types > span', '可上传');

    const route = $$('.agent-route .route-node');
    const routeCopy = [
      ['说明问题', '告诉我们你想处理什么'],
      ['核对资料', '整理与问题相关的信息'],
      ['补充信息', '缺少内容时会及时提醒'],
      ['给出建议', '把重点和下一步说清楚'],
      ['确认操作', '重要变更由你决定是否继续'],
    ];
    route.forEach((node, index) => {
      const copy = routeCopy[index];
      if (!copy) return;
      const strong = node.querySelector('strong');
      const small = node.querySelector('small');
      const em = node.querySelector('em');
      if (strong) strong.textContent = copy[0];
      if (small) small.textContent = copy[1];
      if (em) em.hidden = true;
    });
    text('.agent-map-head b', '办理流程');
    text('.agent-map-head span', '清晰可追踪');
    text('.agent-map-foot span', '根据实际情况自动推进');
    text('.agent-map-foot b', '重要操作会先征得你的确认');

    text('.composer-head b', '补充说明或资料');
    text('.composer-head span', '可以随时继续添加');
    attr('#messageInput', 'placeholder', '例如：这笔退款该怎么处理？订单、物流、聊天记录和图片都在这里，请帮我核对清楚。');
    text('.composer-note', '重要操作会先请你确认，再执行。');

    text('.right-title b', '处理详情');
    text('.right-title small', '进度 · 资料 · 待确认');
    attr('#rightbar', 'aria-label', '处理详情');
    text('#tab-progress', '进度');
    text('#tab-evidence', '相关资料');
    text('#tab-actions', '待确认');
    text('#tab-assets', '已上传');

    text('#panel-progress .status-top small', '当前进度');
    const ring = $('.status-ring em');
    if (ring) ring.textContent = '进度';
    text('.follow-title b', '继续补充');
    text('.follow-title small', '你可以接着说明');

    text('#panel-evidence .panel-copy b', '与结果相关的资料');
    text('#panel-evidence .panel-copy p', '这里会整理影响当前结果的资料和信息，并保留来源方便查看。');
    text('#panel-actions .panel-copy b', '需要你确认');
    text('#panel-actions .panel-copy p', '涉及重要变更时，会先说明影响，再由你决定是否继续。');
    text('#panel-assets .panel-copy b', '你上传的资料');
    text('#panel-assets .panel-copy p', '可以随时继续补充，后续处理会继续使用这些资料。');

    text('#providerModal .modal-head small', '处理方式');
    text('#providerModalTitle', '可用服务');
    text('#providerModal .modal-head p', '选择本次任务的处理方式。系统只显示是否可用，不展示复杂的技术配置。');
    text('#providerModal .modal-foot span', '部分处理方式可能会按当前配置使用已接入的外部服务。');

    text('#runtimeModal .runtime-modal-head small', '服务状态');
    text('#runtimeModalTitle', '当前服务是否正常');
    text('#runtimeModalDescription', '这里展示当前服务的可用情况，不影响你的任务内容和处理结果。');
    text('#runtimeTopologyTitle', '服务组成');
    text('#runtimeModal .runtime-topology .runtime-section-title > div > small', '当前可用能力');
    text('#runtimeModal .runtime-topology .runtime-section-title > span', '实时状态');
    text('#runtimeCatalogTitle', '功能详情');
    text('#runtimeModal .runtime-catalog .runtime-section-title > div > small', '更多信息');
    text('#runtimeModal .runtime-modal-foot b', '重要安全设置由管理员统一维护');

    text('#productTour .tour-kicker b', 'ECOMEVO 使用指南');
    text('#productTour .tour-kicker small', '更简单地处理电商业务问题');
    text('#productTourTitle', '把问题和资料交给我，重要操作由你确认。');
    text('#productTourDescription', '同一个任务里可以持续补充资料和追问，处理进度随时可看。');
    const tourSteps = $$('.tour-flow section');
    const tourCopy = [
      ['说明问题', '告诉我们你遇到的情况和希望得到的结果。'],
      ['上传资料', '加入图片、文档、表格或相关记录。'],
      ['查看并确认', '先看处理建议，重要操作再由你决定。'],
    ];
    tourSteps.forEach((node, index) => {
      const copy = tourCopy[index];
      if (!copy) return;
      const b = node.querySelector('b');
      const p = node.querySelector('p');
      if (b) b.textContent = copy[0];
      if (p) p.textContent = copy[1];
    });
    const tourSections = $$('.tour-grid > section .tour-section-title');
    if (tourSections[0]) {
      const small = tourSections[0].querySelector('small');
      const b = tourSections[0].querySelector('b');
      if (small) small.textContent = '常用场景';
      if (b) b.textContent = '5 类电商业务问题';
    }
    if (tourSections[1]) {
      const small = tourSections[1].querySelector('small');
      const b = tourSections[1].querySelector('b');
      if (small) small.textContent = '使用说明';
      if (b) b.textContent = '信息可自动核对，重要操作由你决定';
    }
    const boundaries = $$('.tour-boundaries li');
    const boundaryCopy = [
      '处理结果会尽量说明参考了哪些资料。',
      '图片、音频和文档的处理能力以当前可用服务为准。',
      '涉及重要业务变更时，不会在你不知情的情况下执行。',
    ];
    boundaries.forEach((node, index) => { if (boundaryCopy[index]) node.textContent = boundaryCopy[index]; });
    text('.tour-status > span', '使用提示');
    const tourStatus = $('.tour-status p');
    if (tourStatus) tourStatus.innerHTML = '<b>放心使用：</b>系统会自动完成信息核对和整理；涉及重要业务变更时，会先请你确认。';
    text('#tourSkipBtn', '进入首页');
    text('#tourStartBtn', '用示例开始');
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
      overview.setAttribute('aria-label', '当前服务概况');
      text('.ops-overview-head small', '当前服务概况');
      text('.ops-overview-head b', '处理服务');
      const metricLabels = $$('.ops-metric > small');
      const labels = ['服务状态', '可用功能', '最近任务', '重要操作'];
      metricLabels.forEach((node, index) => { if (labels[index]) node.textContent = labels[index]; });
      const metricDetails = $$('.ops-metric > span');
      if (metricDetails[1]) metricDetails[1].textContent = '当前可用于处理任务的功能';
      if (metricDetails[3]) metricDetails[3].textContent = '重要变更会先请你确认';
    }

    $$('.evidence-summary-stat small').forEach(node => {
      const map = { TOTAL: '全部', ORIGINAL: '你提供的', DERIVED: '系统整理的' };
      if (map[node.textContent.trim()]) node.textContent = map[node.textContent.trim()];
    });
    $$('.evidence-meta-item').forEach(node => {
      const map = { SOURCE: '来源', PROVENANCE: '类型', TRACE: '编号' };
      if (map[node.dataset.label]) node.dataset.label = map[node.dataset.label];
    });
    $$('.action-authority-row .evidence-audit-cell small').forEach(node => {
      const map = { STATUS: '状态', AUTHORITY: '确认', IMPACT: '影响' };
      if (map[node.textContent.trim()]) node.textContent = map[node.textContent.trim()];
    });
    text('.trace-ledger-title small', '办理情况');
    text('.trace-ledger-title b', '办理进度');
    const traceMeta = $('.trace-ledger-meta');
    if (traceMeta) traceMeta.textContent = traceMeta.textContent.replace(/steps/g, '项');

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
      const b = node.querySelector(':scope > b');
      const p = node.querySelector(':scope > p');
      const count = node.querySelector('.runtime-lane-count');
      if (b) b.textContent = copy[0];
      if (p) p.textContent = copy[1];
      if (count) count.textContent = count.textContent.replace('LIVE', '项可用');
    });

    $$('.runtime-health-grid > div small').forEach((node, index) => {
      const labels = ['服务状态', '可用功能', '检查结果', '备用功能'];
      if (labels[index]) node.textContent = labels[index];
    });
    const summarySpans = $$('.runtime-health-grid > div span');
    if (summarySpans[0]) summarySpans[0].textContent = '当前可用服务已经准备好';
    if (summarySpans[1]) summarySpans[1].textContent = '当前可用于处理任务';
    if (summarySpans[2]) summarySpans[2].textContent = '基础检查已完成';
    if (summarySpans[3]) summarySpans[3].textContent = '需要时可启用';

    $$('.runtime-filter').forEach(node => {
      const key = node.dataset.runtimeFilter;
      const labels = { all: '全部', state: '任务信息', cognition: '智能处理', execution: '资料连接', safety: '安全保护' };
      if (labels[key]) node.textContent = labels[key];
    });

    const taskState = $('#taskState');
    if (taskState?.textContent === '等待目标') taskState.textContent = '等待开始';
    const taskDetail = $('#taskDetail');
    if (taskDetail?.textContent.includes('交代目标')) taskDetail.textContent = '描述问题或先上传资料，我会帮你继续处理。';
    const workStep = $('#workStep');
    if (workStep?.textContent === '正在自主处理') workStep.textContent = '正在处理';
    const workDetail = $('#workDetail');
    if (workDetail?.textContent.includes('任务状态') || workDetail?.textContent.includes('证据路径')) workDetail.textContent = '正在核对你提供的信息和相关资料';
  }

  function applyAll() {
    applyPageCopy();
    applyDynamicCopy();
  }

  function boot() {
    installTheme();
    applyAll();
    let queued = false;
    const observer = new MutationObserver(() => {
      if (queued) return;
      queued = true;
      queueMicrotask(() => {
        queued = false;
        applyAll();
      });
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  installTheme();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
