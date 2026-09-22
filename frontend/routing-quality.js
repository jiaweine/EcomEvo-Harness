const $ = (id) => document.getElementById(id);
const fmt = (v) => v === null || v === undefined ? "—" : typeof v === "number" ? v.toLocaleString(undefined,{maximumFractionDigits:4}) : String(v);
const pct = (v) => v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;

function metric(label, value, note="") {
  return `<div class="metric"><span>${label}</span><strong>${value}</strong>${note ? `<small>${note}</small>` : ""}</div>`;
}

function stats(label, s) {
  return metric(label, fmt(s?.p50), `p95 ${fmt(s?.p95)} · n=${fmt(s?.samples)}`);
}

function render(data) {
  $("status").textContent = `Tenant ${data.tenant_scope} · ${data.window.key} · ${data.coverage.assistant_results} assistant results · read-only`;
  $("cards").innerHTML = [
    metric("Residual EWMA p50", fmt(data.routing_policy.residual_ewma.p50)),
    metric("Adaptive activation p50", fmt(data.adaptive_routing.activation.p50)),
    metric("Failed-call rate", pct(data.tool_quality.failed_call_rate)),
    metric("Stagnated run rate", pct(data.stagnation.stagnated_run_rate)),
    metric("Tool cost / completed", fmt(data.cost_efficiency.tool_cost_per_completed_run)),
    metric("Evidence tags / call", fmt(data.tool_quality.evidence_tag_yield_per_call)),
  ].join("");

  const domains = data.routing_policy.domains || [];
  $("domains").innerHTML = domains.length ? domains.map((d) => `
    <tr><td>${d.domain}</td><td>${fmt(d.observed_runs)}</td><td>${fmt(d.latest_samples)}</td>
    <td>${fmt(d.latest_reward_ewma)}</td><td>${fmt(d.latest_residual_ewma)}</td><td>${fmt(d.residual_delta)}</td></tr>
  `).join("") : '<tr><td colspan="6">当前窗口暂无 routing policy snapshot</td></tr>';

  $("adaptive").innerHTML = [
    metric("Decision events", fmt(data.adaptive_routing.decision_events)),
    metric("Candidates", fmt(data.adaptive_routing.candidates_observed)),
    metric("Selection rate", pct(data.adaptive_routing.selection_rate)),
    stats("Activation", data.adaptive_routing.activation),
    stats("Reliability", data.adaptive_routing.tool_reliability),
    stats("Diversity overlap", data.adaptive_routing.diversity_overlap),
  ].join("");

  $("tools").innerHTML = [
    metric("Calls", fmt(data.tool_quality.calls)),
    metric("Successful", fmt(data.tool_quality.successful_calls)),
    metric("Failed", fmt(data.tool_quality.failed_calls)),
    metric("Unique tools", fmt(data.tool_quality.unique_tools)),
    metric("Evidence tags yielded", fmt(data.tool_quality.evidence_tags_yielded)),
  ].join("");

  $("authority").innerHTML = Object.entries(data.authority)
    .map(([k,v]) => metric(k.replaceAll("_"," "), v ? "true" : "false"))
    .join("");
}

async function load() {
  $("status").textContent = "加载中…";
  const response = await fetch(`/api/runtime/routing-quality?window=${encodeURIComponent($("window").value)}`);
  if (!response.ok) {
    $("status").textContent = `加载失败：HTTP ${response.status}`;
    return;
  }
  render(await response.json());
}
$("window").addEventListener("change", load);
load();
