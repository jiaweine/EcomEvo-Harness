from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from ecomevo.api.app import app


def _identity(monkeypatch, *, tenant="studio-tenant", user="studio-admin", role="admin"):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", tenant)
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", user)
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", role)


def _payload(name: str, *, domain: str = "aftersales"):
    return {
        "domain": domain,
        "name": name,
        "purpose": "判责前检查订单、履约和用户证据是否齐备",
        "guidance": "先核对订单与履约事实，再检查用户证据；存在关键缺口时停止并请求补证，不得直接执行退款。",
        "preferred_tools": ["order.inspect", "evidence.search"],
        "trigger_terms": ["退款", "未收到货"],
        "input_contract": {"requires": ["order_context"]},
        "output_contract": {"fields": ["evidence_gaps"]},
        "safety_notes": "不授予 action authority。",
    }


def test_skill_studio_is_admin_only_and_has_no_runtime_promotion(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch, role="admin")
        catalog = client.get("/api/runtime/skills/catalog")
        assert catalog.status_code == 200
        data = catalog.json()
        assert data["scope"] == "deployment"
        assert data["authority"]["can_promote_runtime"] is False
        assert data["authority"]["candidate_evaluation_mutates_production"] is False
        assert data["authority"]["evaluation_pass_auto_promotes"] is False
        assert "order.inspect" in data["registered_tools"]
        assert client.get("/api/runtime/skills/ui").status_code == 200

        name = f"studio-{uuid.uuid4().hex[:8]}"
        created = client.post("/api/runtime/skills/studio/families", json=_payload(name))
        assert created.status_code == 201
        item = created.json()
        assert item["state"] == "draft"
        assert item["version"] == 1
        assert item["authority"]["studio_changes_runtime_skill"] is False

        submitted = client.post(
            f"/api/runtime/skills/studio/{item['version_id']}/submit",
            json={"note": "ready for isolated candidate evaluation"},
        )
        assert submitted.status_code == 200
        assert submitted.json()["state"] == "review"

        version_two = client.post(
            f"/api/runtime/skills/studio/families/{item['family_id']}/versions",
            json={**_payload(name), "guidance": "依次核对订单、履约、物流和用户证据；证据链不完整时停止，不得执行退款或修改订单。"},
        )
        assert version_two.status_code == 201
        assert version_two.json()["version"] == 2
        assert version_two.json()["version_id"] != item["version_id"]

        _identity(monkeypatch, role="operator", user="studio-operator")
        assert client.get("/api/runtime/skills/catalog").status_code == 403
        assert client.get("/api/runtime/skills/ui").status_code == 403
        assert client.post("/api/runtime/skills/studio/families", json=_payload("blocked")).status_code == 403

        _identity(monkeypatch, role="viewer", user="studio-viewer")
        assert client.get("/api/runtime/skills/studio").status_code == 403


def test_skill_studio_rejects_unknown_tools(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch)
        payload = _payload("unknown-tool")
        payload["preferred_tools"] = ["orders.force_refund"]
        response = client.post("/api/runtime/skills/studio/families", json=payload)
        assert response.status_code == 422
        assert "unknown preferred tools" in response.json()["detail"]


def test_skill_studio_candidate_evaluation_requires_gold_set_coverage(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch)
        payload = _payload(f"general-{uuid.uuid4().hex[:8]}", domain="general")
        payload["preferred_tools"] = ["evidence.search"]
        created = client.post("/api/runtime/skills/studio/families", json=payload).json()
        submitted = client.post(
            f"/api/runtime/skills/studio/{created['version_id']}/submit",
            json={"note": ""},
        )
        assert submitted.status_code == 200
        response = client.post(
            f"/api/runtime/skills/studio/{created['version_id']}/evaluate",
            json={},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "no Gold Set cases cover this skill domain"
        assert client.get("/api/runtime/skills/studio/evaluations/studio-eval-missing").status_code == 404
