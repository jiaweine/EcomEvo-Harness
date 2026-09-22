const $ = (id) => document.getElementById(id);
let catalog = null;

function option(value, label=value) {
  return `<option value="${value}">${label}</option>`;
}

function parseObject(id) {
  const raw = $(id).value.trim();
  if (!raw) return {};
  const value = JSON.parse(raw);
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw new Error(`${id} 必须是 JSON object`);
  }
  return value;
}

function refreshMutations() {
  const rows = catalog?.surfaces?.[$("surface").value] || [];
  $("mutation").innerHTML = rows.map((row) => option(row.mutation, `${row.mutation} · ${row.class}`)).join("");
}

function metric(label, value) {
  return `<div class="metric"><span>${label}</span><b>${String(value ?? "—")}</b></div>`;
}

function render(result) {
  const expected = result.expected_control || {};
  $("summary").innerHTML = [
    metric("Candidate", result.candidate_id),
    metric("Runtime outcome", expected.runtime_outcome),
    metric("Automatic retry", expected.automatic_retry_allowed),
    metric("Business state check", expected.requires_business_state_check),
    metric("Schema revalidation", expected.requires_schema_revalidation),
    metric("Real system invoked", result.replay_candidate?.invokes_real_system),
    metric("Production evidence", result.replay_candidate?.production_evidence),
  ].join("");
  $("result").textContent = JSON.stringify(result, null, 2);
  $("status").textContent = "已生成 deterministic offline replay candidate";
}

async function loadCatalog() {
  const response = await fetch("/api/runtime/shadow/catalog");
  if (!response.ok) throw new Error(`catalog HTTP ${response.status}`);
  catalog = await response.json();
  $("tenantChip").textContent = `tenant: ${catalog.tenant_scope}`;
  $("surface").innerHTML = Object.keys(catalog.surfaces || {}).map((name) => option(name)).join("");
  $("operation").innerHTML = (catalog.operations || []).map((name) => option(name)).join("");
  refreshMutations();
  $("status").textContent = "Catalog loaded · offline only";
}

$("surface").addEventListener("change", refreshMutations);
$("scenarioForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("simulateBtn").disabled = true;
  $("status").textContent = "生成中…";
  try {
    const payload = {
      surface: $("surface").value,
      operation: $("operation").value,
      mutation: $("mutation").value,
      target: $("target").value,
      context_labels: $("labels").value.split(",").map((v) => v.trim()).filter(Boolean),
      baseline_schema: parseObject("baseline"),
      mutated_schema: parseObject("mutated"),
    };
    const response = await fetch("/api/runtime/shadow/simulate", {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    render(body);
  } catch (error) {
    $("status").textContent = `失败：${error.message || error}`;
  } finally {
    $("simulateBtn").disabled = false;
  }
});

loadCatalog().catch((error) => {
  $("status").textContent = `加载失败：${error.message || error}`;
});
