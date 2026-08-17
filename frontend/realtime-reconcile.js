(() => {
  'use strict';

  const UpstreamWebSocket = window.WebSocket;
  if (!UpstreamWebSocket) return;

  const seenMessages = new Set();
  let refreshScheduled = false;

  function remember(messageId) {
    const id = String(messageId || '');
    if (!id || seenMessages.has(id)) return false;
    seenMessages.add(id);
    if (seenMessages.size > 120) seenMessages.delete(seenMessages.values().next().value);
    return true;
  }

  function normalize(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
  }

  function currentConversationId() {
    return new URLSearchParams(location.search).get('conversation') || '';
  }

  function socketConversationId(url) {
    try {
      const parsed = new URL(String(url), location.href);
      const match = parsed.pathname.match(/\/ws\/conversations\/([^/]+)$/);
      return match ? decodeURIComponent(match[1]) : '';
    } catch (_) {
      return '';
    }
  }

  function lastRenderedUserText() {
    const rows = document.querySelectorAll('#messageList .msg.user .msg-content');
    return normalize(rows.length ? rows[rows.length - 1].textContent : '');
  }

  function scheduleCurrentTaskRefresh(message) {
    const messageId = message?.id;
    const content = normalize(message?.content);
    if (!messageId || !content || !remember(messageId)) return;

    // The sending tab already inserts its optimistic user message before POSTing.
    // Only refresh when this tab is visibly missing the accepted turn.
    if (lastRenderedUserText() === content) return;
    if (refreshScheduled) return;
    refreshScheduled = true;

    setTimeout(() => {
      refreshScheduled = false;
      const active = document.querySelector('#conversationList .conv-item.active');
      if (!active || !currentConversationId()) return;
      active.click();
    }, 0);
  }

  function inspectEvent(raw, conversationId) {
    let event;
    try { event = JSON.parse(raw); } catch (_) { return; }
    if (!event || event.type !== 'message.accepted') return;
    if (!conversationId || conversationId !== currentConversationId()) return;
    scheduleCurrentTaskRefresh(event.payload?.message);
  }

  function ReconciledWebSocket(...args) {
    const conversationId = socketConversationId(args[0]);
    const socket = new UpstreamWebSocket(...args);
    if (conversationId) {
      socket.addEventListener('message', event => inspectEvent(event.data, conversationId));
    }
    return socket;
  }

  ReconciledWebSocket.prototype = UpstreamWebSocket.prototype;
  Object.setPrototypeOf(ReconciledWebSocket, UpstreamWebSocket);
  window.WebSocket = ReconciledWebSocket;
})();