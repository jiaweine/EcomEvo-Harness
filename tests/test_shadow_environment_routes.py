from __future__ import annotations

from fastapi.testclient import TestClient

from ecomevo.api.app import app


def _identity(monkeypatch, *, tenant="shadow-tenant-a", user="admin-a", role="admin"):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", tenant)
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", user)
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", role)


def test_shadow_routes_are_admin_only_tenant_scoped_and_deterministic(monkeypatch):
    request = {
        "surface": "mcp",
        "operation": "governed_action",
        "mutation": "connection_reset_after_dispatch",
        "target": "refund.execute",
        "context_labels": ["aftersales", "refund"],
    }

    with TestClient(app) as client:
        _identity(monkeypatch, tenant="shadow-tenant-a", role="admin")
        catalog = client.get("/api/runtime/shadow/catalog")
        assert catalog.status_code == 200
        assert catalog.json()["tenant_scope"] == "shadow-tenant-a"
        assert catalog.json()["authority"]["executes_real_tools"] is False
        assert client.get("/api/runtime/shadow/ui").status_code == 200
        assert client.get("/assets/shadow-environment.js").status_code == 200
        assert client.get("/assets/shadow-environment.css").status_code == 200

        first = client.post("/api/runtime/shadow/simulate", json=request)
        second = client.post("/api/runtime/shadow/simulate", json=request)
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()
        assert first.json()["tenant_scope"] == "shadow-tenant-a"
        assert first.json()["expected_control"]["runtime_outcome"] == "uncertain"
        assert first.json()["replay_candidate"]["persisted_by_simulator"] is False

        _identity(monkeypatch, tenant="shadow-tenant-b", user="admin-b", role="admin")
        other = client.post("/api/runtime/shadow/simulate", json=request)
        assert other.status_code == 200
        assert other.json()["tenant_scope"] == "shadow-tenant-b"
        assert other.json()["candidate_id"] != first.json()["candidate_id"]

        _identity(monkeypatch, tenant="shadow-tenant-a", user="operator-a", role="operator")
        assert client.get("/api/runtime/shadow/catalog").status_code == 403
        assert client.get("/api/runtime/shadow/ui").status_code == 403
        assert client.post("/api/runtime/shadow/simulate", json=request).status_code == 403


def test_shadow_schema_mutation_validation(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch)
        response = client.post(
            "/api/runtime/shadow/simulate",
            json={
                "surface": "structured_data",
                "operation": "read",
                "mutation": "type_changed",
                "target": "orders.snapshot",
                "baseline_schema": {"type": "object"},
                "mutated_schema": {"type": "object"},
            },
        )
        assert response.status_code == 422
        assert "changed schema fingerprint" in response.json()["detail"]
