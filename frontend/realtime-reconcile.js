(() => {
  'use strict';

  const UpstreamWebSocket = window.WebSocket;
  const UpstreamFetch = window.fetch.bind(window);
  if (!UpstreamWebSocket) return;

  const seenMessages = new Set();
  const inflightTurns = new Map();
  const recentLocalSuccess = new Map();
  let refreshScheduled = false;

  function remember(messageId) {
    const id = String(messageId || '');
    if (!id || seenMessages.has(id)) return false;
    seenMessages.add(id);
    if (seenMessages.size > 120) seenMessages.delete(seenMessages.values().next().value);
    return true;
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
      state = { count: 0, acceptedWhilePending: 0 };
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
    let succeeded = false;
    try {
      response = await UpstreamFetch(...args);
      succeeded = Boolean(response?.ok);
      return response;
    } finally {
      state.count = Math.max(0, state.count - 1);
      const acceptedDuringThisRequest = state.acceptedWhilePending > 0;
      if (acceptedDuringThisRequest) state.acceptedWhilePending -= 1;

      if (succeeded) {
        if (!acceptedDuringThisRequest) recentLocalSuccess.set(conversationId, Date.now() + 10000);
      } else if (acceptedDuringThisRequest && conversationId === currentConversationId()) {
        // Another tab won the lease while this tab's POST was still pending.
        scheduleCurrentTaskRefresh();
      }

      if (state.count === 0 && state.acceptedWhilePending === 0) inflightTurns.delete(conversationId);
    }
  };

  function inspectEvent(raw, conversationId) {
    let event;
    try { event = JSON.parse(raw); } catch (_) { return; }
    if (!event || event.type !== 'message.accepted') return;
    if (!conversationId || conversationId !== currentConversationId()) return;

    const messageId = event.payload?.message?.id || event.payload?.message_id;
    if (!remember(messageId)) return;

    const state = inflightTurns.get(conversationId);
    if (state?.count > 0) {
      // Do not guess by message text. Wait for the local POST result: success means this
      // accepted event is ours; 409/network failure means another tab won and we refresh.
      state.acceptedWhilePending += 1;
      return;
    }

    const localUntil = Number(recentLocalSuccess.get(conversationId) || 0);
    if (localUntil > Date.now()) {
      recentLocalSuccess.delete(conversationId);
      return;
    }
    recentLocalSuccess.delete(conversationId);
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