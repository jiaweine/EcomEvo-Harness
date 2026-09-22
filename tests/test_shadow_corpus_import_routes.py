from __future__ import annotations

from fastapi.testclient import TestClient

from ecomevo.api.app import app


def _identity(monkeypatch, *, tenant="shadow-import-a", user="admin-a", role="admin"):
    monkeypatch.setenv("ECOMEVO_AUTH_MODE", "local")
    monkeypatch.setenv("ECOMEVO_LOCAL_TENANT", tenant)
    monkeypatch.setenv("ECOMEVO_LOCAL_USER", user)
    monkeypatch.setenv("ECOMEVO_LOCAL_ROLE", role)


def _request() -> dict:
    return {
        "surface": "mcp",
        "operation": "governed_action",
        "mutation": "timeout_after_dispatch",
        "target": "refund.execute",
        "context_labels": ["aftersales", "refund"],
        "provenance": {
            "source_system": "mcp.gateway",
            "source_event_id": "evt-001",
            "observed_at": "2026-09-22T12:34:56+08:00",
            "source_record_sha256": "a" * 64,
            "redaction_profile": "ecomevo-shadow-v1",
            "redaction_attested": True,
        },
        "observation": {
            "phase": "post_dispatch",
            "status_code": 504,
            "error_code": "UPSTREAM_TIMEOUT",
            "error_class": "GatewayTimeout",
            "latency_ms": 8120,
        },
    }


def test_shadow_corpus_import_route_is_admin_only_tenant_scoped_and_deterministic(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch)
        request = _request()

        first = client.post("/api/runtime/shadow/import-fixture", json=request)
        second = client.post("/api/runtime/shadow/import-fixture", json=request)
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()
        body = first.json()
        assert body["tenant_scope"] == "shadow-import-a"
        assert body["expected_control"]["runtime_outcome"] == "uncertain"
        assert body["replay_fixture"]["persisted_by_importer"] is False
        assert body["replay_fixture"]["invokes_real_system"] is False
        assert body["replay_fixture"]["production_evidence"] is False
        assert body["provenance"]["source_digest_verified_by_shadow"] is False

        _identity(monkeypatch, tenant="shadow-import-b", user="admin-b")
        other = client.post("/api/runtime/shadow/import-fixture", json=request)
        assert other.status_code == 200
        assert other.json()["fixture_id"] != body["fixture_id"]

        _identity(monkeypatch, tenant="shadow-import-a", user="operator-a", role="operator")
        assert client.post("/api/runtime/shadow/import-fixture", json=request).status_code == 403


def test_shadow_corpus_import_rejects_raw_payload_headers_and_free_form_secret(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch)

        raw_payload = _request()
        raw_payload["raw_payload"] = {"authorization": "Bearer secret"}
        response = client.post("/api/runtime/shadow/import-fixture", json=raw_payload)
        assert response.status_code == 422

        nested_headers = _request()
        nested_headers["observation"]["headers"] = {"authorization": "Bearer secret"}
        response = client.post("/api/runtime/shadow/import-fixture", json=nested_headers)
        assert response.status_code == 422

        secret_like = _request()
        secret_like["observation"]["error_code"] = "Bearer secret-token"
        response = client.post("/api/runtime/shadow/import-fixture", json=secret_like)
        assert response.status_code == 422
        assert "bounded identifier" in response.json()["detail"]


def test_shadow_corpus_import_requires_timezone_and_attested_redaction(monkeypatch):
    with TestClient(app) as client:
        _identity(monkeypatch)

        naive_time = _request()
        naive_time["provenance"]["observed_at"] = "2026-09-22T12:34:56"
        response = client.post("/api/runtime/shadow/import-fixture", json=naive_time)
        assert response.status_code == 422
        assert "explicit timezone" in response.json()["detail"]

        unattested = _request()
        unattested["provenance"]["redaction_attested"] = False
        response = client.post("/api/runtime/shadow/import-fixture", json=unattested)
        assert response.status_code == 422
