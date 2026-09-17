(() => {
  const $ = (id) => document.getElementById(id);
  let versions = [];
  let selected = null;
  let toastTimer = null;

  const api = async (path, options = {}) => {
    const response = await fetch(path, {
      ...options,
      headers: {"content-type": "application/json", ...(options.headers || {})},
    });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) {
      const detail = typeof payload?.detail === "string" ? payload.detail : `请求失败 (${response.status})`;
      throw new Error(detail);
    }
    return payload;
  };

  const toast = (message, error = false) => {
    const node = $("toast");
    node.textContent = message;
    node.classList.toggle("error", error);
    node.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove("show"), 3200);
  };

  const chip = (text, extra = "") => {
    const node = document.createElement("span");
    node.className = `status-chip ${extra}`.trim();
    node.textContent = text;
    return node;
  };

  const pretty = (value) => JSON.stringify(value ?? {}, null, 2);
  const shortHash = (value) => value ? `${String(value).slice(0, 12)}…` : "—";
  const endpoint = (item, suffix) => `/api/runtime/policies/${encodeURIComponent(item.policy_id)}/versions/${item.version}/${suffix}`;

  const addMeta = (root, name, value) => {
    const wrap = document.createElement("div");
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = name;
    dd.textContent = value ?? "—";
    wrap.append(dt, dd);
    root.append(wrap);
  };

  const renderAudit = (events) => {
    const root = $("auditList");
    root.replaceChildren();
    if (!events?.length) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "暂无 workflow audit 事件。";
      root.append(empty);
      return;
    }
    [...events].reverse().forEach((event) => {
      const row = document.createElement("div");
      row.className = "audit-item";
      const title = document.createElement("b");
      const meta = document.createElement("small");
      title.textContent = event.event_type;
      meta.textContent = `${event.actor_id} · ${event.created_at} · source ${shortHash(event.source_hash)}`;
      row.append(title, meta);
      if (event.payload && Object.keys(event.payload).length) {
        const payload = document.createElement("small");
        payload.textContent = pretty(event.payload);
        row.append(payload);
      }
      root.append(row);
    });
  };

  const renderPreview = (preview) => {
    const root = $("previewCard");
    root.replaceChildren();
    root.classList.remove("muted");
    const resolution = preview?.resolution || {};
    const head = document.createElement("div");
    head.className = "preview-head";
    const label = document.createElement("strong");
    label.textContent = preview?.operation === "retire" ? "退役预览" : "发布预览";
    head.append(label, chip(resolution.status || "unknown", resolution.status || ""));
    root.append(head);
    const body = document.createElement("div");
    body.textContent = `生产状态未修改 · preview ${shortHash(preview?.preview_hash)} · policies ${(resolution.policies || []).length} · conflicts ${(resolution.conflicts || []).length}`;
    root.append(body);
    const controls = document.createElement("pre");
    controls.textContent = pretty(resolution.controls || {});
    root.append(controls);
  };

  const renderDetail = (item) => {
    selected = item;
    $("detailEmpty").hidden = true;
    $("detailContent").hidden = false;
    $("detailState").textContent = item.workflow_state || item.status;

    const meta = $("versionMeta");
    meta.replaceChildren();
    addMeta(meta, "Version", item.version_id);
    addMeta(meta, "Status", `${item.status} / ${item.workflow_state}`);
    addMeta(meta, "Owner", item.owner || "—");
    addMeta(meta, "Approver", item.approver || item.latest_approval?.actor_id || "—");
    addMeta(meta, "Authority / Priority", `${item.authority} / ${item.priority}`);
    addMeta(meta, "Source hash", item.source_hash);
    addMeta(meta, "Source", item.source || "—");
    addMeta(meta, "Created", item.created_at);

    const rules = $("detailRules");
    rules.replaceChildren();
    (item.rules || []).forEach((rule) => {
      const node = document.createElement("div");
      node.className = "rule-item";
      node.textContent = rule;
      rules.append(node);
    });
    $("detailControls").textContent = pretty(item.controls || {});
    $("detailScope").textContent = pretty(item.scope || {});
    renderAudit(item.events || []);
    $("previewCard").textContent = "尚未运行预览。";
    $("previewCard").classList.add("muted");

    $("draftActions").hidden = item.status !== "draft";
    $("activeActions").hidden = item.status !== "active";
    $("publishBtn").disabled = item.workflow_state !== "approved";
    $("retireBtn").disabled = !item.retirement_request;
  };

  const loadDetail = async (item, quiet = false) => {
    try {
      const value = await api(endpoint(item, "workflow"));
      renderDetail(value);
      const index = versions.findIndex((row) => row.version_id === value.version_id);
      if (index >= 0) versions[index] = value;
      renderVersions();
    } catch (error) {
      if (!quiet) toast(error.message, true);
    }
  };

  const renderVersions = () => {
    const root = $("versionList");
    root.replaceChildren();
    if (!versions.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "当前工作区还没有自定义政策版本。Builtin policies 仍由只读列表与 runtime resolution 提供。";
      root.append(empty);
      return;
    }
    versions.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `version-card${selected?.version_id === item.version_id ? " active" : ""}`;
      const top = document.createElement("div");
      top.className = "version-card-top";
      const title = document.createElement("strong");
      title.textContent = item.version_id;
      top.append(title, chip(item.workflow_state || item.status));
      const detail = document.createElement("small");
      detail.textContent = `${item.domain} · authority ${item.authority} · ${item.source || "no source"}`;
      button.append(top, detail);
      button.addEventListener("click", () => loadDetail(item));
      root.append(button);
    });
  };

  const refresh = async () => {
    try {
      const data = await api("/api/runtime/policies/workflow?limit=200");
      versions = data.items || [];
      renderVersions();
      if (selected) {
        const same = versions.find((item) => item.version_id === selected.version_id);
        if (same) await loadDetail(same, true);
      }
    } catch (error) {
      toast(error.message, true);
    }
  };

  const resolveCurrent = async () => {
    const domain = $("resolveDomain").value;
    const root = $("resolutionCard");
    root.textContent = "正在解析…";
    root.classList.add("muted");
    try {
      const data = await api(`/api/runtime/policies/resolve?domain=${encodeURIComponent(domain)}`);
      root.replaceChildren();
      root.classList.remove("muted");
      const head = document.createElement("div");
      head.className = "resolution-head";
      const title = document.createElement("strong");
      title.textContent = `${domain} · 当前 runtime resolution`;
      head.append(title, chip(data.status || "unknown", data.status || ""));
      const summary = document.createElement("div");
      summary.textContent = `版本 ${(data.policy_versions || data.policies || []).length} · conflicts ${(data.conflicts || []).length} · hash ${shortHash(data.resolution_hash)}`;
      const controls = document.createElement("pre");
      controls.textContent = pretty(data.controls_authority || data.controls || {});
      root.append(head, summary, controls);
    } catch (error) {
      root.textContent = error.message;
      root.classList.add("muted");
    }
  };

  $("draftForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    let controls, scope;
    try {
      controls = JSON.parse($("policyControls").value || "{}");
      scope = JSON.parse($("policyScope").value || "{}");
      if (!controls || Array.isArray(controls) || typeof controls !== "object") throw new Error("Controls 必须是 JSON object");
      if (!scope || Array.isArray(scope) || typeof scope !== "object") throw new Error("Scope 必须是 JSON object");
    } catch (error) {
      toast(error.message || "JSON 格式错误", true);
      return;
    }
    const rules = $("policyRules").value.split("\n").map((line) => line.trim()).filter(Boolean);
    const payload = {
      policy_key: $("policyKey").value.trim(),
      domain: $("policyDomain").value,
      rules,
      controls,
      scope,
      authority: Number($("policyAuthority").value),
      priority: Number($("policyPriority").value),
      source: $("policySource").value.trim(),
    };
    try {
      const created = await api("/api/runtime/policies/drafts", {method: "POST", body: JSON.stringify(payload)});
      toast(`已创建 ${created.version_id}；尚未生效`);
      await refresh();
      await loadDetail(created);
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("previewPublishBtn").addEventListener("click", async () => {
    if (!selected) return;
    try { renderPreview(await api(endpoint(selected, "preview-publish"))); }
    catch (error) { toast(error.message, true); }
  });

  $("approveBtn").addEventListener("click", async () => {
    if (!selected) return;
    if (!confirm("确认以 Checker 身份审批此 immutable 版本？创建人不能审批自己的版本；审批仍不会使政策生效。")) return;
    try {
      const result = await api(endpoint(selected, "approve"), {method: "POST", body: JSON.stringify({effective_from: null, note: "Approved in Policy Center"})});
      toast("审批通过；仍未发布");
      renderDetail(result);
      renderPreview(result.preview);
      await refresh();
    } catch (error) { toast(error.message, true); }
  });

  $("rejectBtn").addEventListener("click", async () => {
    if (!selected) return;
    const note = prompt("填写拒绝原因。内容不可直接修改，需要创建新版本。", "需要调整政策内容");
    if (!note) return;
    try {
      const result = await api(endpoint(selected, "reject"), {method: "POST", body: JSON.stringify({note})});
      toast("已记录拒绝意见");
      renderDetail(result);
      await refresh();
    } catch (error) { toast(error.message, true); }
  });

  $("publishBtn").addEventListener("click", async () => {
    if (!selected) return;
    if (!confirm("发布会改变当前工作区的生产政策解析。系统会重新校验审批绑定的 preview hash；确认继续？")) return;
    try {
      const result = await api(endpoint(selected, "publish"), {method: "POST", body: "{}"});
      toast("政策版本已发布并写入原子审计");
      renderDetail(result);
      await refresh();
      await resolveCurrent();
    } catch (error) { toast(error.message, true); }
  });

  $("previewRetireBtn").addEventListener("click", async () => {
    if (!selected) return;
    try { renderPreview(await api(endpoint(selected, "preview-retire"))); }
    catch (error) { toast(error.message, true); }
  });

  $("requestRetireBtn").addEventListener("click", async () => {
    if (!selected) return;
    if (!confirm("申请退役不会立即下线政策；系统会先模拟退役后的 resolver 状态，并要求另一管理员执行。")) return;
    try {
      const result = await api(endpoint(selected, "retirement-requests"), {method: "POST", body: JSON.stringify({effective_to: null, note: "Retirement requested in Policy Center"})});
      toast("退役申请已记录；等待另一管理员执行");
      renderDetail(result);
      renderPreview(result.preview);
      await refresh();
    } catch (error) { toast(error.message, true); }
  });

  $("retireBtn").addEventListener("click", async () => {
    if (!selected) return;
    if (!confirm("退役会改变生产 policy resolution，并要求当前管理员不是申请人。确认继续？")) return;
    try {
      const result = await api(endpoint(selected, "retire"), {method: "POST", body: "{}"});
      toast("政策已退役并写入原子审计");
      renderDetail(result);
      await refresh();
      await resolveCurrent();
    } catch (error) { toast(error.message, true); }
  });

  $("refreshBtn").addEventListener("click", refresh);
  $("resolveBtn").addEventListener("click", resolveCurrent);
  $("resolveDomain").addEventListener("change", resolveCurrent);

  Promise.all([refresh(), resolveCurrent()]).catch((error) => toast(error.message, true));
})();
