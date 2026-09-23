from __future__ import annotations

from fastapi.testclient import TestClient

from ecomevo.api.app import app


def _identity(monkeypatch, *, role="admin", user="authority-admin"):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", "tenant-authority")
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", user)
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", role)
    monkeypatch.setenv("ECOMEVO_DEPLOYMENT_NODES", "1")


def test_runtime_authority_snapshot_route_is_admin_only(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch)
        response = client.get("/api/runtime/readiness/runtime-authority")
        assert response.status_code == 200
        body = response.json()
        assert body["snapshot_status"] == "available"
        assert len(body["snapshot_sha256"]) == 64
        assert body["scope"] == "deployment"
        assert body["authority"]["read_only"] is True
        assert body["authority"]["changes_policy"] is False
        assert body["authority"]["changes_routing"] is False
        assert body["methodology"]["cross_node_shared"] is False
        assert body["methodology"]["cross_node_supported"] is False
        assert body["methodology"]["fingerprint_equality_proves_shared_transaction_domain"] is False
        assert set(body["surfaces"]) == {
            "policy_versions",
            "runtime_skills",
            "evolution_policy",
            "routing_policy",
            "routing_tool_stats",
            "harness_components",
        }

        _identity(monkeypatch, role="operator", user="authority-operator")
        assert client.get("/api/runtime/readiness/runtime-authority").status_code == 403

        _identity(monkeypatch, role="viewer", user="authority-viewer")
        assert client.get("/api/runtime/readiness/runtime-authority").status_code == 403
