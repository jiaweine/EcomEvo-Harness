(() => {
  const $ = (id) => document.getElementById(id);
  const DOMAINS = [
    ["product_governance", "商品治理"],
    ["merchant_review", "商家审核"],
    ["aftersales", "售后判责"],
    ["risk_review", "风险核查"],
    ["content_audit", "内容审核"],
    ["general", "通用"],
  ];
  const state = { catalog: null, selected: null };

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = String(text);
    return item;
  }

  function toast(message) {
    const box = $("studioToast");
    box.textContent = String(message || "");
    box.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => box.classList.remove("show"), 2200);
  }

  async function request(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: { "content-type": "application/json", ...(options.headers || {}) },
    });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) {
      const detail = payload && payload.detail ? payload.detail : `HTTP ${response.status}`;
      throw new Error(String(detail));
    }
    return payload;
  }

  function fmtTime(value) {
    if (!value) return "—";
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    }).format(new Date(Number(value) * 1000));
  }

  function statusLabel(value) {
    return {
      draft: "草稿",
      review: "评审中",
      evaluated_pass: "评估通过 · 未上线",
      evaluated_fail: "评估未通过",
      archived: "已归档",
      active: "Active",
      shadow: "Shadow",
      retired: "Retired",
    }[value] || value || "—";
  }

  function pill(value) {
    let kind = "";
    if (value === "evaluated_pass" || value === "active") kind = " pass";
    else if (value === "evaluated_fail" || value === "retired") kind = " fail";
    else if (value === "review" || value === "shadow") kind = " review";
    return node("span", `pill${kind}`, statusLabel(value));
  }

  function fillSelectors(catalog) {
    const domain = $("domain");
    domain.replaceChildren();
    DOMAINS.forEach(([value, label]) => {
      const option = node("option", "", label);
      option.value = value;
      domain.append(option);
    });
    const tools = $("preferredTools");
    tools.replaceChildren();
    (catalog.registered_tools || []).forEach((key) => {
      const option = node("option", "", key);
      option.value = key;
      tools.append(option);
    });
  }

  function renderSummary(catalog) {
    const box = $("studioSummary");
    box.replaceChildren();
    const runtime = catalog.runtime_skills || [];
    const families = catalog.studio_families || [];
    const cards = [
      ["Runtime active", runtime.filter((x) => x.status === "active").length],
      ["Shadow candidates", runtime.filter((x) => x.status === "shadow").length],
      ["Studio families", families.length],
      ["Evaluated pass · 未上线", families.filter((x) => x.state === "evaluated_pass").length],
    ];
    cards.forEach(([label, value]) => {
      const card = node("div", "summary-card");
      card.append(node("small", "", label), node("strong", "", value));
      box.append(card);
    });
  }

  function renderFamilies(catalog) {
    const box = $("studioFamilies");
    box.replaceChildren();
    const rows = catalog.studio_families || [];
    if (!rows.length) {
      box.append(node("div", "empty", "还没有人工流程版本。左侧创建的 v1 不会自动进入 Runtime。"));
      return;
    }
    rows.forEach((item) => {
      const card = node("article", "skill-card");
      card.tabIndex = 0;
      const top = node("div", "card-top");
      const title = node("h3", "", item.name);
      top.append(title, pill(item.state));
      const meta = node("div", "card-meta");
      meta.append(
        node("span", "pill", `${item.family_id} · v${item.version}`),
        node("span", "pill", item.domain),
        node("span", "pill", fmtTime(item.created_at)),
      );
      card.append(top, meta, node("p", "card-copy", item.purpose));
      card.addEventListener("click", () => openVersion(item.version_id));
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") openVersion(item.version_id);
      });
      box.append(card);
    });
  }

  function renderRuntime(catalog) {
    const box = $("runtimeSkills");
    box.replaceChildren();
    const rows = catalog.runtime_skills || [];
    if (!rows.length) {
      box.append(node("div", "empty", "当前还没有 runtime skill。Studio 版本不会自动填入这里。"));
      return;
    }
    rows.slice(0, 30).forEach((item) => {
      const card = node("article", "runtime-card");
      const top = node("div", "card-top");
      top.append(node("h3", "", item.name || item.skill_id), pill(item.status));
      const meta = node("div", "card-meta");
      meta.append(node("span", "pill", item.domain), node("span", "pill", item.skill_id));
      const stats = node("dl", "");
      [["Uses", item.uses], ["Wins", item.wins], ["Losses", item.losses]].forEach(([key, value]) => {
        const wrap = node("div", "");
        wrap.append(node("dt", "", key), node("dd", "", value));
        stats.append(wrap);
      });
      card.append(top, meta, stats);
      box.append(card);
    });
  }

  function renderPolicies(catalog) {
    const box = $("skillPolicies");
    box.replaceChildren();
    const rows = catalog.evolution_policies || [];
    if (!rows.length) {
      box.append(node("div", "empty", "暂无 runtime evolution policy。"));
      return;
    }
    rows.forEach((item) => {
      const card = node("div", "policy-item");
      card.append(node("b", "", item.domain));
      const dl = node("dl", "");
      [["Promotion", item.promotion_threshold], ["Retirement", item.retirement_threshold], ["Exploration", item.exploration], ["Updates", item.updates]].forEach(([key, value]) => {
        const wrap = node("div", "");
        wrap.append(node("dt", "", key), node("dd", "", value));
        dl.append(wrap);
      });
      card.append(dl);
      box.append(card);
    });
  }

  async function refresh() {
    const catalog = await request("/api/runtime/skills/catalog");
    state.catalog = catalog;
    fillSelectors(catalog);
    renderSummary(catalog);
    renderFamilies(catalog);
    renderRuntime(catalog);
    renderPolicies(catalog);
  }

  function selectedTools() {
    return Array.from($("preferredTools").selectedOptions).map((option) => option.value);
  }

  function parseJsonField(id) {
    const raw = $(id).value.trim() || "{}";
    const value = JSON.parse(raw);
    if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(`${id} 必须是 JSON object`);
    return value;
  }

  function formPayload() {
    return {
      domain: $("domain").value,
      name: $("name").value.trim(),
      purpose: $("purpose").value.trim(),
      guidance: $("guidance").value.trim(),
      preferred_tools: selectedTools(),
      trigger_terms: $("triggerTerms").value.split(/[,，\n]/).map((x) => x.trim()).filter(Boolean),
      input_contract: parseJsonField("inputContract"),
      output_contract: parseJsonField("outputContract"),
      safety_notes: $("safetyNotes").value.trim(),
      source_skill_id: null,
    };
  }

  function resetDraft() {
    $("skillForm").reset();
    $("familyId").value = "";
    $("inputContract").value = "{}";
    $("outputContract").value = "{}";
    $("builderTitle").textContent = "新建技能族 · v1";
    $("draftHint").textContent = "新建后会生成不可变 v1。";
    if ($("domain").options.length) $("domain").selectedIndex = 0;
  }

  function loadAsNewVersion(item) {
    $("familyId").value = item.family_id;
    $("domain").value = item.domain;
    $("name").value = item.name;
    $("purpose").value = item.purpose;
    $("guidance").value = item.guidance;
    $("triggerTerms").value = (item.trigger_terms || []).join(", ");
    $("inputContract").value = JSON.stringify(item.input_contract || {}, null, 2);
    $("outputContract").value = JSON.stringify(item.output_contract || {}, null, 2);
    $("safetyNotes").value = item.safety_notes || "";
    Array.from($("preferredTools").options).forEach((option) => {
      option.selected = (item.preferred_tools || []).includes(option.value);
    });
    $("builderTitle").textContent = `${item.family_id} · 新建 v${Number(item.version) + 1}`;
    $("draftHint").textContent = "不会覆盖旧版本；保存后生成新的 content hash。";
    $("versionDialog").close();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function detailBlock(label, value, full = false, pre = false) {
    const block = node("div", `detail-block${full ? " full" : ""}`);
    block.append(node("small", "", label));
    const content = node(pre ? "pre" : "p", "", value === "" ? "—" : value);
    block.append(content);
    return block;
  }

  async function openVersion(versionId) {
    const item = await request(`/api/runtime/skills/studio/${encodeURIComponent(versionId)}`);
    state.selected = item;
    $("dialogTitle").textContent = `${item.name} · v${item.version}`;
    const detail = $("versionDetail");
    detail.replaceChildren();
    const grid = node("div", "detail-grid");
    grid.append(
      detailBlock("状态", statusLabel(item.state)),
      detailBlock("Content hash", item.content_hash),
      detailBlock("用途", item.purpose, true),
      detailBlock("Guidance", item.guidance, true),
      detailBlock("Trigger terms", (item.trigger_terms || []).join(", ")),
      detailBlock("Preferred tools", (item.preferred_tools || []).join(", ") || "—"),
      detailBlock("Input contract", JSON.stringify(item.input_contract || {}, null, 2), true, true),
      detailBlock("Output contract", JSON.stringify(item.output_contract || {}, null, 2), true, true),
      detailBlock("安全说明", item.safety_notes || "—", true),
    );
    detail.append(grid);
    if (item.evaluation) {
      detail.append(detailBlock("已关联评估快照", JSON.stringify(item.evaluation, null, 2), true, true));
    }
    const timeline = node("div", "timeline");
    (item.events || []).forEach((event) => {
      timeline.append(node("div", "timeline-row", `${fmtTime(event.created_at)} · ${event.event_type} · ${event.actor_id}`));
    });
    detail.append(timeline);
    $("submitReviewButton").disabled = item.state !== "draft";
    $("linkEvaluationButton").disabled = !["review", "evaluated_pass", "evaluated_fail"].includes(item.state);
    $("archiveButton").disabled = item.state === "archived";
    $("versionDialog").showModal();
  }

  $("skillForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const payload = formPayload();
      const familyId = $("familyId").value.trim();
      const url = familyId
        ? `/api/runtime/skills/studio/families/${encodeURIComponent(familyId)}/versions`
        : "/api/runtime/skills/studio/families";
      await request(url, { method: "POST", body: JSON.stringify(payload) });
      toast(familyId ? "新版本已保存；旧版本保持不变" : "技能族 v1 已创建");
      resetDraft();
      await refresh();
    } catch (error) { toast(error.message); }
  });

  $("resetDraft").addEventListener("click", resetDraft);
  $("newVersionButton").addEventListener("click", () => state.selected && loadAsNewVersion(state.selected));
  $("submitReviewButton").addEventListener("click", async () => {
    if (!state.selected) return;
    try {
      await request(`/api/runtime/skills/studio/${encodeURIComponent(state.selected.version_id)}/submit`, { method: "POST", body: JSON.stringify({ note: "" }) });
      toast("已提交评审；仍未进入 Runtime");
      await refresh();
      await openVersion(state.selected.version_id);
    } catch (error) { toast(error.message); }
  });
  $("linkEvaluationButton").addEventListener("click", async () => {
    if (!state.selected) return;
    const runId = $("evaluationRunId").value.trim();
    if (!runId) return toast("请输入 Evaluation run ID");
    try {
      await request(`/api/runtime/skills/studio/${encodeURIComponent(state.selected.version_id)}/evaluation-links`, { method: "POST", body: JSON.stringify({ run_id: runId }) });
      toast("评估快照已关联；不会自动上线");
      await refresh();
      await openVersion(state.selected.version_id);
    } catch (error) { toast(error.message); }
  });
  $("archiveButton").addEventListener("click", async () => {
    if (!state.selected) return;
    try {
      await request(`/api/runtime/skills/studio/${encodeURIComponent(state.selected.version_id)}/archive`, { method: "POST", body: JSON.stringify({ note: "" }) });
      toast("版本已归档");
      $("versionDialog").close();
      await refresh();
    } catch (error) { toast(error.message); }
  });

  refresh().catch((error) => toast(error.message));
})();