from __future__ import annotations

from fastapi.testclient import TestClient

from ecomevo.api.app import app


def _identity(monkeypatch, *, tenant="tenant-ready-a", user="admin-ready", role="admin"):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", tenant)
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", user)
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", role)
    monkeypatch.setenv("ECOMEVO_DEPLOYMENT_NODES", "1")


def test_release_readiness_routes_are_admin_only(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch)
        preview = client.get("/api/runtime/readiness/preview", params={"window": "7d"})
        assert preview.status_code == 200
        body = preview.json()
        assert body["tenant_scope"] == "tenant-ready-a"
        assert body["authority"]["approved_for_release"] is False
        checks = {row["id"]: row for row in body["checks"]}
        assert checks["deployment_topology"]["status"] == "pass"
        assert body["sources"]["deployment_topology"]["declared_nodes"] == 1
        assert body["sources"]["deployment_topology"]["actual_replica_discovery"] is False
        migration = body["sources"]["multi_node_migration"]
        assert migration["ready"] is False
        assert migration["status"] == "not_ready_not_requested"
        assert migration["methodology"]["self_attested_backend_capabilities_accepted"] is False

        migration_route = client.get("/api/runtime/readiness/multi-node")
        assert migration_route.status_code == 200
        assert migration_route.json()["declared_nodes"] == 1
        assert migration_route.json()["ready"] is False
        assert migration_route.json()["authority"]["changes_storage_backend"] is False

        created = client.post("/api/runtime/readiness/snapshots", params={"window": "7d"})
        assert created.status_code == 201
        snapshot_id = created.json()["id"]

        listed = client.get("/api/runtime/readiness/snapshots")
        assert listed.status_code == 200
        assert any(row["id"] == snapshot_id for row in listed.json()["items"])

        fetched = client.get(f"/api/runtime/readiness/snapshots/{snapshot_id}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == snapshot_id

        assert client.get("/api/runtime/readiness/ui").status_code == 200
        assert client.get("/assets/release-readiness.js").status_code == 200
        assert client.get("/assets/release-readiness.css").status_code == 200

        _identity(monkeypatch, role="operator", user="operator-ready")
        assert client.get("/api/runtime/readiness/preview").status_code == 403
        assert client.get("/api/runtime/readiness/multi-node").status_code == 403
        assert client.post("/api/runtime/readiness/snapshots").status_code == 403

        _identity(monkeypatch, role="viewer", user="viewer-ready")
        assert client.get("/api/runtime/readiness/snapshots").status_code == 403


def test_release_readiness_snapshot_is_tenant_scoped(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch, tenant="tenant-ready-owner")
        created = client.post("/api/runtime/readiness/snapshots")
        assert created.status_code == 201
        snapshot_id = created.json()["id"]

        _identity(monkeypatch, tenant="tenant-ready-other", user="other-admin")
        assert client.get(f"/api/runtime/readiness/snapshots/{snapshot_id}").status_code == 404
        assert snapshot_id not in {
            row["id"] for row in client.get("/api/runtime/readiness/snapshots").json()["items"]
        }


def test_release_readiness_invalid_window_is_rejected(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch)
        assert client.get("/api/runtime/readiness/preview", params={"window": "90d"}).status_code == 422
