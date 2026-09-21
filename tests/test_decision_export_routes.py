from __future__ import annotations

from fastapi.testclient import TestClient

from ecomevo.api.app import app, store


def _identity(monkeypatch, *, tenant="tenant-export-route-a", user="admin-a", role="admin"):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", tenant)
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", user)
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", role)


def test_decision_export_routes_are_admin_only_and_tenant_scoped(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch)
        conv = client.post(
            "/api/conversations",
            json={"title": "审计导出 API", "scene": "risk_review"},
        ).json()
        store.add_message(conv["id"], "user", "请核对当前风险材料。", {})
        store.add_message(
            conv["id"],
            "assistant",
            "当前结论仍需要人工复核。",
            {"domain": "risk_review", "secret": "must-redact"},
        )
        store.watch_conversation(
            conv["id"],
            "reviewer-route-a",
            tenant_id="tenant-export-route-a",
        )
        store.add_collaboration_comment(
            conv["id"],
            "admin-a",
            "@reviewer-route-a 请复核导出材料。",
            tenant_id="tenant-export-route-a",
        )
        before = client.get(f"/api/conversations/{conv['id']}").json()
        action_before = [
            (row["id"], row["status"], row["payload"])
            for row in before["actions"]
        ]

        created = client.post(
            "/api/runtime/decision-exports",
            json={"conversation_id": conv["id"]},
        )
        assert created.status_code == 200
        snapshot = created.json()
        export_id = snapshot["id"]
        assert len(snapshot["content_hash"]) == 64
        assert snapshot["payload"]["messages"][-1]["payload"]["secret"] == "[redacted]"
        assert all(value is False for value in snapshot["authority"].values())
        assert snapshot["payload"]["collaboration"]["watchers"][0]["user_id"] == "reviewer-route-a"
        assert snapshot["payload"]["collaboration"]["events"][1]["mentions"] == ["reviewer-route-a"]

        listed = client.get("/api/runtime/decision-exports")
        assert listed.status_code == 200
        assert export_id in {row["id"] for row in listed.json()["items"]}

        detail = client.get(f"/api/runtime/decision-exports/{export_id}")
        assert detail.status_code == 200
        assert detail.json()["conversation_id"] == conv["id"]

        verified = client.get(f"/api/runtime/decision-exports/{export_id}/verify")
        assert verified.status_code == 200
        assert verified.json()["valid"] is True

        downloaded = client.get(f"/api/runtime/decision-exports/{export_id}/download")
        assert downloaded.status_code == 200
        assert "attachment;" in downloaded.headers["content-disposition"]
        assert downloaded.json()["content_hash"] == snapshot["content_hash"]
        assert client.get("/api/runtime/decision-exports/ui").status_code == 200
        assert client.get("/assets/decision-exports.js").status_code == 200
        assert client.get("/assets/decision-exports.css").status_code == 200

        after = client.get(f"/api/conversations/{conv['id']}").json()
        assert [
            (row["id"], row["status"], row["payload"])
            for row in after["actions"]
        ] == action_before

        _identity(monkeypatch, tenant="tenant-export-route-b", user="admin-b")
        assert client.get(f"/api/runtime/decision-exports/{export_id}").status_code == 404
        assert client.post(
            "/api/runtime/decision-exports",
            json={"conversation_id": conv["id"]},
        ).status_code == 404

        _identity(monkeypatch, role="operator", user="operator-a")
        assert client.get("/api/runtime/decision-exports").status_code == 403
        assert client.post(
            "/api/runtime/decision-exports",
            json={"conversation_id": conv["id"]},
        ).status_code == 403

        _identity(monkeypatch, role="viewer", user="viewer-a")
        assert client.get("/api/runtime/decision-exports/ui").status_code == 403
