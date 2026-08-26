(() => {
  'use strict';

  const replacements = [
    ['商品治理', '商品问题'],
    ['商家审核', '商家认证'],
    ['售后判责', '售后处理'],
    ['风险核查', '风险问题'],
    ['内容审核', '内容问题'],
    ['新的业务任务', '新的业务办理'],
    ['业务任务', '业务办理'],
    ['最近任务', '最近办理'],
    ['还没有历史任务', '还没有办理记录'],
    ['认知引擎', '处理方式'],
    ['自动编排', '自动选择'],
    ['本地受控', '本地处理'],
    ['业务资料', '您提交的'],
    ['核对结果', '系统整理'],
    ['关键证据', '判断依据'],
    ['执行控制', '待您确认'],
    ['执行轨迹', '办理进度'],
    ['原始资料', '您提交的'],
    ['运行派生', '系统整理'],
    ['低成本追加指令', '继续补充信息或选择下一步'],
    ['正在自主处理', '正在处理'],
    ['正在建立任务状态与证据路径', '正在核对您提交的信息'],
    ['等待目标', '等待提交'],
    ['交代目标或先加入资料，系统会自主规划下一步。', '描述您的问题或先添加资料，我们会从这里开始处理。'],
    ['交代目标并加入资料，系统会围绕证据缺口继续处理。', '描述您的问题并添加相关资料，我们会继续帮您处理。'],
    ['改变业务状态', '会影响业务状态'],
    ['待确认操作', '待确认'],
    ['任务控制面', '办理详情'],
    ['多模态输入', '描述您的问题'],
    ['Runtime', '服务'],
    ['Agent', '服务'],
    ['Plugin', '功能'],
    ['Provider', '处理方式'],
    ['Evidence', '资料'],
    ['Authority', '待确认'],
    ['Provenance', '来源'],
    ['Trace', '记录'],
    ['Contract', '状态'],
    ['Event Sourced', '信息自动保存'],
  ];

  function installTheme() {
    if (document.querySelector('link[data-ecomevo-customer-theme]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/assets/customer-service-theme.css';
    link.dataset.ecomevoCustomerTheme = '1';
    document.head.appendChild(link);
    document.documentElement.dataset.ecomevoTheme = 'customer-service';
    const themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) themeColor.setAttribute('content', '#ffffff');
  }

  function replaceText(value) {
    let next = String(value || '');
    for (const [from, to] of replacements) next = next.split(from).join(to);
    return next;
  }

  function translateTextNodes(root) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || ['SCRIPT', 'STYLE', 'OPTION'].includes(parent.tagName)) return NodeFilter.FILTER_REJECT;
        return node.nodeValue?.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(node => {
      const next = replaceText(node.nodeValue);
      if (next !== node.nodeValue) node.nodeValue = next;
    });
  }

  function customerizeProviders() {
    const select = document.getElementById('providerSelect');
    if (select) {
      let onlineIndex = 0;
      [...select.options].forEach(option => {
        const value = String(option.value || '');
        const raw = option.textContent.trim();
        if (value === 'auto' || raw.includes('自动编排')) option.textContent = '自动选择';
        else if (value === 'demo' || raw.includes('本地受控')) option.textContent = '本地处理';
        else if (raw.includes('认知引擎')) {
          onlineIndex += 1;
          option.textContent = `在线处理 ${onlineIndex}${raw.includes('未配置') ? ' · 未设置' : ''}`;
        } else {
          option.textContent = replaceText(raw).replace('未配置', '未设置');
        }
      });
    }

    const cards = [...document.querySelectorAll('#providerGrid .provider-card')];
    let onlineIndex = 0;
    cards.forEach(card => {
      const title = card.querySelector('b');
      const status = card.querySelector('.provider-status');
      if (title) {
        const raw = title.textContent.trim();
        if (raw.includes('本地受控')) title.textContent = '本地处理';
        else if (raw.includes('认知引擎')) {
          onlineIndex += 1;
          title.textContent = `在线处理 ${onlineIndex}`;
        } else title.textContent = replaceText(raw);
      }
      if (status?.textContent.includes('未配置')) status.textContent = '未设置';
    });
  }

  function customerizeAnswerFooters() {
    document.querySelectorAll('.answer-provider').forEach(node => {
      if (node.textContent.trim() !== '处理完成') node.textContent = '处理完成';
    });
  }

  function customerizeEvidence() {
    document.querySelectorAll('.evidence-top em').forEach(node => {
      const raw = node.textContent.trim();
      if (raw.includes('业务资料') || raw.includes('您提交')) node.textContent = '您提交的';
      else node.textContent = '系统整理';
    });
  }

  function customerizeDynamicCopy() {
    const scopes = [
      document.getElementById('conversationList'),
      document.getElementById('progressList'),
      document.getElementById('actionList'),
      document.getElementById('evidenceList'),
      document.getElementById('workCard'),
      document.querySelector('.task-head'),
    ];
    scopes.forEach(translateTextNodes);
    customerizeProviders();
    customerizeAnswerFooters();
    customerizeEvidence();
  }

  function boot() {
    customerizeDynamicCopy();
    let queued = false;
    const observer = new MutationObserver(() => {
      if (queued) return;
      queued = true;
      queueMicrotask(() => {
        queued = false;
        customerizeDynamicCopy();
      });
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  installTheme();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();