from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ecomevo.api.policy_api import build_policy_router
from ecomevo.api.policy_workflow_routes import install_policy_workflow_routes
from ecomevo.identity import IdentityMiddleware
from ecomevo.runtime import EcomEvoEngine


ROOT = Path(__file__).resolve().parents[1]


def _identity(monkeypatch, *, tenant="A", user="maker", role="admin"):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", tenant)
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", user)
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", role)


def _payload(key="refund-guard"):
    return {
        "policy_key": key,
        "domain": "aftersales",
        "rules": ["证据不足时必须进入人工复核"],
        "controls": {"tenant.refund.action": "review"},
        "scope": {"channel": "marketplace"},
        "authority": 60,
        "priority": 5,
        "source": "internal-policy:test",
    }


def _app(tmp_path):
    engine = EcomEvoEngine(tmp_path / "runtime.db")
    app = FastAPI()
    app.include_router(build_policy_router(engine))
    install_policy_workflow_routes(app, engine=engine, frontend=ROOT / "frontend")
    app.add_middleware(IdentityMiddleware)
    return app


def test_policy_workflow_api_enforces_maker_checker_and_tenant_scope(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _identity(monkeypatch, tenant="A", user="maker", role="admin")
    with TestClient(app) as client:
        assert client.get("/api/runtime/policies/ui").status_code == 200
        created = client.post("/api/runtime/policies/drafts", json=_payload())
        assert created.status_code == 201
        item = created.json()
        assert item["scope"]["tenant"] == "A"
        assert item["workflow_state"] == "draft"
        assert item["policy_id"].startswith("tenant.")
        assert item["authority"]["publish_without_approval"] is False

        before = client.get("/api/runtime/policies/resolve", params={
            "domain": "aftersales",
            "scope": '{"channel":"marketplace"}',
            "as_of": "2030-01-01T00:00:00Z",
        })
        assert before.status_code == 200
        assert "tenant.refund.action" not in before.json()["controls"]

        self_approve = client.post(
            f"/api/runtime/policies/{item['policy_id']}/versions/{item['version']}/approve",
            json={"effective_from": "2030-01-01T00:00:00Z", "note": "self"},
        )
        assert self_approve.status_code == 403

        _identity(monkeypatch, tenant="A", user="checker", role="admin")
        preview = client.get(
            f"/api/runtime/policies/{item['policy_id']}/versions/{item['version']}/preview-publish",
            params={"effective_from": "2030-01-01T00:00:00Z"},
        )
        assert preview.status_code == 200
        assert preview.json()["production_mutated"] is False
        assert preview.json()["safe_to_apply"] is True

        approved = client.post(
            f"/api/runtime/policies/{item['policy_id']}/versions/{item['version']}/approve",
            json={"effective_from": "2030-01-01T00:00:00Z", "note": "four eyes"},
        )
        assert approved.status_code == 200
        assert approved.json()["workflow_state"] == "approved"

        published = client.post(
            f"/api/runtime/policies/{item['policy_id']}/versions/{item['version']}/publish"
        )
        assert published.status_code == 200
        assert published.json()["status"] == "active"
        assert published.json()["approver"] == "checker"
        assert published.json()["events"][-1]["event_type"] == "published"

        after = client.get("/api/runtime/policies/resolve", params={
            "domain": "aftersales",
            "scope": '{"channel":"marketplace"}',
            "as_of": "2030-01-01T00:00:01Z",
        })
        assert after.status_code == 200
        assert after.json()["controls"]["tenant.refund.action"] == "review"

        _identity(monkeypatch, tenant="B", user="other-admin", role="admin")
        hidden = client.get(
            f"/api/runtime/policies/{item['policy_id']}/versions/{item['version']}/workflow"
        )
        assert hidden.status_code == 403
        catalog = client.get("/api/runtime/policies/workflow")
        assert catalog.status_code == 200
        assert all(row["policy_id"] != item["policy_id"] for row in catalog.json()["items"])

        _identity(monkeypatch, tenant="A", user="viewer", role="viewer")
        assert client.get("/api/runtime/policies/workflow").status_code == 403
        assert client.post("/api/runtime/policies/drafts", json=_payload("blocked")).status_code == 403


def test_policy_workflow_api_rejects_scope_spoof_and_builtin_write(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _identity(monkeypatch, tenant="A", user="admin", role="admin")
    with TestClient(app) as client:
        spoof = _payload("spoof")
        spoof["scope"] = {"tenant": "B"}
        response = client.post("/api/runtime/policies/drafts", json=spoof)
        assert response.status_code == 403

        builtin = client.get("/api/runtime/policies/builtin.aftersales/versions/1/preview-publish")
        assert builtin.status_code == 403


def test_policy_retirement_requires_second_admin(monkeypatch, tmp_path):
    app = _app(tmp_path)
    _identity(monkeypatch, tenant="A", user="maker", role="admin")
    with TestClient(app) as client:
        item = client.post("/api/runtime/policies/drafts", json=_payload("retire-flow")).json()

        _identity(monkeypatch, tenant="A", user="checker", role="admin")
        assert client.post(
            f"/api/runtime/policies/{item['policy_id']}/versions/{item['version']}/approve",
            json={"effective_from": "2030-01-01T00:00:00Z", "note": "approve"},
        ).status_code == 200
        assert client.post(
            f"/api/runtime/policies/{item['policy_id']}/versions/{item['version']}/publish"
        ).status_code == 200

        _identity(monkeypatch, tenant="A", user="maker", role="admin")
        request = client.post(
            f"/api/runtime/policies/{item['policy_id']}/versions/{item['version']}/retirement-requests",
            json={"effective_to": "2030-01-02T00:00:00Z", "note": "replace"},
        )
        assert request.status_code == 200
        assert request.json()["preview"]["safe_to_apply"] is True
        self_retire = client.post(
            f"/api/runtime/policies/{item['policy_id']}/versions/{item['version']}/retire"
        )
        assert self_retire.status_code == 403

        _identity(monkeypatch, tenant="A", user="checker", role="admin")
        retired = client.post(
            f"/api/runtime/policies/{item['policy_id']}/versions/{item['version']}/retire"
        )
        assert retired.status_code == 200
        assert retired.json()["status"] == "retired"
        assert retired.json()["events"][-1]["event_type"] == "retired"
