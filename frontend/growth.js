(() => {
  "use strict";

  const state = { dashboard: null, opportunities: [], metric: "incremental_revenue", sidecarOpen: false };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[ch]));
  const money = (value) => `¥ ${Math.round(Number(value || 0)).toLocaleString("zh-CN")}`;
  const compactMoney = (value) => {
    const n = Number(value || 0);
    if (n >= 100000000) return `¥${(n / 100000000).toFixed(2)}亿`;
    if (n >= 10000) return `¥${(n / 10000).toFixed(n >= 1000000 ? 0 : 1)}万`;
    return money(n);
  };
  const api = async (url, options = {}) => {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    const response = await fetch(url, { ...options, headers });
    if (!response.ok) {
      let detail = `请求失败 (${response.status})`;
      try { detail = (await response.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return response.status === 204 ? null : response.json();
  };

  function toast(message, kind = "success") {
    const stack = $("#toastStack");
    if (!stack) return;
    const node = document.createElement("div");
    node.className = `toast ${kind}`;
    node.textContent = message;
    stack.appendChild(node);
    window.setTimeout(() => node.remove(), 3400);
  }

  function statusClass(status) {
    return ["running", "canary", "analyzing"].includes(status) ? status : "";
  }

  function statusText(status) {
    return ({ running: "运行中", canary: "5% Canary", analyzing: "分析中", paused: "已暂停", draft: "草稿", awaiting_approval: "待审批" })[status] || status;
  }

  function renderSpark(target, values, color = "#5b5cf0") {
    const host = $(target);
    if (!host || !values.length) return;
    const min = Math.min(...values), max = Math.max(...values), span = Math.max(1, max - min);
    const pts = values.map((v, i) => `${(i / (values.length - 1 || 1)) * 88},${25 - ((v - min) / span) * 20}`).join(" ");
    host.innerHTML = `<svg viewBox="0 0 88 27" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="sg-${target.slice(1)}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".18"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs><polygon points="0,27 ${pts} 88,27" fill="url(#sg-${target.slice(1)})"/><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>`;
  }

  function chartPath(points) {
    if (!points.length) return "";
    return points.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(2)},${p[1].toFixed(2)}`).join(" ");
  }

  function renderTrend() {
    const svg = $("#trendChart");
    const dashboard = state.dashboard;
    if (!svg || !dashboard) return;
    const data = dashboard.time_series || [];
    const metric = state.metric;
    if (!data.length) return;
    const width = 760, height = 270, left = 46, right = 12, top = 22, bottom = 34;
    const values = data.map((row) => Number(row[metric] || 0));
    const vmax = Math.max(...values) * 1.13;
    const vmin = Math.min(0, Math.min(...values) * .92);
    const plotW = width - left - right, plotH = height - top - bottom;
    const x = (i) => left + (i / Math.max(1, data.length - 1)) * plotW;
    const y = (v) => top + (1 - (v - vmin) / Math.max(1, vmax - vmin)) * plotH;
    const points = values.map((v, i) => [x(i), y(v)]);
    const forecastUpper = points.map(([px, py]) => [px, Math.max(top, py - 14)]);
    const forecastLower = points.map(([px, py]) => [px, Math.min(top + plotH, py + 18)]).reverse();
    const grid = [0, .25, .5, .75, 1].map((r) => {
      const py = top + r * plotH;
      const label = ((vmax - (vmax - vmin) * r) / 10000).toFixed(0) + "万";
      return `<line x1="${left}" y1="${py}" x2="${width-right}" y2="${py}" stroke="#edf0f5" stroke-width="1"/><text x="${left-8}" y="${py+3}" text-anchor="end" fill="#9aa3b1" font-size="9">${label}</text>`;
    }).join("");
    const tickStep = Math.max(1, Math.floor(data.length / 6));
    const ticks = data.map((row, i) => i % tickStep === 0 || i === data.length - 1 ? `<text x="${x(i)}" y="${height-9}" text-anchor="middle" fill="#9aa3b1" font-size="9">${esc(row.date.slice(5))}</text>` : "").join("");
    const bars = data.map((row, i) => {
      const barValue = Number(row.avoided_cost || 0);
      const barH = (barValue / Math.max(...data.map((r) => Number(r.avoided_cost || 0)))) * 48;
      return `<rect x="${x(i)-3}" y="${top+plotH-barH}" width="6" height="${barH}" rx="2" fill="#dbe5ff" opacity=".62"/>`;
    }).join("");
    const band = [...forecastUpper, ...forecastLower];
    const bandPoints = band.map((p) => `${p[0]},${p[1]}`).join(" ");
    svg.innerHTML = `<defs><linearGradient id="trendArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6570f4" stop-opacity=".2"/><stop offset="1" stop-color="#6570f4" stop-opacity="0"/></linearGradient></defs>${grid}${bars}<polygon points="${bandPoints}" fill="#dfe6ff" opacity=".36"/><path d="${chartPath(points)} L${points.at(-1)[0]},${top+plotH} L${points[0][0]},${top+plotH} Z" fill="url(#trendArea)"/><path d="${chartPath(points)}" fill="none" stroke="#5b5cf0" stroke-width="2" vector-effect="non-scaling-stroke"/>${points.map((p,i) => i === values.length-1 ? `<circle cx="${p[0]}" cy="${p[1]}" r="4" fill="#5b5cf0" stroke="white" stroke-width="2"/>` : "").join("")}${ticks}<rect class="trend-hit" x="${left}" y="${top}" width="${plotW}" height="${plotH}" fill="transparent"/>`;

    const hit = $(".trend-hit", svg), tip = $("#chartTooltip");
    if (hit && tip) {
      hit.addEventListener("pointermove", (event) => {
        const box = svg.getBoundingClientRect();
        const ratio = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
        const index = Math.min(data.length - 1, Math.max(0, Math.round(ratio * (data.length - 1))));
        const row = data[index];
        const pointX = (x(index) / width) * box.width;
        const pointY = (y(Number(row[metric] || 0)) / height) * box.height;
        tip.innerHTML = `<strong>${esc(row.date)}</strong><div>${metric === "incremental_revenue" ? "增量收入" : metric === "incremental_profit" ? "增量利润" : "节省成本"}　${compactMoney(row[metric])}</div><div style="margin-top:3px;color:#8b94a3">Evidence-aware estimate</div>`;
        tip.style.left = `${pointX}px`; tip.style.top = `${pointY}px`; tip.style.opacity = "1";
      });
      hit.addEventListener("pointerleave", () => { tip.style.opacity = "0"; });
    }
  }

  function renderCampaigns(rows) {
    const body = $("#campaignRows");
    if (!body) return;
    $("#campaignCount").textContent = rows.length;
    $("#runningCount").textContent = rows.filter((row) => ["running", "canary", "analyzing"].includes(row.status)).length;
    body.innerHTML = rows.map((row) => `<tr>
      <td><span class="campaign-name"><span class="campaign-thumb">${esc(row.name.slice(0,1))}</span>${esc(row.name)}</span></td>
      <td>${esc(row.campaign_type)}</td><td><span class="status-pill ${statusClass(row.status)}">${esc(statusText(row.status))}</span></td>
      <td>${esc(row.objective)}</td><td>${Number(row.audience_size).toLocaleString("zh-CN")}</td><td>${esc(row.starts_at || "—")}</td>
      <td class="${Number(row.expected_lift)>=0 ? "lift-positive" : "lift-negative"}">${Number(row.expected_lift)>=0?"+":""}${Number(row.expected_lift).toFixed(1)}%</td>
      <td><button class="row-action" data-canary="${esc(row.campaign_id)}">${row.status === "draft" ? "申请 Canary" : "查看"}</button></td>
    </tr>`).join("") || `<tr><td colspan="8" class="loading-cell">暂无 Campaign</td></tr>`;
    $$('[data-canary]', body).forEach((button) => button.addEventListener("click", async () => {
      const row = rows.find((item) => item.campaign_id === button.dataset.canary);
      if (!row) return;
      if (row.status !== "draft") { toast(`${row.name}：当前状态 ${statusText(row.status)}`); return; }
      try {
        await api(`/api/growth/campaigns/${encodeURIComponent(row.campaign_id)}/canary-request`, { method: "POST" });
        toast("已创建 Canary 审批请求。"); await refreshDashboard();
      } catch (error) { toast(error.message, "error"); }
    }));
  }

  function renderTodos(approvals) {
    const host = $("#todoList");
    if (!host) return;
    const extras = [
      {icon:"▣",klass:"blue",title:"实验结果已出",desc:"新用户首购激励（7日）",badge:"3"},
      {icon:"⌕",klass:"green",title:"模型需要重新训练",desc:"数据分布发生变化",badge:"1"},
      {icon:"!",klass:"",title:"渠道发送异常",desc:"短信通道失败率上升",badge:"1"},
    ];
    const approvalItems = approvals.slice(0,2).map((row) => ({ icon:"◎", klass:"", title:row.title, desc:`预计覆盖 ${Number(row.blast_radius).toLocaleString("zh-CN")} 人 · ${money(row.budget_rmb)}`, badge:"审批", approval:row.approval_id }));
    const items = [...approvalItems, ...extras].slice(0,5);
    host.innerHTML = items.map((item) => `<div class="todo-item"><span class="todo-icon ${item.klass}">${item.icon}</span><span class="todo-copy"><strong>${esc(item.title)}</strong><span>${esc(item.desc)}</span></span>${item.approval ? `<button class="row-action" data-approval="${esc(item.approval)}">审批</button>` : `<span class="todo-badge">${esc(item.badge)}</span>`}</div>`).join("");
    $$('[data-approval]', host).forEach((button) => button.addEventListener("click", async () => {
      const approval = approvals.find((row) => row.approval_id === button.dataset.approval);
      if (!approval) return;
      const approved = window.confirm(`批准「${approval.title}」进入 Canary？\n\n这只会推进到受限流量 Canary，不会绕过后续执行治理。`);
      try {
        await api(`/api/growth/approvals/${encodeURIComponent(approval.approval_id)}/decision`, { method:"POST", body:JSON.stringify({decision: approved ? "approve" : "reject", note:"Growth cockpit decision"}) });
        toast(approved ? "审批通过，Campaign 已进入 Canary。" : "已驳回并退回 Draft。", approved ? "success" : "error");
        await refreshDashboard();
      } catch (error) { toast(error.message, "error"); }
    }));
  }

  function renderAgentRun(run) {
    if (!run) return;
    const steps = $("#agentSteps"), artifacts = $("#agentArtifacts");
    if (steps) steps.innerHTML = (run.steps || []).map((step) => `<div class="run-step ${esc(step.status)}">${esc(step.label)}</div>`).join("");
    if (artifacts) artifacts.innerHTML = (run.artifacts || []).map((item) => `<span class="artifact-chip">${esc(item.type)} · ${esc(item.status)}</span>`).join("");
  }

  function renderOpportunities(rows) {
    const host = $("#opportunityGrid"); if (!host) return;
    host.innerHTML = rows.map((row) => `<article class="opportunity-card"><div class="opportunity-top"><h3>${esc(row.title)}</h3><span class="evidence-badge">Evidence ${esc(row.evidence_tier)}</span></div><div class="opportunity-metric">${compactMoney(row.max_incremental_revenue)}</div><p>${esc(row.recommended_action)}</p><div class="opportunity-meta"><span>人群 ${Number(row.audience).toLocaleString("zh-CN")}</span><span>Uplift +${Number(row.estimated_uplift_pp).toFixed(1)}pp</span><span>Support ${(Number(row.support)*100).toFixed(0)}%</span></div></article>`).join("");
  }

  function renderDashboard(data) {
    state.dashboard = data;
    const k = data.kpis || {};
    $("#kpiRevenue").textContent = money(k.incremental_revenue);
    $("#kpiProfit").textContent = money(k.incremental_profit);
    $("#kpiRoi").textContent = `${Number(k.incremental_roi || 0).toFixed(2)}x`;
    $("#kpiAvoided").textContent = money(k.avoided_cost);
    $("#latencyValue").textContent = data.system?.decision_latency_ms ?? "—";
    $("#freshnessValue").textContent = data.system?.data_freshness_minutes ?? "—";
    if (data.data_mode !== "reference_seed") $("#referenceBanner")?.remove();
    renderCampaigns(data.campaigns || []);
    renderTodos(data.approvals || []);
    renderAgentRun(data.active_agent_run);
    const series = data.time_series || [];
    renderSpark("#sparkRevenue", series.slice(-12).map((x)=>x.incremental_revenue), "#4d8fff");
    renderSpark("#sparkProfit", series.slice(-12).map((x)=>x.incremental_profit), "#7655f3");
    renderSpark("#sparkRoi", series.slice(-12).map((x)=>x.incremental_profit / Math.max(1,x.incremental_revenue*.226)), "#16ad72");
    renderSpark("#sparkAvoided", series.slice(-12).map((x)=>x.avoided_cost), "#f2a11b");
    renderTrend();
    const colors = ["#625cf4","#7d86f9","#4f9de8","#2fb9d5","#16b981","#8ac7c3","#9aa5b4"];
    const channelHost = $("#channelList");
    if (channelHost) channelHost.innerHTML = (data.channel_mix || []).map((row,i)=>`<div class="channel-row"><i class="channel-color" style="background:${colors[i%colors.length]}"></i><span>${esc(row.name)}</span><strong>${Number(row.share)}%</strong></div>`).join("");
    $("#donutTotal").textContent = compactMoney(k.incremental_revenue).replace(/^¥/,"¥ ");
    renderMiniChart();
  }

  function renderMiniChart() {
    const svg = $("#miniResultChart"); if (!svg) return;
    const values = [11,13,12,18,16,21,25,24,29,31,35,34,39,43];
    const min=Math.min(...values),max=Math.max(...values); const pts=values.map((v,i)=>`${(i/(values.length-1))*120},${36-((v-min)/(max-min))*30}`).join(" ");
    svg.innerHTML = `<polyline points="${pts}" fill="none" stroke="#19aa70" stroke-width="1.7" vector-effect="non-scaling-stroke"/>`;
  }

  async function refreshDashboard() {
    const days = Number($("#periodSelect")?.value || 30);
    try { renderDashboard(await api(`/api/growth/dashboard?days=${days}`)); }
    catch (error) { toast(error.message, "error"); }
  }

  async function loadOpportunities() {
    try { state.opportunities = await api("/api/growth/opportunities"); renderOpportunities(state.opportunities); }
    catch (error) { toast(error.message, "error"); }
  }

  function openAgent() {
    const sidecar=$("#agentSidecar"), backdrop=$("#drawerBackdrop");
    sidecar?.classList.add("open"); backdrop?.classList.add("show"); state.sidecarOpen=true;
    $("#agentPrompt")?.focus();
  }
  function closeDrawers() {
    $("#agentSidecar")?.classList.remove("open"); $(".sidebar")?.classList.remove("open"); $("#drawerBackdrop")?.classList.remove("show"); state.sidecarOpen=false;
  }

  function bindAgent() {
    $("#agentOpenBtn")?.addEventListener("click", openAgent); $("#mobileAgentBtn")?.addEventListener("click", openAgent); $("#drawerBackdrop")?.addEventListener("click", closeDrawers);
    $("#agentForm")?.addEventListener("submit", async (event) => {
      event.preventDefault(); const input=$("#agentPrompt"); const goal=input?.value.trim(); if (!goal) return;
      input.value=""; openAgent();
      const scroll=$("#agentScroll"); const msg=document.createElement("div"); msg.className="agent-message user-message"; msg.innerHTML=`<div class="avatar small">L</div><div><span class="message-time">刚刚</span><p>${esc(goal)}</p></div>`; scroll?.appendChild(msg); if(scroll) scroll.scrollTop=scroll.scrollHeight;
      const steps=$("#agentSteps"); if(steps) steps.innerHTML=`<div class="skeleton-line"></div><div class="skeleton-line short"></div><div class="skeleton-line"></div>`;
      try { const run=await api("/api/growth/agent/plan",{method:"POST",body:JSON.stringify({goal})}); renderAgentRun(run); toast("已生成受治理候选方案，未触发外部执行。"); }
      catch(error){ toast(error.message,"error"); }
    });
  }

  function bindCampaignModal() {
    const modal=$("#campaignModal"), form=$("#campaignForm");
    const open=()=>modal?.showModal();
    ["#newCampaignBtn","#quickCampaign","#mobileCreateBtn"].forEach((selector)=>$(selector)?.addEventListener("click",open));
    form?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitter=event.submitter; if(submitter?.value === "cancel"){ modal.close(); return; }
      const data=Object.fromEntries(new FormData(form).entries());
      ["audience_size","budget_rmb","canary_percent"].forEach((key)=>data[key]=Number(data[key]||0));
      try { await api("/api/growth/campaigns",{method:"POST",body:JSON.stringify(data)}); modal.close(); form.reset(); toast("Campaign Draft 已创建。下一步可请求 Shadow / Approval / Canary。"); await refreshDashboard(); }
      catch(error){ toast(error.message,"error"); }
    });
  }

  function bindCommandPalette() {
    const modal=$("#commandModal"), trigger=$("#commandTrigger"), input=$("#commandInput");
    const open=()=>{ modal?.showModal(); window.setTimeout(()=>input?.focus(),20); };
    trigger?.addEventListener("click",open);
    document.addEventListener("keydown",(event)=>{ if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="k"){event.preventDefault();open();} if(event.key==="Escape") closeDrawers(); });
    $$('[data-command]').forEach((button)=>button.addEventListener("click",()=>{ const command=button.dataset.command; modal?.close(); if(command==="campaign") $("#campaignModal")?.showModal(); if(command==="agent") openAgent(); if(command==="evidence") toast("Decision Log 已接入 /api/growth/decisions，可在 Evidence 视图继续展开。"); }));
  }

  function bindNavigation() {
    $("#periodSelect")?.addEventListener("change",refreshDashboard);
    $$('[data-metric]').forEach((button)=>button.addEventListener("click",()=>{ $$('[data-metric]').forEach((node)=>node.classList.remove("active")); button.classList.add("active"); state.metric=button.dataset.metric; renderTrend(); }));
    $("#mobileMenu")?.addEventListener("click",()=>{ $(".sidebar")?.classList.add("open"); $("#drawerBackdrop")?.classList.add("show"); });
    $("#quickOpportunity")?.addEventListener("click",()=>$(".opportunity-panel")?.scrollIntoView({behavior:"smooth",block:"center"}));
    $("#quickCreative")?.addEventListener("click",()=>{ openAgent(); const input=$("#agentPrompt"); if(input) input.value="基于当前高增量机会，生成 6 个品牌合规的创意候选，并为每个候选建立 treatment identity。"; });
    $("#quickData")?.addEventListener("click",()=>toast("数据中心入口已保留；当前版本继续复用现有 Connection Center。"));
    $$('[data-view]').forEach((button)=>button.addEventListener("click",()=>{ if(button.dataset.view==="dashboard") return; if(button.dataset.view==="agent") return openAgent(); toast(`${button.textContent.trim()} 已纳入产品 IA；本次 v1 首先交付增长驾驶舱与治理闭环。`); }));
  }

  async function init() {
    bindNavigation(); bindAgent(); bindCampaignModal(); bindCommandPalette();
    await Promise.all([refreshDashboard(), loadOpportunities()]);
    if ("serviceWorker" in navigator && location.protocol.startsWith("http")) navigator.serviceWorker.register("/assets/growth-sw.js").catch(()=>{});
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
