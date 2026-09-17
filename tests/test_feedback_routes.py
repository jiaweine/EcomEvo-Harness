from __future__ import annotations

from fastapi.testclient import TestClient

from ecomevo.api.app import app, store


def _identity(monkeypatch, *, tenant="tenant-feedback-a", user="operator-a", role="operator"):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", tenant)
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", user)
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", role)


def _assistant_payload():
    return {
        "evidence": [
            {
                "evidence_id": "ev-api-1",
                "source": "order.inspect",
                "title": "订单状态",
                "detail": "订单 API-88421 当前状态为已签收",
            }
        ],
        "grounding": {
            "schema_version": 3,
            "evidence_sufficiency": "sufficient",
            "claims": [
                {
                    "text": "订单 API-88421 已签收。",
                    "kind": "fact",
                    "verdict": "supported",
                    "evidence_ids": ["ev-api-1"],
                }
            ],
        },
    }


def test_feedback_routes_enforce_operator_submit_admin_review_and_tenant_isolation(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch, role="operator")
        conv = client.post(
            "/api/conversations",
            json={"title": "反馈 API", "scene": "aftersales"},
        ).json()
        store.add_message(conv["id"], "user", "这笔订单是什么状态？", {})
        assistant = store.add_message(
            conv["id"],
            "assistant",
            "订单 API-88421 已签收。",
            _assistant_payload(),
        )

        caps = client.get("/api/feedback/capabilities")
        assert caps.status_code == 200
        assert caps.json()["can_submit"] is True
        assert caps.json()["authority"]["feedback_changes_production_authority"] is False

        targets = client.get(
            f"/api/conversations/{conv['id']}/feedback/targets",
            params={"message_id": assistant["id"]},
        )
        assert targets.status_code == 200
        claim_ref = targets.json()["claims"][0]["ref"]

        created = client.post(
            f"/api/conversations/{conv['id']}/feedback",
            json={
                "assistant_message_id": assistant["id"],
                "category": "factual_error",
                "impact": "decision_relevant",
                "target_type": "claim",
                "target_ref": claim_ref,
                "explanation": "签收状态需要核对承运商原始回传。",
                "proposed_correction": "先标记为待核对。",
            },
        )
        assert created.status_code == 200
        feedback_id = created.json()["id"]
        assert created.json()["status"] == "open"

        _identity(monkeypatch, role="viewer", user="viewer-a")
        assert client.get(f"/api/conversations/{conv['id']}/feedback").status_code == 200
        assert client.post(
            f"/api/conversations/{conv['id']}/feedback",
            json={
                "assistant_message_id": assistant["id"],
                "category": "other",
                "impact": "answer_only",
                "target_type": "answer",
                "explanation": "viewer 不应能提交",
            },
        ).status_code == 403
        assert client.get("/api/runtime/feedback").status_code == 403

        _identity(monkeypatch, tenant="tenant-feedback-b", role="admin", user="admin-b")
        assert client.get(f"/api/conversations/{conv['id']}/feedback").status_code == 404
        assert client.get(f"/api/runtime/feedback/{feedback_id}").status_code == 404

        _identity(monkeypatch, role="admin", user="admin-a")
        admin_list = client.get("/api/runtime/feedback")
        assert admin_list.status_code == 200
        assert feedback_id in {row["id"] for row in admin_list.json()["items"]}
        review = client.post(
            f"/api/runtime/feedback/{feedback_id}/review",
            json={"decision": "accepted_for_eval", "note": "进入离线评估候选。"},
        )
        assert review.status_code == 200
        assert review.json()["status"] == "accepted_for_eval"
        sample = client.get(f"/api/runtime/feedback/{feedback_id}/evaluation-sample")
        assert sample.status_code == 200
        assert sample.json()["authority"]["auto_promotes_to_gold_set"] is False
        assert client.get("/api/runtime/feedback/ui").status_code == 200
