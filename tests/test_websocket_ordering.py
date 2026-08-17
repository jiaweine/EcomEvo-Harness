import importlib

from fastapi.testclient import TestClient


appmod = importlib.import_module("ecomevo.api.app")


def test_websocket_queue_wakeup_drains_durable_events_in_id_order():
    with TestClient(appmod.app) as client:
        conv = client.post("/api/conversations", json={"scene": "merchant_review"}).json()
        cid = conv["id"]

        with client.websocket_connect(f"/ws/conversations/{cid}") as ws:
            # Simulate another worker writing an earlier durable event without touching this
            # process's in-memory queue. A real HTTP request then makes this worker emit a
            # later message.accepted event on its own event loop.
            remote = appmod.store.add_event(cid, "progress", {"step": "remote-worker"})
            sent = client.post(
                f"/api/conversations/{cid}/messages",
                json={"content": "检查当前资料缺口", "asset_ids": [], "provider": "demo"},
            )
            assert sent.status_code == 200

            first = ws.receive_json()
            second = ws.receive_json()

    assert first["id"] == remote["id"]
    assert first["type"] == "progress"
    assert second["type"] == "message.accepted"
    assert second["payload"]["message"]["id"] == sent.json()["message"]["id"]
    assert first["id"] < second["id"]
