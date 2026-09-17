(() => {
  const HEARTBEAT_MS = 15_000;
  const ACTIVE_LEASE_MS = 30_000;
  const MIN_SEND_GAP_MS = 5_000;
  let lastInteractionAt = 0;
  let lastSentAt = 0;
  let inFlight = false;

  const foreground = () => document.visibilityState === "visible" && document.hasFocus();
  const recentlyActive = (now) => lastInteractionAt > 0 && now - lastInteractionAt <= ACTIVE_LEASE_MS;

  const heartbeat = async () => {
    const now = Date.now();
    if (!foreground() || !recentlyActive(now) || inFlight || now - lastSentAt < MIN_SEND_GAP_MS) return;
    lastSentAt = now;
    inFlight = true;
    try {
      await fetch("/api/operator-activity/heartbeat", {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({surface: "workbench"}),
        credentials: "same-origin",
        keepalive: true,
      });
    } catch (_) {
      // Telemetry is strictly non-blocking. Workbench behavior must not depend on it.
    } finally {
      inFlight = false;
    }
  };

  const markInteraction = () => {
    lastInteractionAt = Date.now();
    void heartbeat();
  };

  for (const eventName of ["pointerdown", "keydown", "touchstart", "input"]) {
    window.addEventListener(eventName, markInteraction, {capture: true, passive: true});
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void heartbeat();
  });
  window.addEventListener("focus", () => void heartbeat());

  window.setInterval(() => void heartbeat(), HEARTBEAT_MS);
})();
