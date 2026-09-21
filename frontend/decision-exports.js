(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const list = byId("export-list");
  const form = byId("create-form");
  const conversationInput = byId("conversation-id");
  const createStatus = byId("create-status");
  const emptyDetail = byId("empty-detail");
  const detail = byId("snapshot-detail");
  const verifyButton = byId("verify");
  const verifyResult = byId("verify-result");
  const download = byId("download");
  let selectedId = "";

  async function request(path, options = {}) {
    const response = await fetch(path, {
      headers: {"Content-Type": "application/json", ...(options.headers || {})},
      ...options,
    });
    let body = {};
    try {
      body = await response.json();
    } catch (_error) {
      body = {};
    }
    if (!response.ok) {
      throw new Error(body.detail || `请求失败（${response.status}）`);
    }
    return body;
  }

  function setStatus(node, message, kind = "") {
    node.textContent = message;
    node.className = node === createStatus ? "status" : "verify-result";
    if (kind) node.classList.add(kind);
  }

  function formatTime(value) {
    const timestamp = Number(value || 0) * 1000;
    if (!Number.isFinite(timestamp) || timestamp <= 0) return "—";
    return new Date(timestamp).toLocaleString();
  }

  function shortHash(value) {
    const text = String(value || "");
    return text.length > 18 ? `${text.slice(0, 10)}…${text.slice(-8)}` : text;
  }

  function renderList(items) {
    list.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "当前工作区还没有导出快照。";
      list.append(empty);
      return;
    }
    for (const item of items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "export-row";
      button.dataset.exportId = item.id;
      if (item.id === selectedId) button.classList.add("active");

      const title = document.createElement("strong");
      title.textContent = item.conversation_id || "未知任务";
      const id = document.createElement("span");
      id.textContent = item.id;
      const meta = document.createElement("span");
      meta.textContent = `${formatTime(item.created_at)} · ${shortHash(item.content_hash)}`;
      button.append(title, id, meta);
      button.addEventListener("click", () => showSnapshot(item.id));
      list.append(button);
    }
  }

  async function loadList() {
    try {
      const body = await request("/api/runtime/decision-exports");
      renderList(body.items || []);
    } catch (error) {
      list.replaceChildren();
      const message = document.createElement("p");
      message.className = "empty";
      message.textContent = error.message;
      list.append(message);
    }
  }

  async function showSnapshot(id) {
    selectedId = id;
    verifyResult.textContent = "";
    try {
      const snapshot = await request(`/api/runtime/decision-exports/${encodeURIComponent(id)}`);
      byId("meta-id").textContent = snapshot.id || "—";
      byId("meta-conversation").textContent = snapshot.conversation_id || "—";
      byId("meta-creator").textContent = snapshot.created_by || "—";
      byId("meta-created").textContent = formatTime(snapshot.created_at);
      byId("meta-hash").textContent = snapshot.content_hash || "—";
      byId("payload-json").textContent = JSON.stringify(snapshot.payload || {}, null, 2);
      emptyDetail.hidden = true;
      detail.hidden = false;
      verifyButton.disabled = false;
      download.classList.remove("disabled");
      download.removeAttribute("aria-disabled");
      download.href = `/api/runtime/decision-exports/${encodeURIComponent(id)}/download`;
      renderActiveRow();
    } catch (error) {
      setStatus(verifyResult, error.message, "error");
    }
  }

  function renderActiveRow() {
    for (const row of list.querySelectorAll(".export-row")) {
      row.classList.toggle("active", row.dataset.exportId === selectedId);
    }
  }

  async function createSnapshot(event) {
    event.preventDefault();
    const conversationId = conversationInput.value.trim();
    if (!conversationId) return;
    setStatus(createStatus, "正在固定当前审计材料…");
    try {
      const snapshot = await request("/api/runtime/decision-exports", {
        method: "POST",
        body: JSON.stringify({conversation_id: conversationId}),
      });
      setStatus(createStatus, `已创建不可变快照：${snapshot.id}`, "success");
      await loadList();
      await showSnapshot(snapshot.id);
    } catch (error) {
      setStatus(createStatus, error.message, "error");
    }
  }

  async function verifySelected() {
    if (!selectedId) return;
    verifyButton.disabled = true;
    setStatus(verifyResult, "正在重新计算内容哈希…");
    try {
      const result = await request(
        `/api/runtime/decision-exports/${encodeURIComponent(selectedId)}/verify`
      );
      if (result.valid) {
        setStatus(verifyResult, `校验通过 · SHA-256 ${result.observed_hash}`, "success");
      } else {
        setStatus(verifyResult, "校验失败：存储内容与创建时哈希不一致。", "error");
      }
    } catch (error) {
      setStatus(verifyResult, error.message, "error");
    } finally {
      verifyButton.disabled = false;
    }
  }

  form.addEventListener("submit", createSnapshot);
  byId("refresh").addEventListener("click", loadList);
  verifyButton.addEventListener("click", verifySelected);
  loadList();
})();
