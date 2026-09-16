from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    # A smoke test must never consume or mutate the operator's normal runtime data.
    # Import the application only after binding a fresh durable root.
    with tempfile.TemporaryDirectory(prefix="ecomevo-smoke-") as tmp:
        os.environ["ECOMEVO_DATA"] = tmp
        os.environ.setdefault("ECOMEVO_AUTH_MODE", "local")
        os.environ.setdefault("ECOMEVO_LOCAL_ROLE", "admin")

        from fastapi.testclient import TestClient
        from ecomevo.api.app import app

        with TestClient(app) as client:
            conv = client.post(
                "/api/conversations",
                json={"title": "售后 E2E", "scene": "aftersales"},
            ).json()

            inbox = client.get("/api/inbox?view=all")
            assert inbox.status_code == 200
            inbox_payload = inbox.json()
            assert any(row["id"] == conv["id"] for row in inbox_payload["items"])
            assert inbox_payload["authority"] == {
                "assignment_grants_approval": False,
                "priority_changes_runtime_routing": False,
            }
            assert client.get("/api/inbox/ui").status_code == 200
            assert client.get("/assets/inbox.js").status_code == 200
            assert client.get("/assets/inbox.css").status_code == 200

            claimed = client.post(f"/api/inbox/{conv['id']}/claim")
            assert claimed.status_code == 200
            assert claimed.json()["owner_user_id"]
            priority = client.patch(
                f"/api/inbox/{conv['id']}/priority",
                json={"priority": "urgent"},
            )
            assert priority.status_code == 200
            assert priority.json()["queue_priority"] == "urgent"
            released = client.delete(f"/api/inbox/{conv['id']}/claim")
            assert released.status_code == 200
            assert released.json()["owner_user_id"] is None

            raw = (
                "订单 order-88421\n金额: 299\n物流显示签收，用户反馈未收到货\n"
                "客服记录：申请退款"
            ).encode("utf-8")
            asset = client.post(
                "/api/assets",
                files={"file": ("order.log", raw, "text/plain")},
                data={"conversation_id": conv["id"]},
            ).json()
            response = client.post(
                f"/api/conversations/{conv['id']}/messages",
                json={
                    "content": "请结合订单和履约记录给出售后判责建议",
                    "asset_ids": [asset["id"]],
                    "provider": "demo",
                },
            )
            assert response.status_code == 200
            detail = client.get(f"/api/conversations/{conv['id']}").json()
            assistant = [row for row in detail["messages"] if row["role"] == "assistant"][-1]
            assert assistant["payload"]["domain"] == "aftersales"
            assert assistant["payload"]["runtime"]["event_chain_valid"] is True
            assert detail["actions"]
            action = detail["actions"][0]

            queue_item = client.get(f"/api/inbox/{conv['id']}").json()
            if action["requires_confirmation"]:
                assert queue_item["queue_state"] == "waiting_approval"
                completed = client.post(
                    f"/api/actions/{action['id']}/decision",
                    json={"decision": "approve", "note": "E2E"},
                ).json()
                assert completed["status"] == "simulated"
                assert completed["payload"]["execution_outcome"] == "simulated"
            print({
                "conversation_id": conv["id"],
                "domain": assistant["payload"]["domain"],
                "session_id": assistant["payload"]["session_id"],
                "actions": len(detail["actions"]),
                "queue_state": queue_item["queue_state"],
                "event_chain_valid": True,
            })


if __name__ == "__main__":
    main()
