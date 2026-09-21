from __future__ import annotations

from fastapi.testclient import TestClient

from ecomevo.api.app import app


def _identity(monkeypatch, *, tenant="routing-tenant-a", user="admin-a", role="admin"):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", tenant)
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", user)
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", role)


def test_routing_quality_is_admin_only_and_server_tenant_scoped(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch, tenant="routing-tenant-a", role="admin")
        response = client.get("/api/runtime/routing-quality", params={"window": "24h"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["tenant_scope"] == "routing-tenant-a"
        assert payload["authority"] == {
            "read_only": True,
            "changes_routing": False,
            "changes_policy": False,
            "changes_runtime_skills": False,
            "approves_business_actions": False,
            "executes_tools": False,
        }
        assert client.get("/api/runtime/routing-quality/ui").status_code == 200

        _identity(monkeypatch, tenant="routing-tenant-b", role="admin", user="admin-b")
        other = client.get("/api/runtime/routing-quality", params={"window": "24h"})
        assert other.status_code == 200
        assert other.json()["tenant_scope"] == "routing-tenant-b"

        _identity(monkeypatch, tenant="routing-tenant-a", role="operator", user="operator-a")
        assert client.get("/api/runtime/routing-quality").status_code == 403
        assert client.get("/api/runtime/routing-quality/ui").status_code == 403

        _identity(monkeypatch, tenant="routing-tenant-a", role="viewer", user="viewer-a")
        assert client.get("/api/runtime/routing-quality").status_code == 403


def test_routing_quality_window_validation(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch)
        response = client.get("/api/runtime/routing-quality", params={"window": "90d"})
        assert response.status_code == 422
