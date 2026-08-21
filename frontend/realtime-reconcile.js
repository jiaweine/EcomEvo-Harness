(() => {
  'use strict';

  const UpstreamWebSocket = window.WebSocket;
  const UpstreamFetch = window.fetch.bind(window);
  if (!UpstreamWebSocket) return;

  const seenMessages = new Set();
  const localAcceptedIds = new Set();
  const inflightTurns = new Map();
  let refreshScheduled = false;

  function rememberBounded(set, value, limit = 120) {
    const id = String(value || '');
    if (!id) return false;
    const fresh = !set.has(id);
    if (fresh) set.add(id);
    while (set.size > limit) set.delete(set.values().next().value);
    return fresh;
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

  function requestUrl(input) {
    return typeof input === 'string' ? input : (input && input.url) || '';
  }

  function requestMethod(options) {
    return String(options?.method || 'GET').toUpperCase();
  }

  function messagePostConversationId(input, options) {
    if (requestMethod(options) !== 'POST') return '';
    try {
      const parsed = new URL(requestUrl(input), location.href);
      const match = parsed.pathname.match(/\/api\/conversations\/([^/]+)\/messages$/);
      return match ? decodeURIComponent(match[1]) : '';
    } catch (_) {
      return '';
    }
  }

  function turnState(conversationId) {
    let state = inflightTurns.get(conversationId);
    if (!state) {
      state = { count: 0, acceptedIds: [] };
      inflightTurns.set(conversationId, state);
    }
    return state;
  }

  function scheduleCurrentTaskRefresh() {
    if (refreshScheduled) return;
    refreshScheduled = true;
    setTimeout(() => {
      refreshScheduled = false;
      const active = document.querySelector('#conversationList .conv-item.active');
      if (!active || !currentConversationId()) return;
      active.click();
    }, 0);
  }

  window.fetch = async (...args) => {
    const conversationId = messagePostConversationId(args[0], args[1]);
    if (!conversationId) return UpstreamFetch(...args);

    const state = turnState(conversationId);
    state.count += 1;
    let response;
    let localMessageId = '';
    try {
      response = await UpstreamFetch(...args);
      if (response?.ok) {
        try {
          const payload = await response.clone().json();
          localMessageId = String(payload?.message?.id || '');
          if (localMessageId) rememberBounded(localAcceptedIds, localMessageId);
        } catch (_) {}
      }
      return response;
    } finally {
      state.count = Math.max(0, state.count - 1);
      const acceptedIds = state.acceptedIds.splice(0);
      if (acceptedIds.length && conversationId === currentConversationId()) {
        const hasRemoteAccepted = acceptedIds.some(id => !localMessageId || id !== localMessageId);
        if (hasRemoteAccepted) scheduleCurrentTaskRefresh();
      }
      if (state.count === 0 && state.acceptedIds.length === 0) inflightTurns.delete(conversationId);
    }
  };

  function inspectEvent(raw, conversationId) {
    let event;
    try { event = JSON.parse(raw); } catch (_) { return; }
    if (!event || event.type !== 'message.accepted') return;
    if (!conversationId || conversationId !== currentConversationId()) return;

    const messageId = String(event.payload?.message?.id || event.payload?.message_id || '');
    if (!messageId || !rememberBounded(seenMessages, messageId)) return;

    if (localAcceptedIds.has(messageId)) {
      localAcceptedIds.delete(messageId);
      return;
    }

    const state = inflightTurns.get(conversationId);
    if (state?.count > 0) {
      state.acceptedIds.push(messageId);
      return;
    }

    scheduleCurrentTaskRefresh();
  }

  function ReconciledWebSocket(...args) {
    const conversationId = socketConversationId(args[0]);
    const socket = new UpstreamWebSocket(...args);
    if (conversationId) socket.addEventListener('message', event => inspectEvent(event.data, conversationId));
    return socket;
  }

  ReconciledWebSocket.prototype = UpstreamWebSocket.prototype;
  Object.setPrototypeOf(ReconciledWebSocket, UpstreamWebSocket);
  window.WebSocket = ReconciledWebSocket;
})();