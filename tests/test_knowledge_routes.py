from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ecomevo.api.knowledge_routes import install_knowledge_routes
from ecomevo.identity import IdentityMiddleware


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def _app(tmp_path):
    app = FastAPI()
    install_knowledge_routes(
        app,
        db_path=tmp_path / "knowledge-routes.db",
        frontend=FRONTEND,
    )
    app.add_middleware(IdentityMiddleware)
    return app


def _identity(monkeypatch, *, tenant, user, role):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", tenant)
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", user)
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", role)


def _payload():
    return {
        "name": "物流签收争议 SOP",
        "source_tier": "S2",
        "domain": "aftersales",
        "description": "受控售后治理知识",
        "owner": "售后治理",
        "jurisdiction": "CN",
        "tags": ["物流", "签收"],
        "version": {
            "title": "签收争议 v1",
            "content_text": "物流显示签收但用户否认收货时，应核对承运商原始轨迹、签收凭证和用户举证后再进行责任判断。",
            "effective_from": None,
            "effective_until": None,
            "review_due_at": None,
            "provenance": "内部售后 SOP",
        },
    }


def test_admin_lifecycle_search_and_projection(monkeypatch, tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _identity(monkeypatch, tenant="tenant-a", user="admin-a", role="admin")
        created = client.post("/api/runtime/knowledge/sources", json=_payload())
        assert created.status_code == 201
        source = created.json()
        version = source["versions"][0]
        assert version["state"] == "draft"

        premature = client.post(
            f"/api/runtime/knowledge/versions/{version['version_id']}/publish",
            json={"note": ""},
        )
        assert premature.status_code == 409

        reviewed = client.post(
            f"/api/runtime/knowledge/versions/{version['version_id']}/review",
            json={"note": "复核完成"},
        )
        assert reviewed.status_code == 200 and reviewed.json()["state"] == "reviewed"
        published = client.post(
            f"/api/runtime/knowledge/versions/{version['version_id']}/publish",
            json={"note": "目录发布"},
        )
        assert published.status_code == 200 and published.json()["state"] == "published"

        search = client.get("/api/runtime/knowledge/search", params={"q": "承运商"})
        assert search.status_code == 200
        assert len(search.json()["items"]) == 1
        projection = client.get(
            f"/api/runtime/knowledge/versions/{version['version_id']}/retrieval-projection"
        )
        assert projection.status_code == 200
        assert projection.json()["authority"]["eligible_for_runtime_evidence"] is False
        assert projection.json()["authority"]["changes_production_authority"] is False
        assert client.get("/api/runtime/knowledge/ui").status_code == 200


def test_runtime_namespace_denies_non_admin_and_tenants_are_isolated(monkeypatch, tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _identity(monkeypatch, tenant="tenant-a", user="admin-a", role="admin")
        source = client.post("/api/runtime/knowledge/sources", json=_payload()).json()

        _identity(monkeypatch, tenant="tenant-b", user="admin-b", role="admin")
        assert client.get(f"/api/runtime/knowledge/sources/{source['source_id']}").status_code == 404
        assert client.get("/api/runtime/knowledge").json()["count"] == 0

        for role in ("viewer", "operator", "approver"):
            _identity(monkeypatch, tenant="tenant-a", user=f"{role}-a", role=role)
            assert client.get("/api/runtime/knowledge").status_code == 403


def test_api_cannot_author_s1(monkeypatch, tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        _identity(monkeypatch, tenant="tenant-a", user="admin-a", role="admin")
        payload = _payload()
        payload["source_tier"] = "S1"
        response = client.post("/api/runtime/knowledge/sources", json=payload)
        # Literal schema rejects S1 before it can enter the store.
        assert response.status_code == 422
