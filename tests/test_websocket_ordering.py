import asyncio
import importlib

from fastapi.testclient import TestClient


appmod = importlib.import_module("ecomevo.api.app")


def test_websocket_queue_wakeup_drains_durable_events_in_id_order():
    client = TestClient(appmod.app)
    conv = client.post("/api/conversations", json={"scene": "merchant_review"}).json()
    cid = conv["id"]

    with client.websocket_connect(f"/ws/conversations/{cid}") as ws:
        # Simulate another worker writing an earlier durable event without touching this
        # process's in-memory queue, then let this process emit a later event.
        remote = appmod.store.add_event(cid, "progress", {"step": "remote-worker"})
        local = asyncio.run(appmod.emit(cid, "notice", {"title": "local-worker"}))

        first = ws.receive_json()
        second = ws.receive_json()

    assert first["id"] == remote["id"]
    assert first["type"] == "progress"
    assert second["id"] == local["id"]
    assert second["type"] == "notice"
    assert first["id"] < second["id"]
