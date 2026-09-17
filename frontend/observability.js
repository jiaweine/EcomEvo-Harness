(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const SCENE = {
    product_governance: '商品治理', merchant_review: '商家审核', aftersales: '售后判责',
    risk_review: '风险核查', content_audit: '内容审核', general: '业务核对',
  };
  const STATUS = {
    queued: '排队中', running: '处理中', succeeded: '成功', failed: '失败',
    proposed: '待确认', approved: '已批准', simulated: '已模拟', executed: '已执行',
    rejected: '已拒绝', uncertain: '结果待核对',
  };
  let currentWindow = '7d';
  let toastTimer;

  async function api(url) {
    const response = await fetch(url);
    if (!response.ok) {
      let detail = `请求失败 ${response.status}`;
      try { const body = await response.json(); if (typeof body.detail === 'string') detail = body.detail; } catch {}
      throw new Error(detail);
    }
    return response.json();
  }

  function toast(message) {
    const node = $('obsToast');
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('show'), 2600);
  }

  function fmtRate(value) {
    return value == null ? '—' : `${Math.round(Number(value) * 1000) / 10}%`;
  }

  function fmtSeconds(value) {
    if (value == null) return '—';
    const seconds = Number(value);
    if (seconds < 60) return `${Math.round(seconds * 10) / 10}s`;
    if (seconds < 3600) return `${Math.round(seconds / 6) / 10}m`;
    return `${Math.round(seconds / 360) / 10}h`;
  }

  function fmtHours(value) {
    if (value == null) return '—';
    const hours = Math.max(0, Number(value));
    if (hours < 1) return `${Math.round(hours * 600) / 10}m`;
    return `${Math.round(hours * 100) / 100}h`;
  }

  function fmtEfficiency(value) {
    if (value == null) return '—';
    const number = Number(value);
    return Number.isFinite(number) ? String(Math.round(number * 100) / 100) : '—';
  }

  function stat(label, value, note = '') {
    return `<article class="obs-stat"><small>${esc(label)}</small><b>${esc(value)}</b>${note ? `<p>${esc(note)}</p>` : ''}</article>`;
  }

  function renderHero(data) {
    const ns = data.north_star || {};
    const quality = data.quality || {};
    const operatorHours = ns.operator_hours || {};
    const efficiency = ns.verified_decisions_per_operator_hour || {};
    $('obsHero').innerHTML = [
      [
        'Verified / operator hour',
        efficiency.available ? fmtEfficiency(efficiency.value) : '—',
        efficiency.available
          ? `${efficiency.verified_decisions ?? ns.verified_decisions ?? 0} verified / ${fmtHours(efficiency.operator_hours ?? operatorHours.value)}`
          : (efficiency.reason || '没有可核验的人工活跃时间，暂不计算'),
      ],
      [
        'Operator hours',
        operatorHours.available ? fmtHours(operatorHours.value) : '—',
        operatorHours.available
          ? `${operatorHours.active_users ?? 0} 位活跃操作员 · ${operatorHours.bucket_seconds ?? 15}s 服务端 bucket`
          : (operatorHours.reason || '未采集'),
      ],
      ['Verified decisions', ns.verified_decisions ?? 0, '已完成且没有已知证据缺口'],
      ['Evidence gap', fmtRate(quality.evidence_gap_rate), `${quality.evidence_gap_results ?? 0} 个结果存在明确缺口`],
    ].map(([label, value, note], index) => `<article class="obs-hero-card ${index ? 'muted' : ''}"><small>${esc(label)}</small><b>${esc(value)}</b><p>${esc(note)}</p></article>`).join('');
  }

  function renderQuality(data) {
    const q = data.quality || {};
    $('obsQuality').innerHTML = [
      stat('Assistant results', q.assistant_results ?? 0),
      stat('Verified rate', fmtRate(q.verified_rate), '不是模型置信度'),
      stat('Evidence gap rate', fmtRate(q.evidence_gap_rate)),
      stat('Verified decisions', q.verified_decisions ?? 0),
      stat('Evidence gaps', q.evidence_gap_results ?? 0),
      stat('Claim grounding coverage', fmtRate(q.grounding_coverage_rate), `${q.grounding_instrumented_results ?? 0} 条结果有细粒度 grounding`),
    ].join('');
  }

  function renderReliability(data) {
    const r = data.reliability || {};
    const latency = r.end_to_end_latency_seconds || {};
    $('obsReliability').innerHTML = [
      stat('Jobs', r.jobs ?? 0),
      stat('Succeeded', r.succeeded ?? 0),
      stat('Failed', r.failed ?? 0),
      stat('Retry rate', fmtRate(r.retry_rate), `${r.retry_jobs ?? 0} 个 job attempts > 1`),
      stat('Latency p50', fmtSeconds(latency.p50), 'created → terminal；包含排队'),
      stat('Latency p95', fmtSeconds(latency.p95), `${latency.samples ?? 0} 个 terminal 样本`),
    ].join('');
  }

  function renderAuthority(data) {
    const a = data.authority_workload || {};
    $('obsAuthority').innerHTML = [
      stat('Actions', a.actions_in_window ?? 0),
      stat('Need confirmation', a.confirmation_required ?? 0, fmtRate(a.confirmation_required_rate)),
      stat('Side-effect actions', a.side_effect_actions ?? 0),
      stat('Uncertain incidents', a.uncertain_incidents ?? 0, '统计窗口内当前仍标记 uncertain 的 action'),
      stat('Waiting approval now', a.current_waiting_approval ?? 0),
      stat('Uncertain now', a.current_uncertain_actions ?? 0, '必须核对实际业务状态'),
    ].join('');
  }

  function renderAvailability(data) {
    const labels = {
      operator_active_hours: 'Operator active hours', token_usage: 'Token usage', provider_cost: 'Provider cost',
    };
    const availability = data.telemetry_availability || {};
    $('obsAvailability').innerHTML = Object.entries(availability).map(([key, item]) => `
      <div class="obs-availability-row"><div><b>${esc(labels[key] || key)}</b><p>${esc(item?.reason || item?.definition || '')}</p></div><span>${item?.available ? '已采集' : '未采集'}</span></div>`).join('');
  }

  function renderSeries(data) {
    const rows = Array.isArray(data.series) ? data.series : [];
    $('obsSeriesHint').textContent = rows.length ? `${rows.length} 个有数据的日期` : '当前窗口暂无数据';
    if (!rows.length) {
      $('obsSeries').innerHTML = '<div class="obs-availability-row"><div><b>暂无趋势数据</b><p>产生任务结果后会在这里显示。</p></div></div>';
      return;
    }
    const max = Math.max(1, ...rows.flatMap(row => [Number(row.verified_decisions || 0), Number(row.evidence_gaps || 0)]));
    $('obsSeries').innerHTML = rows.map(row => {
      const verified = Number(row.verified_decisions || 0);
      const gap = Number(row.evidence_gaps || 0);
      const vh = Math.max(3, Math.round(verified / max * 100));
      const gh = Math.max(gap ? 3 : 0, Math.round(gap / max * 100));
      const hours = fmtHours(row.operator_hours ?? 0);
      const vph = row.verified_decisions_per_operator_hour == null ? '—' : fmtEfficiency(row.verified_decisions_per_operator_hour);
      return `<div class="obs-day"><div class="obs-day-bars"><i title="Verified ${verified}" style="height:${vh}%"></i><i class="gap" title="Evidence gap ${gap}" style="height:${gh}%"></i></div><b>${esc(row.date)}</b><small>Verified ${verified} · Gap ${gap}</small><small>Active ${esc(hours)} · V/OH ${esc(vph)}</small></div>`;
    }).join('');
  }

  function renderBars(id, values, labelMap = {}) {
    const box = $(id);
    const rows = Object.entries(values || {}).sort((a, b) => Number(b[1]) - Number(a[1]));
    if (!rows.length) {
      box.innerHTML = '<div class="obs-availability-row"><div><b>暂无数据</b></div></div>';
      return;
    }
    const max = Math.max(1, ...rows.map(([, value]) => Number(value || 0)));
    box.innerHTML = rows.map(([key, value]) => `<div class="obs-bar-row"><span>${esc(labelMap[key] || key)}</span><div class="obs-bar-track"><i style="width:${Math.round(Number(value || 0) / max * 100)}%"></i></div><b>${Number(value || 0)}</b></div>`).join('');
  }

  function render(data) {
    $('obsTenant').textContent = `Workspace: ${data.tenant_scope || '—'}`;
    $('obsGenerated').textContent = `更新于 ${new Date(Number(data.generated_at || 0) * 1000).toLocaleString('zh-CN')}`;
    renderHero(data);
    renderQuality(data);
    renderReliability(data);
    renderAuthority(data);
    renderAvailability(data);
    renderSeries(data);
    renderBars('obsScenes', data.distribution?.job_scenes, SCENE);
    renderBars('obsJobStatuses', data.distribution?.job_statuses, STATUS);
    renderBars('obsActionStatuses', data.distribution?.action_statuses, STATUS);
  }

  async function load(windowKey = currentWindow) {
    currentWindow = windowKey;
    document.querySelectorAll('[data-window]').forEach(button => button.classList.toggle('active', button.dataset.window === windowKey));
    $('obsGenerated').textContent = '正在刷新…';
    try {
      const data = await api(`/api/runtime/observability?window=${encodeURIComponent(windowKey)}`);
      render(data);
    } catch (error) {
      $('obsGenerated').textContent = '加载失败';
      toast(error.message || '无法加载观测数据');
    }
  }

  document.querySelectorAll('[data-window]').forEach(button => { button.onclick = () => load(button.dataset.window); });
  load('7d');
})();
