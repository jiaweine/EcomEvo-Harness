from __future__ import annotations

from fastapi.testclient import TestClient

from ecomevo.api.app import app


def _identity(monkeypatch, *, tenant, user, role):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", tenant)
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", user)
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", role)


def test_operator_heartbeat_is_operator_only_and_server_clocked(monkeypatch):
    tenant = "operator-telemetry-route-a"
    with TestClient(app) as client:
        _identity(monkeypatch, tenant=tenant, user="operator-a1", role="operator")
        first = client.post(
            "/api/operator-activity/heartbeat",
            json={"surface": "workbench"},
        )
        assert first.status_code == 200
        assert first.json() == {
            "recorded": True,
            "bucket_seconds": 15,
            "client_duration_accepted": False,
            "changes_authority": False,
        }

        _identity(monkeypatch, tenant=tenant, user="operator-a2", role="operator")
        second = client.post(
            "/api/operator-activity/heartbeat",
            json={"surface": "workbench"},
        )
        assert second.status_code == 200

        _identity(monkeypatch, tenant=tenant, user="admin-a", role="admin")
        observed = client.get("/api/runtime/observability", params={"window": "24h"})
        assert observed.status_code == 200
        activity = observed.json()["operator_activity"]
        assert activity["active_seconds"] == 30
        assert activity["active_users"] == 2
        assert activity["window_fully_covered"] is False
        assert 0.0 < activity["window_coverage_rate"] < 1.0
        payload = observed.json()
        assert payload["telemetry_availability"]["operator_active_hours"]["complete"] is False
        assert payload["north_star"]["verified_decisions_per_operator_hour"]["available"] is False

        _identity(monkeypatch, tenant="operator-telemetry-route-b", user="operator-b", role="operator")
        assert client.post(
            "/api/operator-activity/heartbeat",
            json={"surface": "workbench"},
        ).status_code == 200

        _identity(monkeypatch, tenant="operator-telemetry-route-b", user="admin-b", role="admin")
        other = client.get("/api/runtime/observability", params={"window": "24h"}).json()
        assert other["operator_activity"]["active_seconds"] == 15
        assert other["operator_activity"]["active_users"] == 1

        _identity(monkeypatch, tenant=tenant, user="viewer-a", role="viewer")
        assert client.post(
            "/api/operator-activity/heartbeat",
            json={"surface": "workbench"},
        ).status_code == 403


def test_operator_heartbeat_rejects_client_duration_and_timestamp(monkeypatch):
    tenant = "operator-telemetry-extra-fields"
    with TestClient(app) as client:
        _identity(monkeypatch, tenant=tenant, user="operator-extra", role="operator")
        rejected = client.post(
            "/api/operator-activity/heartbeat",
            json={
                "surface": "workbench",
                "duration_seconds": 3600,
                "timestamp": 1,
            },
        )
        assert rejected.status_code == 422

        _identity(monkeypatch, tenant=tenant, user="admin-extra", role="admin")
        snapshot = client.get("/api/runtime/observability", params={"window": "24h"}).json()
        assert snapshot["operator_activity"]["active_seconds"] == 0
        assert snapshot["north_star"]["verified_decisions_per_operator_hour"]["available"] is False
