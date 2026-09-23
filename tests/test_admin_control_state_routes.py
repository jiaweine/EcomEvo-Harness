from __future__ import annotations

from fastapi.testclient import TestClient

from ecomevo.api.app import app


def _identity(monkeypatch, *, role="admin", user="admin-state-admin"):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", "tenant-admin-state")
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", user)
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", role)
    monkeypatch.setenv("ECOMEVO_DEPLOYMENT_NODES", "1")


def test_admin_control_state_manifest_route_is_admin_only(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch)
        response = client.get("/api/runtime/readiness/admin-control-state")
        assert response.status_code == 200
        body = response.json()
        assert body["scope"] == "deployment"
        assert body["manifest_status"] == "available_local_only"
        assert len(body["manifest_sha256"]) == 64
        assert body["database_count"] == 6
        assert body["unavailable_database_ids"] == []
        assert {row["filename"] for row in body["databases"]} == {
            "evaluation.db",
            "knowledge.db",
            "decision_exports.db",
            "release_readiness.db",
            "skill_studio.db",
            "connection_governance.db",
        }
        assert body["authority"]["read_only"] is True
        assert body["authority"]["changes_admin_state"] is False
        assert body["methodology"]["cross_database_atomic_snapshot"] is False
        assert body["methodology"]["shared_across_application_nodes"] is False
        assert body["methodology"]["cross_node_supported"] is False
        assert body["methodology"]["requirement_current_satisfied"] is False
        assert body["multi_node_ready"] is False

        _identity(monkeypatch, role="operator", user="admin-state-operator")
        assert client.get("/api/runtime/readiness/admin-control-state").status_code == 403

        _identity(monkeypatch, role="viewer", user="admin-state-viewer")
        assert client.get("/api/runtime/readiness/admin-control-state").status_code == 403
