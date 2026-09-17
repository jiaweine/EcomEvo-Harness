from __future__ import annotations

from fastapi.testclient import TestClient

from ecomevo.api.app import app


def _identity(monkeypatch, *, tenant="obs-tenant-a", user="admin-a", role="admin"):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", tenant)
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", user)
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", role)


def test_observability_is_admin_only_and_server_tenant_scoped(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch, tenant="obs-tenant-a", role="admin")
        response = client.get("/api/runtime/observability", params={"window": "24h"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["tenant_scope"] == "obs-tenant-a"
        assert payload["methodology"]["read_only"] is True
        assert payload["methodology"]["changes_authority"] is False
        assert payload["north_star"]["verified_decisions_per_operator_hour"]["available"] is False
        assert client.get("/api/runtime/observability/ui").status_code == 200

        _identity(monkeypatch, tenant="obs-tenant-b", role="admin", user="admin-b")
        other = client.get("/api/runtime/observability", params={"window": "24h"})
        assert other.status_code == 200
        assert other.json()["tenant_scope"] == "obs-tenant-b"

        _identity(monkeypatch, tenant="obs-tenant-a", role="operator", user="operator-a")
        assert client.get("/api/runtime/observability").status_code == 403
        assert client.get("/api/runtime/observability/ui").status_code == 403

        _identity(monkeypatch, tenant="obs-tenant-a", role="viewer", user="viewer-a")
        assert client.get("/api/runtime/observability").status_code == 403


def test_observability_window_validation(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch)
        response = client.get("/api/runtime/observability", params={"window": "90d"})
        assert response.status_code == 422
